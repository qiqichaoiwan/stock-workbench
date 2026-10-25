"""账户服务:邀请码注册/登录(多租户基座)+ 登录限流。

角色体系:admin(用户管理+全部)/ analyst(选股工作台操作)/ user(只读看板+自己持仓)。
管理员账号由 scripts/create_admin.py 初始化,注册一律只能创建 user 角色。
"""
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..portfolio.models import InviteCode, User
from ..shared.security import create_access_token, hash_password, verify_password

log = logging.getLogger("account")

# ---------- 登录限流(内存滑窗,进程级;重启清零可接受) ----------

_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 8
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()


def _check_login_rate(client_ip: str) -> None:
    """只检查窗口,不计数:成功登录不应消耗失败配额(旧实现把成功也计入)。"""
    now = time.time()
    with _login_lock:
        q = _login_attempts[client_ip]
        while q and now - q[0] > _LOGIN_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "尝试过于频繁,请 1 分钟后再试")


def _record_login_fail(client_ip: str) -> None:
    """仅失败尝试计入滑动窗口。"""
    now = time.time()
    with _login_lock:
        q = _login_attempts[client_ip]
        while q and now - q[0] > _LOGIN_WINDOW_SECONDS:
            q.popleft()
        q.append(now)


# ---------- 注册(凭邀请码) ----------

def generate_invite(db: Session, created_by: int, note: str = "",
                    expires_days: int = 7) -> InviteCode:
    code = "INV-" + secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10].upper()
    inv = InviteCode(code=code, note=note, created_by=created_by,
                     expires_at=datetime.now(timezone.utc) + timedelta(days=expires_days))
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def register(db: Session, username: str, password: str, nickname: str = "",
             invite_code: str = "") -> User:
    """凭一次性邀请码注册;校验存在/未用/未过期/未作废,用后即焚。"""
    # 邀请码问题属请求参数校验失败,用 400 而非 403(403 表示已认证但无权访问)
    if not invite_code or not invite_code.strip():
        raise HTTPException(400, "注册需要邀请码,请联系管理员获取")
    inv = db.execute(select(InviteCode).where(
        InviteCode.code == invite_code.strip().upper())).scalar_one_or_none()
    if inv is None or not inv.active:
        raise HTTPException(400, "邀请码无效")
    if inv.used_by is not None:
        raise HTTPException(400, "邀请码已被使用")
    # SQLite DateTime 读回为 naive,统一按 UTC 处理再比较
    exp = inv.expires_at.replace(tzinfo=timezone.utc) if inv.expires_at else None
    if exp is not None and exp < datetime.now(timezone.utc):
        raise HTTPException(400, "邀请码已过期")

    exists = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "用户名已存在")
    user = User(username=username, password_hash=hash_password(password),
                nickname=nickname or username, role="user")
    db.add(user)
    db.flush()
    # 原子占用邀请码:同一事务内按 used_by IS NULL 条件更新,0 行则回滚新建用户
    claimed = db.execute(
        InviteCode.__table__.update()
        .where(InviteCode.code == inv.code, InviteCode.used_by.is_(None))
        .values(used_by=user.id, used_at=datetime.now(timezone.utc))
    ).rowcount
    if not claimed:
        db.rollback()
        raise HTTPException(400, "邀请码已被使用")
    db.commit()
    db.refresh(user)
    log.info("新用户注册: %s(邀请码 %s)", username, inv.code)
    return user


# ---------- 登录 ----------

def login(db: Session, username: str, password: str, client_ip: str = "") -> dict:
    client_ip = client_ip or "unknown"
    _check_login_rate(client_ip)
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        _record_login_fail(client_ip)
        raise HTTPException(401, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(403, "账号已停用,请联系管理员")
    return {"token": create_access_token(user.id, user.username, user.password_hash),
            "user": {"id": user.id, "username": user.username,
                     "nickname": user.nickname, "role": user.role}}


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)
