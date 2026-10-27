"""首次初始化脚本:建表 → 种子策略卡/参数 → 管理员账户 → 可选数据回填。

用法:
  python scripts/init_data.py --admin-user admin --admin-password <密码>
  python scripts/init_data.py --admin-user admin --admin-password <密码> --backfill  # 连历史数据一起回填(首次约30-60分钟,增量补缺)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, create_all  # noqa: E402
from app.portfolio.models import User  # noqa: E402
from app.screener.strategy_engine import ensure_defaults  # noqa: E402
from app.shared.security import hash_password  # noqa: E402
from app.task_runner import run_task  # noqa: E402


def create_admin(db, username: str, password: str) -> User:
    """直接创建/修复管理员(注册接口只允许邀请码注册 user 角色)。"""
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user:
        user.role, user.is_active = "admin", True
        user.password_hash = hash_password(password)
    else:
        user = User(username=username, password_hash=hash_password(password),
                    nickname=username, role="admin")
        db.add(user)
    db.commit()
    db.refresh(user)
    return user


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--backfill", action="store_true", help="同步成分/基础信息并回填历史日线")
    parser.add_argument("--years", type=int, default=5)
    args = parser.parse_args()

    create_all()
    db = SessionLocal()
    try:
        ensure_defaults(db)
        print("[1/4] 策略卡与参数种子 OK")
        try:
            user = create_admin(db, args.admin_user, args.admin_password)
            print(f"[2/4] 管理员 {user.username} 创建成功(role={user.role})")
        except Exception as exc:  # noqa: BLE001
            print(f"[2/4] 管理员创建失败: {exc}")
        db.close()

        if args.backfill:
            print("[3/4] 同步候选域(指数成分+股票信息+赛道)…")
            for name in ("sync_members", "sync_basic", "sync_tracks"):
                try:
                    print(f"  {name}: {run_task(name)}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  {name} 失败(可稍后在任务中心手动重跑): {exc}")
            print(f"[4/4] 回填 {args.years} 年历史日线(耗时较长)…")
            try:
                print(f"  backfill_history: {run_task('backfill_history', years=args.years)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  backfill 失败: {exc}")
        else:
            print("[3/4] 跳过数据回填(可用 --backfill 或在任务中心手动触发)")
            print("[4/4] 完成")
    finally:
        db.close()


if __name__ == "__main__":
    main()
