"""权限体系与邀请码注册的 API 级测试(独立临时库,不碰生产数据)。

覆盖:邀请码一次性、无码拒绝(400)、禁用用户 401、角色分级(user 403 / analyst 200)、
最后一个管理员保护、修改密码、用户管理仅 admin 可见。
"""
import os
import tempfile

_test_dir = tempfile.mkdtemp(prefix="smtest_")
os.environ["DATABASE_URL"] = f"sqlite:///{_test_dir}/auth_test.db"
os.environ["JWT_SECRET"] = "test-secret-for-auth"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, create_all  # noqa: E402
from app.main import app  # noqa: E402
from app.portfolio import account  # noqa: E402
from app.portfolio.models import User  # noqa: E402
from app.shared.security import hash_password  # noqa: E402

create_all()
client = TestClient(app)  # 不用 with,不触发 startup(避免起调度器)

USERS = {
    "boss": ("admin", "admin"),
    "tactician": ("analyst", "analyst"),
    "reader": ("user", "user"),
}


def _make_user(username: str, role: str) -> None:
    db = SessionLocal()
    try:
        if db.execute(User.__table__.select().where(User.username == username)).first():
            return
        db.add(User(username=username, password_hash=hash_password(f"pass-{username}-123"),
                    nickname=username, role=role))
        db.commit()
    finally:
        db.close()


for uname, (_key, role) in USERS.items():
    _make_user(uname, role)


def _login(username: str) -> str:
    r = client.post("/api/auth/login", json={"username": username,
                                             "password": f"pass-{username}-123"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _reset_rate_limit():
    with account._login_lock:
        account._login_attempts.clear()


def setup_function(_):
    _reset_rate_limit()


def test_register_without_invite_rejected():
    r = client.post("/api/auth/register",
                    json={"username": "nobody", "password": "whatever-123"})
    # 邀请码缺失属请求参数校验失败 → 400(403 表示已认证但无权访问)
    assert r.status_code == 400
    assert "邀请码" in r.json()["detail"]


def test_invite_one_time_use():
    token = _login("boss")
    r = client.post("/api/auth/invites", json={"note": "测试", "expires_days": 1},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    code = r.json()["code"]

    r = client.post("/api/auth/register",
                    json={"username": "newbie", "password": "newbie-pass-123",
                          "invite_code": code})
    assert r.status_code == 200 and r.json()["role"] == "user"

    # 复用同一邀请码 → 拒绝(参数校验失败 → 400)
    r = client.post("/api/auth/register",
                    json={"username": "newbie2", "password": "newbie-pass-123",
                          "invite_code": code})
    assert r.status_code == 400


def test_invalid_invite_rejected():
    r = client.post("/api/auth/register",
                    json={"username": "newbie3", "password": "newbie-pass-123",
                          "invite_code": "INV-NOTEXIST"})
    assert r.status_code == 400


def test_inactive_user_token_rejected():
    token = _login("reader")
    db = SessionLocal()
    try:
        u = db.execute(User.__table__.select().where(User.username == "reader")).first()
        db.execute(User.__table__.update().where(User.id == u.id).values(is_active=False))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    # 登录同样被拒
    r = client.post("/api/auth/login", json={"username": "reader", "password": "pass-reader-123"})
    assert r.status_code == 403
    db = SessionLocal()
    try:
        db.execute(User.__table__.update().where(User.username == "reader").values(is_active=True))
        db.commit()
    finally:
        db.close()


def test_role_gating_screener_writes():
    user_tok = _login("reader")
    analyst_tok = _login("tactician")
    headers_u = {"Authorization": f"Bearer {user_tok}"}
    headers_a = {"Authorization": f"Bearer {analyst_tok}"}

    # 读接口:user 也能看
    r = client.get("/api/screener/strategy-params", headers=headers_u)
    assert r.status_code == 200
    # 写接口:user 403
    r = client.put("/api/screener/strategy-params",
                   headers=headers_u, json={"params": {}})
    assert r.status_code == 403
    # 写接口:analyst 200
    r = client.put("/api/screener/strategy-params",
                   headers=headers_a, json={"params": {}})
    assert r.status_code == 200
    # 任务触发:user 403
    r = client.post("/api/screener/tasks/news/run", headers=headers_u)
    assert r.status_code == 403


def test_users_admin_only():
    analyst_tok = _login("tactician")
    r = client.get("/api/auth/users",
                   headers={"Authorization": f"Bearer {analyst_tok}"})
    assert r.status_code == 403
    admin_tok = _login("boss")
    r = client.get("/api/auth/users",
                   headers={"Authorization": f"Bearer {admin_tok}"})
    assert r.status_code == 200 and r.json()["total"] >= 3


def test_last_admin_protected():
    admin_tok = _login("boss")
    h = {"Authorization": f"Bearer {admin_tok}"}
    db = SessionLocal()
    try:
        boss = db.execute(User.__table__.select().where(User.username == "boss")).first()
        boss_id = boss.id
    finally:
        db.close()
    # 降级自己(唯一 admin)→ 400
    r = client.patch(f"/api/auth/users/{boss_id}", headers=h, json={"role": "user"})
    assert r.status_code == 400
    # 停用自己 → 400
    r = client.patch(f"/api/auth/users/{boss_id}", headers=h, json={"is_active": False})
    assert r.status_code == 400
    # 删除自己 → 400
    r = client.delete(f"/api/auth/users/{boss_id}", headers=h)
    assert r.status_code == 400


def test_change_password_flow():
    token = _login("tactician")
    h = {"Authorization": f"Bearer {token}"}
    r = client.patch("/api/auth/me", headers=h,
                     json={"old_password": "wrong-old-999", "new_password": "changed-pass-123"})
    assert r.status_code == 400
    r = client.patch("/api/auth/me", headers=h,
                     json={"old_password": "pass-tactician-123", "new_password": "changed-pass-123"})
    assert r.status_code == 200
    # 新密码可登录
    _reset_rate_limit()
    r = client.post("/api/auth/login",
                    json={"username": "tactician", "password": "changed-pass-123"})
    assert r.status_code == 200
    # 改回,便于其他用例
    db = SessionLocal()
    try:
        db.execute(User.__table__.update().where(User.username == "tactician")
                   .values(password_hash=hash_password("pass-tactician-123")))
        db.commit()
    finally:
        db.close()


def test_login_rate_limit():
    for _i in range(8):
        client.post("/api/auth/login", json={"username": "ghost", "password": "wrong-000000"})
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "wrong-000000"})
    assert r.status_code == 429
