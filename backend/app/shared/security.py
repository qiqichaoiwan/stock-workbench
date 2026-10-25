"""安全基座:JWT、bcrypt 密码哈希、Fernet 密钥加密(用户 LLM api_key)。"""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..config import ROOT_DIR, settings
from ..db import get_db

_bearer = HTTPBearer(auto_error=False)


def _resolve_fernet_key() -> bytes:
    """优先取配置;否则生成并持久化到 data/.fernet.key,保证重启后仍可解密。"""
    if settings.fernet_key:
        return settings.fernet_key.encode()
    key_file = ROOT_DIR / "data" / ".fernet.key"
    if key_file.exists():
        return key_file.read_text().strip().encode()
    key = Fernet.generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(key.decode())
    return key


_fernet = Fernet(_resolve_fernet_key())


# ---------- 密码 ----------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode())
    except ValueError:
        return False


# ---------- JWT ----------

def credential_fingerprint(password_hash: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), password_hash.encode(), hashlib.sha256).hexdigest()


def create_access_token(user_id: int, username: str, password_hash: str) -> str:
    payload = {
        "credential": credential_fingerprint(password_hash),
        "sub": str(user_id),
        "username": username,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效凭证")


def current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> int:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    payload = decode_token(credentials.credentials)
    user_id = int(payload["sub"])
    from ..portfolio.models import User  # 局部导入避免模型加载顺序问题

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    if not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号已停用,请联系管理员")
    if payload.get("credential") != credential_fingerprint(user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "凭证已撤销,请重新登录")
    return user_id


def current_user(
    user_id: int = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> "User":
    """返回已验证且启用中的 User 实体(供角色判断)。"""
    from ..portfolio.models import User

    return db.get(User, user_id)


def require_admin(user: "User" = Depends(current_user)) -> "User":
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


def require_analyst(user: "User" = Depends(current_user)) -> "User":
    """选股工作台写操作:admin 或 analyst。"""
    if user.role not in ("admin", "analyst"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要策略员或管理员权限")
    return user


# ---------- API Key 加密存储 ----------

def encrypt_secret(plain: str) -> str:
    return _fernet.encrypt(plain.encode("utf-8")).decode()


def decrypt_secret(cipher: str) -> str:
    try:
        return _fernet.decrypt(cipher.encode("utf-8")).decode()
    except InvalidToken:
        return ""


def mask_secret(plain: str) -> str:
    if len(plain) <= 8:
        return "****"
    return plain[:4] + "****" + plain[-4:]
