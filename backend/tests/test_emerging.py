"""新兴赛道雷达阶段判定测试(独立内存库)。"""
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import ScreenerBase
from app.screener import industry_monitor
from app.screener.models import IndustryDaily, NewsItem, Track

engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
ScreenerBase.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

TODAY = date(2026, 10, 23)


def _row(code, name, mom20, mom60, ratio_score=0.0, avoid=False):
    return IndustryDaily(
        industry_code=code, industry_name=name, trade_date=TODAY,
        close=100.0, pct_chg=1.0, amount=1e9, market_amount=1e12,
        ratio=0.1, ratio_score=ratio_score, mom_20=mom20, mom_60=mom60,
        avoid_until=TODAY + timedelta(days=10) if avoid else None,
    )


def test_emerging_stage_classification():
    db = Session()
    # 行业截面:A=低位转强(启动期) B=已大涨强动量(发酵期) C=拥挤回避 D=平淡观察
    db.add_all([
        _row("s1", "低位启动行业", mom20=3.0, mom60=-5.0),
        _row("s2", "高位强动量行业", mom20=25.0, mom60=40.0),
        _row("s3", "拥挤行业", mom20=20.0, mom60=50.0, ratio_score=450.0, avoid=True),
        _row("s4", "平淡行业", mom20=-0.5, mom60=10.0),
    ])
    # 新闻突增:启动行业近3日 6 条,前7日 2 条
    for i in range(6):
        db.add(NewsItem(title=f"低位启动行业利好 {i}", publish_time=datetime.combine(
            TODAY - timedelta(days=1), datetime.min.time()),
            related_industry="低位启动行业", screened=True))
    for i in range(2):
        db.add(NewsItem(title=f"低位启动行业旧闻 {i}", publish_time=datetime.combine(
            TODAY - timedelta(days=6), datetime.min.time()),
            related_industry="低位启动行业", screened=True))
    db.commit()

    out = industry_monitor.emerging_tracks(db, top=10)
    stages = {r["name"]: r["stage"] for r in out}
    assert stages["低位启动行业"] == "starting"
    assert stages["高位强动量行业"] == "rising"
    assert stages["拥挤行业"] == "avoid"
    assert stages["平淡行业"] == "watch"

    # 排序:启动期应排在发酵期/观察之前,回避垫底
    names = [r["name"] for r in out]
    assert names.index("低位启动行业") < names.index("高位强动量行业")
    assert names.index("低位启动行业") < names.index("平淡行业")
    assert names[-1] == "拥挤行业"

    # 新闻突增系数显著(6+1)/(2/7+1)≈3.2
    start = next(r for r in out if r["name"] == "低位启动行业")
    assert start["news_surge"] > 2.0
    assert start["tracked"] is False

    # 已建同名赛道 → tracked 标记
    db.add(Track(name="低位启动行业", keywords="低位启动行业"))
    db.commit()
    out2 = industry_monitor.emerging_tracks(db, top=10)
    start2 = next(r for r in out2 if r["name"] == "低位启动行业")
    assert start2["tracked"] is True
    db.close()
