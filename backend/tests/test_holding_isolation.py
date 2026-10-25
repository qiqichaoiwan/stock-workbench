"""持仓隔离回归测试(内存 sqlite,不碰生产库)。

覆盖:
1. 并发 sell 只成功一次:conditional update 序列化,第二个 sell 报"该持仓已卖出",
   且 TradeRecord 只有一条(holding_id 无唯一约束,防线在 claim 层,见 models 注释)。
2. G03 B->C 晋升:创新高后 trail(percent/ATR 取高) > 保本价 → 晋升 C 并抬止损;
   A 阶段禁止上移(未达标时 stop 保持初始价)。
3. 日报隔离:report_service._alerts(user_id) 只读、只返回该用户持仓的 danger/warning,
   不写库(不推进止损阶段);update_daily(user_id=...) 只扫该用户。
"""
import sys
import threading
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import PortfolioBase, ScreenerBase  # noqa: E402
from app.portfolio import holding_service as hs  # noqa: E402
from app.portfolio.models import Holding, TradeRecord, User  # noqa: E402
from app.portfolio.report_service import _alerts  # noqa: E402
from app.screener.models import DailyQuote  # noqa: E402
from app.shared.security import hash_password  # noqa: E402


def _fresh_db():
    """每用例独立临时文件库(不碰生产库);文件库供多线程共享,内存库多连接会分叉。"""
    import tempfile

    path = tempfile.mktemp(prefix="smhold_", suffix=".db")
    engine = create_engine(f"sqlite:///{path}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
    return sessionmaker(bind=engine)


BASE = date(2026, 10, 20)


def _seed_quotes(db, code, closes, start=BASE):
    for i, c in enumerate(closes):
        db.add(DailyQuote(code=code, trade_date=start + timedelta(days=i),
                          open=c, high=c + 2, low=c - 1, close=c))
    db.commit()


def _cutoff(db, day):
    """把 completed_trade_day 钉在指定日期,避免依赖真实交易日历/外部网络。"""
    p1 = patch.object(hs.stock_ref.dg, "completed_trade_day", return_value=day)
    p2 = patch.object(hs.stock_ref, "latest_quote_date", return_value=day)
    return p1, p2


def test_concurrent_sell_only_one_wins():
    Session = _fresh_db()
    db = Session()
    db.add(User(username="u1", password_hash=hash_password("x" * 12), nickname="u1"))
    db.commit()
    _seed_quotes(db, "600519", [100.0])
    h = hs.init_holding(db, 1, "600519", 100.0, 100, BASE)
    hid = h.id
    cutoff = BASE
    results = []

    def attempt():
        s = Session()
        try:
            with patch.object(hs.stock_ref.dg, "completed_trade_day", return_value=cutoff):
                rec = hs.sell(s, 1, hid, 101.0, cutoff)
            results.append(("ok", rec.id))
        except Exception as exc:  # noqa: BLE001
            results.append(("fail", str(exc)))
        finally:
            s.close()

    with patch.object(hs.stock_ref.dg, "completed_trade_day", return_value=cutoff):
        t1, t2 = threading.Thread(target=attempt), threading.Thread(target=attempt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    oks = [r for r in results if r[0] == "ok"]
    fails = [r for r in results if r[0] == "fail"]
    assert len(oks) == 1, results
    assert len(fails) == 1 and "已卖出" in fails[0][1], results
    db2 = Session()
    try:
        n = db2.query(TradeRecord).filter(TradeRecord.holding_id == hid).count()
        assert n == 1
    finally:
        db2.close()


def test_g03_b_to_c_promotion_and_a_locked():
    Session = _fresh_db()
    db = Session()
    # A 阶段:平淡行情(未达 1R/保本+3%),止损必须保持初始价不动
    _seed_quotes(db, "600519", [100.0, 101.0])
    h = hs.init_holding(db, 1, "600519", 100.0, 100, BASE)
    day_a = BASE + timedelta(days=1)
    p1, p2 = _cutoff(db, day_a)
    with p1, p2:
        hs.update_daily(db, user_id=1, trade_date=day_a)
    db.refresh(h)
    assert h.stop_stage == "A" and abs(h.stop_price - 90.0) < 1e-6

    # 大涨创新高:trail=percent/ATR 取高者 > 保本价 → B 直升 C 且止损抬到 trail
    _seed_quotes(db, "600519", [112.0, 125.0], start=BASE + timedelta(days=2))
    day_c = BASE + timedelta(days=3)
    p1, p2 = _cutoff(db, day_c)
    with p1, p2:
        hs.update_daily(db, user_id=1, trade_date=day_c)
    db.refresh(h)
    assert h.stop_stage == "C", (h.stop_stage, h.stop_price)
    assert h.stop_price > h.breakeven_price


def test_daily_report_isolation_readonly():
    Session = _fresh_db()
    db = Session()
    db.add_all([
        User(username="a", password_hash=hash_password("x" * 12), nickname="a"),
        User(username="b", password_hash=hash_password("x" * 12), nickname="b"),
    ])
    db.commit()
    # A 用户持仓已跌破止损(danger);B 用户持仓距止损 2%(warning)
    _seed_quotes(db, "600519", [80.0])
    _seed_quotes(db, "600520", [92.0])
    ha = hs.init_holding(db, 1, "600519", 100.0, 100, BASE)
    hb = hs.init_holding(db, 2, "600520", 100.0, 100, BASE)
    db.refresh(ha)
    db.refresh(hb)
    cutoff = BASE
    with patch.object(hs.stock_ref.dg, "completed_trade_day", return_value=cutoff):
        al_a = _alerts(db, 1)
        al_b = _alerts(db, 2)
    assert any(a["level"] == "danger" and a["code"] == "600519" for a in al_a)
    assert not any(a["code"] == "600520" for a in al_a)
    assert any(a["level"] == "warning" and a["code"] == "600520" for a in al_b)
    assert not any(a["code"] == "600519" for a in al_b)
    # 只读:日报不得推进止损阶段
    db.refresh(ha)
    assert ha.stop_stage == "A"
    # update_daily(user_id) 只扫该用户
    with patch.object(hs.stock_ref.dg, "completed_trade_day", return_value=cutoff):
        with patch.object(hs.stock_ref, "latest_quote_date", return_value=cutoff):
            hs.update_daily(db, user_id=1, trade_date=cutoff)
    db.refresh(hb)
    assert hb.stop_stage == "A" and hb.highest_price == 100.0
