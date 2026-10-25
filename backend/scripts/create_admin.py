"""创建/修复管理员账户(注册接口仅支持邀请码注册 user 角色)。

用法:
  python scripts/create_admin.py --admin-user admin --admin-password <密码≥8位>
  python scripts/create_admin.py --admin-user admin --admin-password <新密码> --promote 用户名
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, create_all  # noqa: E402

from init_data import create_admin  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--admin-password", required=True)
    args = parser.parse_args()
    if len(args.admin_password) < 8:
        sys.exit("密码至少 8 位")
    create_all()
    db = SessionLocal()
    try:
        user = create_admin(db, args.admin_user, args.admin_password)
        print(f"管理员 {user.username} 就绪(role={user.role}, active={user.is_active})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
