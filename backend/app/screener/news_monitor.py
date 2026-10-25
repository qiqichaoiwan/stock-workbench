"""新闻监测(低频甄别式)。

会议纪要共识:高频文本采集因成本过高被否决,单日突发消息多为噪声。
定位:低频采集(每日2次)+ 关键词规则打标 + 噪声过滤,服务「赛道确认与热度」,
不追求时效、不作入场依据。大V言论不采集。
"""
import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..shared import data_gateway as dg
from .models import NewsItem, Track
from .universe_manager import list_tracks

log = logging.getLogger("news_monitor")

# 重大利好/利空 规则(规则引擎一期,LLM 语义二期)
BULL_PATTERNS = [
    ("业绩预增", r"(预增|业绩预告.{0,6}(增|升)|净利润.{0,8}(增|升)\d+%)"),
    ("大额订单", r"(中标|签订.{0,8}(合同|协议)|订单.{0,6}(增长|饱满))"),
    ("回购增持", r"(回购|增持)"),
    ("涨价提价", r"(涨价|提价|上调.{0,4}价格|价格上调)"),
    ("政策利好", r"(政策|规划|补贴|税收优惠|支持.{0,4}发展)"),
    ("突破量产", r"(量产|交付.{0,4}(开始|启动)|投产|良率)"),
]
BEAR_PATTERNS = [
    ("业绩预减", r"(预减|预亏|业绩.{0,4}(下滑|亏损)|商誉减值)"),
    ("减持解禁", r"(减持|解禁|质押.{0,4}(风险|平仓))"),
    ("立案调查", r"(立案|调查|处罚|警示函|监管问询)"),
    ("涨价转跌", r"(降价|价格.{0,4}(下跌|战))"),
]
NOISE_PATTERNS = [
    (r"(涨停|打板|连板|龙头战法|妖股|庄家|大V|老师|带飞|上车)", "交易话术噪声"),
    (r"^\[.{0,4}\]$|^【快讯】$", "无实质内容"),
]
# 一日游行情甄别:同一主题当日新闻热度突然爆量但缺基本面词 → 降权
SUSTAIN_WORDS = r"(订单|业绩|产能|供需|涨价|扩产|签约|量产)"


def _match_patterns(text: str, patterns: list[tuple[str, str]]) -> list[str]:
    hits = []
    for label, pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            hits.append(label)
    return hits


def sync_news(db: Session, limit: int = 300) -> dict:
    """采集财联社电报 + 规则打标 + 噪声过滤,写入未入库的新条目。"""
    try:
        df = dg.get_cls_telegraph()
    except Exception as exc:  # noqa: BLE001
        log.error("cls telegraph failed: %s", exc)
        return {"fetched": 0, "stored": 0}

    tracks = list_tracks(db)
    track_keywords = {t.name: [k.strip() for k in (t.keywords or "").split(",") if k.strip()]
                      for t in tracks}
    cutoff = datetime.now(dg.TZ).replace(tzinfo=None) - timedelta(hours=24)
    recent_titles = {t for (t,) in db.execute(
        select(NewsItem.title).where(NewsItem.created_at >= datetime.utcnow() - timedelta(days=3)))}

    stored = 0
    for _, row in df.head(limit).iterrows():
        title, summary = str(row["title"]), str(row.get("summary") or "")[:500]
        if not title or title in recent_titles:
            continue
        text = title + " " + summary
        publish = row["publish_time"]
        if publish is not None and getattr(publish, "tzinfo", None) is not None:
            publish = publish.replace(tzinfo=None)
        if publish is not None and publish < cutoff:
            continue

        bull = _match_patterns(text, BULL_PATTERNS)
        bear = _match_patterns(text, BEAR_PATTERNS)
        noise = [label for pat, label in NOISE_PATTERNS if re.search(pat, text)]
        sentiment = "中性"
        if bear and not bull:
            sentiment = "利空"
        elif bull and not bear:
            sentiment = "利好"
        elif bull and bear:
            sentiment = "混合"

        related_track = ""
        for tname, kws in track_keywords.items():
            if any(kw in text for kw in kws) or tname in text:
                related_track = tname
                break

        # 热度与甄别:有赛道归属+基本面词 → 可信;纯交易话术 → 噪声
        has_sustain = bool(re.search(SUSTAIN_WORDS, text))
        screened = (not noise) and (bool(related_track) or has_sustain)
        heat = len(bull) + len(bear) + (2 if related_track else 0) + (1 if has_sustain else 0)
        if noise:
            heat -= 2

        db.add(NewsItem(
            source="cls", publish_time=publish, title=title, summary=summary,
            category=related_track or ("基本面" if has_sustain else "其他"),
            sentiment=sentiment, related_industry=related_track,
            related_codes=",".join(re.findall(r"\b(\d{6})\b", text))[:60],
            heat=float(max(heat, 0)), rule_hits=str({
                "bull": bull, "bear": bear, "noise": noise, "sustain": has_sustain,
            }).replace("'", '"'),
            screened=screened,
        ))
        stored += 1
    db.commit()
    log.info("news synced: fetched=%s stored=%s", len(df), stored)
    return {"fetched": int(len(df)), "stored": stored}


def track_news_heat(db: Session) -> dict[str, float]:
    """各赛道近7日甄别后新闻热度(供行业景气评分引用)。"""
    since = datetime.utcnow() - timedelta(days=7)
    rows = db.execute(select(NewsItem.related_industry, NewsItem.heat).where(
        NewsItem.screened == True,  # noqa: E712
        NewsItem.created_at >= since,
    )).all()
    heat: dict[str, float] = {}
    for industry, h in rows:
        if industry:
            heat[industry] = heat.get(industry, 0.0) + float(h or 0)
    return heat
