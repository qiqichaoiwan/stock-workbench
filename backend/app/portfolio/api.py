"""Portfolio REST API:账户/用户管理/持仓/交易/报告/LLM设置(全部 user_id 隔离)。"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..db import get_db
from ..portfolio import account, ai_advisor, holding_service, report_service
from ..portfolio.models import (AiAnalysis, Holding, InviteCode, TradeRecord, User,
                                UserLlmConfig, WatchAlertLog, Watchlist, WeeklyReport)
from ..shared import llm_gateway
from ..shared.security import (current_user_id, decrypt_secret, encrypt_secret,
                               hash_password, mask_secret, require_admin, verify_password)

router = APIRouter(prefix="/api", tags=["portfolio"])


def _cn(dt):
    """UTC 落库字段序列化为北京时间(YYYY-MM-DD HH:MM:SS)。"""
    if not dt:
        return ""
    from datetime import timedelta, timezone

    return (dt.replace(tzinfo=timezone.utc) + timedelta(hours=8)).replace(
        tzinfo=None).isoformat(sep=" ")


def _me(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "用户不存在或已停用")
    return user


# ---------- 账户 ----------

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=64)
    nickname: str = ""
    invite_code: str = Field(default="", max_length=32)


@router.post("/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    user = account.register(db, body.username, body.password, body.nickname,
                            body.invite_code)
    return {"id": user.id, "username": user.username, "role": user.role}


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else ""
    return account.login(db, body.username, body.password, client_ip)


@router.get("/auth/me")
def me(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    user = _me(db, user_id)
    llm_cfg = db.execute(select(UserLlmConfig).where(
        UserLlmConfig.user_id == user_id)).scalar_one_or_none()
    return {"id": user.id, "username": user.username, "nickname": user.nickname,
            "role": user.role,
            "llm": {"configured": bool(llm_cfg and llm_cfg.enabled and llm_cfg.model),
                    "model": llm_cfg.model if llm_cfg else "",
                    "masked_key": llm_cfg.api_key_masked if llm_cfg else ""}}


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=64)


@router.patch("/auth/me")
def change_password(body: ChangePasswordIn, user_id: int = Depends(current_user_id),
                    db: Session = Depends(get_db)):
    user = _me(db, user_id)
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码错误")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


# ---------- 用户管理(admin) ----------

VALID_ROLES = ("admin", "analyst", "user")


class UserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=64)
    nickname: str = ""
    role: str = "user"


class UserPatchIn(BaseModel):
    nickname: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=64)


def _active_admin_count(db: Session) -> int:
    return db.execute(select(func.count(User.id)).where(
        User.role == "admin", User.is_active == True)).scalar() or 0  # noqa: E712


def _user_brief(u: User, holding_cnt: int = 0) -> dict:
    return {"id": u.id, "username": u.username, "nickname": u.nickname,
            "role": u.role, "is_active": u.is_active,
            "created_at": _cn(u.created_at),
            "holding_count": holding_cnt}


@router.get("/auth/users")
def list_users(keyword: str = "", page: int = 1, size: int = 20,
               admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    size = min(max(size, 1), 100)
    q = select(User).order_by(User.id)
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        q = q.where((User.username.like(like)) | (User.nickname.like(like)))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    rows = db.execute(q.offset((max(page, 1) - 1) * size).limit(size)).scalars().all()
    counts = dict(db.execute(
        select(Holding.user_id, func.count(Holding.id))
        .where(Holding.status == "holding").group_by(Holding.user_id)).all())
    return {"total": total, "items": [_user_brief(u, counts.get(u.id, 0)) for u in rows]}


@router.post("/auth/users")
def create_user(body: UserCreateIn, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    if body.role not in VALID_ROLES:
        raise HTTPException(400, "非法角色")
    exists = db.execute(select(User).where(
        User.username == body.username)).scalar_one_or_none()
    if exists:
        raise HTTPException(400, "用户名已存在")
    user = User(username=body.username, password_hash=hash_password(body.password),
                nickname=body.nickname or body.username, role=body.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_brief(user)


@router.patch("/auth/users/{user_id}")
def patch_user(user_id: int, body: UserPatchIn, admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(400, "非法角色")
        if user.role == "admin" and body.role != "admin" and _active_admin_count(db) <= 1:
            raise HTTPException(400, "系统至少需要保留一个启用的管理员")
        user.role = body.role
    if body.is_active is not None:
        if user.id == admin.id and not body.is_active:
            raise HTTPException(400, "不能停用自己")
        if (not body.is_active and user.role == "admin"
                and _active_admin_count(db) <= 1):
            raise HTTPException(400, "不能停用最后一个启用的管理员")
        user.is_active = body.is_active
    if body.nickname is not None:
        user.nickname = body.nickname
    db.commit()
    return _user_brief(user)


@router.post("/auth/users/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPasswordIn,
                   admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"ok": True}


@router.delete("/auth/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    if user.id == admin.id:
        raise HTTPException(400, "不能删除自己")
    if user.role == "admin" and _active_admin_count(db) <= 1:
        raise HTTPException(400, "不能删除最后一个启用的管理员")
    # 级联清理该租户的全部数据:子表先删、父表后删。
    # 用批量 DELETE 而非 db.delete(row):后者由 unit of work 统一 flush,
    # 语句顺序不保证,会先发 DELETE pf_user 触发 FOREIGN KEY constraint failed。
    for model in (TradeRecord, WatchAlertLog, AiAnalysis, WeeklyReport,
                  UserLlmConfig, Watchlist, Holding):
        db.execute(delete(model).where(model.user_id == user.id))
    # 邀请码无外键:清空 used_by 防孤儿引用,同时置 active=False 防被重新使用
    db.execute(update(InviteCode).where(InviteCode.used_by == user.id)
               .values(used_by=None, active=False))
    db.flush()
    db.delete(user)
    db.commit()
    return {"ok": True}


# ---------- 邀请码(admin) ----------

class InviteIn(BaseModel):
    note: str = ""
    expires_days: int = Field(default=7, ge=1, le=90)


@router.get("/auth/invites")
def list_invites(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(InviteCode).order_by(InviteCode.id.desc())
                      .limit(200)).scalars().all()
    names = dict(db.execute(select(User.id, User.username)).all())
    return [{"id": r.id, "code": r.code, "note": r.note, "active": r.active,
             "used": r.used_by is not None,
             "used_by": names.get(r.used_by, "") if r.used_by else "",
             "used_at": _cn(r.used_at),
             "expires_at": _cn(r.expires_at),
             "created_at": _cn(r.created_at)}
            for r in rows]


@router.post("/auth/invites")
def create_invite(body: InviteIn, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    inv = account.generate_invite(db, admin.id, body.note, body.expires_days)
    return {"id": inv.id, "code": inv.code}


@router.delete("/auth/invites/{invite_id}")
def revoke_invite(invite_id: int, admin: User = Depends(require_admin),
                  db: Session = Depends(get_db)):
    inv = db.get(InviteCode, invite_id)
    if inv is None:
        raise HTTPException(404, "邀请码不存在")
    inv.active = False
    db.commit()
    return {"ok": True}


# ---------- 持仓 ----------

class HoldingIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    buy_price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    buy_date: date
    stop_pct: float = Field(default=holding_service.INITIAL_STOP_PCT, gt=0, le=30)
    take_profit_price: float = 0.0
    note: str = ""


@router.get("/holdings")
def list_holdings(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    return holding_service.holdings_view(db, user_id)


@router.post("/holdings")
def create_holding(body: HoldingIn, user_id: int = Depends(current_user_id),
                   db: Session = Depends(get_db)):
    try:
        h = holding_service.init_holding(db, user_id, body.code, body.buy_price,
                                         body.quantity, body.buy_date, body.stop_pct,
                                         body.take_profit_price, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": h.id, "code": h.code, "breakeven_price": h.breakeven_price,
            "initial_stop_price": h.initial_stop_price, "risk_r": h.risk_r}


class SellIn(BaseModel):
    sell_price: float = Field(gt=0)
    sell_date: date | None = None
    exit_trigger: str = "manual"
    note: str = ""


@router.post("/holdings/{holding_id}/sell")
def sell_holding(holding_id: int, body: SellIn, user_id: int = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    try:
        rec = holding_service.sell(db, user_id, holding_id, body.sell_price,
                                   body.sell_date or date.today(),
                                   body.exit_trigger, body.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"id": rec.id, "pnl": rec.pnl, "pnl_pct": rec.pnl_pct,
            "exit_trigger": rec.exit_trigger}


@router.get("/trades")
def list_trades(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    return holding_service.trade_history(db, user_id)


@router.get("/trades/stats")
def trade_stats_api(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    return holding_service.trade_stats(db, user_id)


# ---------- 自购三价预警(P1-3:只读预警,不写止损;站内信先行) ----------

def _watch_code_check(code: str) -> str:
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(400, "code 须为 6 位数字")
    return code


def _watch_dict(w, db: Session) -> dict:
    from ..shared import stock_ref

    latest = stock_ref.latest_close(db, w.code)
    close = latest[1] if latest else None
    pct = 0.0
    if latest is not None:
        hist = stock_ref.quote_history(db, w.code, days=5)
        prev = hist[-2]["close"] if len(hist) >= 2 else None
        pct = round((close / prev - 1) * 100, 2) if prev else 0.0
    return {"id": w.id, "code": w.code, "name": w.name,
            "cost_price": w.cost_price, "take_profit": w.take_profit,
            "stop_loss": w.stop_loss, "alert_pct": w.alert_pct,
            "note": w.note, "close": close, "pct_chg": pct,
            "created_at": _cn(w.created_at)}


class WatchIn(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    name: str = ""
    cost_price: float = 0.0
    take_profit: float = 0.0
    stop_loss: float = 0.0
    alert_pct: float = 0.0
    note: str = ""


@router.get("/watchlist")
def list_watchlist(user_id: int = Depends(current_user_id),
                   db: Session = Depends(get_db)):
    """当前用户自购列表 + 现价/涨跌幅只读(via stock_ref.latest_close)。"""
    rows = db.execute(select(Watchlist).where(
        Watchlist.user_id == user_id).order_by(Watchlist.id.desc())).scalars().all()
    return [_watch_dict(w, db) for w in rows]


@router.post("/watchlist")
def create_watch(body: WatchIn, user_id: int = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    code = _watch_code_check(body.code)
    exists = db.execute(select(Watchlist).where(
        Watchlist.user_id == user_id, Watchlist.code == code)).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(400, "该股票已在自购列表")
    from ..shared import stock_ref

    if not stock_ref.stock_exists(db, code):
        raise HTTPException(400, f"股票代码 {code} 不存在")
    w = Watchlist(user_id=user_id, code=code,
                  name=body.name or stock_ref.stock_name(db, code),
                  cost_price=body.cost_price or 0.0,
                  take_profit=body.take_profit or 0.0,
                  stop_loss=body.stop_loss or 0.0,
                  alert_pct=body.alert_pct or 0.0, note=body.note or "")
    db.add(w)
    db.commit()
    db.refresh(w)
    return {"id": w.id, "code": w.code}


@router.put("/watchlist/{watch_id}")
def update_watch(watch_id: int, body: WatchIn,
                 user_id: int = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    w = db.get(Watchlist, watch_id)
    if w is None or w.user_id != user_id:
        raise HTTPException(404, "自购记录不存在")
    code = _watch_code_check(body.code)
    dup = db.execute(select(Watchlist).where(
        Watchlist.user_id == user_id, Watchlist.code == code,
        Watchlist.id != watch_id)).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(400, "该股票已在自购列表")
    from ..shared import stock_ref

    if not stock_ref.stock_exists(db, code):
        raise HTTPException(400, f"股票代码 {code} 不存在")
    w.code = code
    w.name = body.name or stock_ref.stock_name(db, code) or w.name
    w.cost_price = body.cost_price or 0.0
    w.take_profit = body.take_profit or 0.0
    w.stop_loss = body.stop_loss or 0.0
    w.alert_pct = body.alert_pct or 0.0
    w.note = body.note or ""
    db.commit()
    return {"ok": True}


@router.delete("/watchlist/{watch_id}")
def delete_watch(watch_id: int, user_id: int = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    w = db.get(Watchlist, watch_id)
    if w is None or w.user_id != user_id:
        raise HTTPException(404, "自购记录不存在")
    db.delete(w)
    db.commit()
    return {"ok": True}


@router.get("/watchlist/alerts")
def list_watch_alerts(limit: int = 50, user_id: int = Depends(current_user_id),
                      db: Session = Depends(get_db)):
    """当前用户预警记录(站内信先行,去重日志只读展示)。"""
    limit = min(max(limit, 1), 200)
    rows = db.execute(select(WatchAlertLog).where(
        WatchAlertLog.user_id == user_id).order_by(
        WatchAlertLog.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "code": r.code, "rule": r.rule, "message": r.message,
             "created_at": _cn(r.created_at)} for r in rows]


# ---------- AI 分析(参考层) ----------

@router.post("/holdings/{holding_id}/ai-analysis")
def ai_analysis(holding_id: int, user_id: int = Depends(current_user_id),
                db: Session = Depends(get_db)):
    h = db.get(Holding, holding_id)
    if h is None or h.user_id != user_id:
        raise HTTPException(404, "持仓不存在")
    return ai_advisor.analyze_stock(db, user_id, h.code, holding_id)


@router.get("/ai-analyses")
def list_analyses(user_id: int = Depends(current_user_id), db: Session = Depends(get_db),
                  limit: int = 50):
    rows = db.execute(select(AiAnalysis).where(AiAnalysis.user_id == user_id)
                      .order_by(AiAnalysis.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "code": r.code, "kind": r.kind, "content": r.content,
             "sell_points": r.sell_points, "model": r.model_name,
             "created_at": _cn(r.created_at)} for r in rows]


# ---------- 报告 ----------

@router.get("/reports/daily")
def daily_report(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    return {"content": report_service.daily_report(db, user_id)}


@router.get("/reports/weekly")
def weekly_list(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    return report_service.list_weekly_reports(db, user_id)


@router.post("/reports/weekly/generate")
def weekly_generate(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    rec = report_service.weekly_report(db, user_id)
    return {"id": rec.id, "title": rec.title}


@router.get("/reports/weekly/{report_id}")
def weekly_get(report_id: int, user_id: int = Depends(current_user_id),
               db: Session = Depends(get_db)):
    rec = report_service.get_weekly_report(db, user_id, report_id)
    if rec is None:
        raise HTTPException(404, "报告不存在")
    return {"id": rec.id, "title": rec.title, "content": rec.content,
            "week_start": rec.week_start.isoformat(), "week_end": rec.week_end.isoformat()}


# ---------- LLM 设置 ----------

class LlmIn(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    temperature: float = 0.3
    enabled: bool = True


@router.get("/settings/llm")
def get_llm(user_id: int = Depends(current_user_id), db: Session = Depends(get_db)):
    cfg = db.execute(select(UserLlmConfig).where(
        UserLlmConfig.user_id == user_id)).scalar_one_or_none()
    if cfg is None:
        return {"base_url": "", "model": "", "temperature": 0.3, "enabled": True,
                "masked_key": ""}
    return {"base_url": cfg.base_url, "model": cfg.model, "temperature": cfg.temperature,
            "enabled": cfg.enabled, "masked_key": cfg.api_key_masked}


@router.put("/settings/llm")
def save_llm(body: LlmIn, user_id: int = Depends(current_user_id),
             db: Session = Depends(get_db)):
    # 停用状态允许 URL/模型留空(前端"仅保存停用状态");启用时才要求必填。
    # 非空 URL 仍过网关守卫:SSRF/内网地址 400,允许本机 HTTP(vLLM/Ollama)。
    base_url = (body.base_url or "").strip()
    model = (body.model or "").strip()
    if body.enabled and not base_url:
        raise HTTPException(400, "启用状态下 Base URL 不能为空")
    if body.enabled and not model:
        raise HTTPException(400, "启用状态下模型不能为空")
    safe_url = ""
    if base_url:
        try:
            safe_url = llm_gateway._validate_base_url(base_url, allow_local=True)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    cfg = db.execute(select(UserLlmConfig).where(
        UserLlmConfig.user_id == user_id)).scalar_one_or_none()
    if cfg is None:
        cfg = UserLlmConfig(user_id=user_id, base_url="", model="")
        db.add(cfg)
    # 停用时留空表示"保持原配置不变",避免仅切开关就清掉已有 URL/模型
    if safe_url:
        cfg.base_url = safe_url
    if model:
        cfg.model = model
    cfg.temperature, cfg.enabled = body.temperature, body.enabled
    if body.api_key:  # 留空表示不修改密钥
        cfg.api_key_cipher = encrypt_secret(body.api_key)
        cfg.api_key_masked = mask_secret(body.api_key)
    db.commit()
    return {"ok": True, "masked_key": cfg.api_key_masked}


@router.post("/settings/llm/test")
def test_llm(body: LlmIn, user_id: int = Depends(current_user_id),
             db: Session = Depends(get_db)):
    # test_config 内部先做 _validate_base_url(SSRF 拒绝),再用短超时(8s)探测,
    # 避免探测挂起拖住请求线程;非法 URL 直接返回 ok=False,不抛 500。
    cfg = db.execute(select(UserLlmConfig).where(
        UserLlmConfig.user_id == user_id)).scalar_one_or_none()
    key = body.api_key or (decrypt_secret(cfg.api_key_cipher) if cfg else "")
    ok, msg = llm_gateway.test_config(body.base_url, key, body.model, allow_local=True)
    if cfg and ok:
        cfg.last_test_ok = True
        db.commit()
    return {"ok": ok, "message": msg}
