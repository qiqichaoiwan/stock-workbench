"""候选域管理:核心指数成分 + 热门赛道,收窄筛选范围(约1000只)。

候选域 = 指数成分并集(沪深300/中证500/创业板50/科创50·100) ∪ 启用赛道成分
ST/停牌/次新/市值过滤在策略阶段做,这里只管"范围"。
"""
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared import data_gateway as dg
from .models import IndexMembership, StockBasic, Track, TrackMembership

log = logging.getLogger("universe")

CORE_INDEXES: list[str] = ["000300", "000905", "399673", "000688", "000698"]


def sync_index_members(db: Session, index_code: str) -> int:
    """同步一个指数的成分到候选域(新进插入,离场置 out_date)。"""
    members = dg.get_index_members(index_code)
    idx_name = dg.INDEX_NAMES.get(index_code, index_code)
    known = {c for (c,) in db.execute(
        select(IndexMembership.stock_code).where(
            IndexMembership.index_code == index_code,
            IndexMembership.out_date.is_(None),
        ))}
    incoming = set(members["code"])
    today_d = dg.today()

    for _, row in members.iterrows():
        code = row["code"]
        if code in known:
            continue
        db.add(IndexMembership(index_code=index_code, index_name=idx_name, stock_code=code,
                               stock_name=str(row.get("name", "")), in_date=today_d))

    for gone in known - incoming:
        for m in db.execute(select(IndexMembership).where(
                IndexMembership.index_code == index_code,
                IndexMembership.out_date.is_(None),
                IndexMembership.stock_code == gone)).scalars():
            m.out_date = today_d
    db.commit()
    log.info("index %s synced: %s members (%s new, %s out)",
             index_code, len(incoming), len(incoming - known), len(known - incoming))
    return len(incoming)


def sync_all_indexes(db: Session) -> dict[str, int]:
    result = {}
    for idx in CORE_INDEXES:
        try:
            result[idx] = sync_index_members(db, idx)
        except Exception as exc:  # noqa: BLE001 单个指数失败不影响其余
            log.error("sync index %s failed: %s", idx, exc)
            result[idx] = -1
    return result


def sync_stock_basic(db: Session) -> int:
    """全市场股票基础信息(含ST识别、板块、市值),供过滤与展示。"""
    df = dg.get_spot_snapshot()
    st_set = set(df.loc[df["name"].astype(str).str.contains("ST", na=False), "code"])
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "code": r["code"], "name": str(r.get("name", "")),
            "board": dg.classify_board(r["code"]), "is_st": r["code"] in st_set,
            "total_mv": float(r.get("total_mv") or 0), "circ_mv": float(r.get("circ_mv") or 0),
        })
    existing = {c: s for c, s in db.execute(select(StockBasic.code, StockBasic.status))}
    for r in rows:
        obj = db.get(StockBasic, r["code"])
        if obj is None:
            db.add(StockBasic(**r))
        else:
            obj.name, obj.board, obj.is_st = r["name"], r["board"], r["is_st"]
            obj.total_mv, obj.circ_mv = r["total_mv"], r["circ_mv"]
            if obj.status == "delisted" and r["code"] not in existing or True:
                obj.status = "active"
    db.commit()
    return len(rows)


def sync_track_members(db: Session, track: Track) -> int:
    """按东财概念板块名拉取赛道成分(接口被墙时抛错,由 API 层转友好提示)。"""
    if not track.concept_board:
        return 0
    from .models import StockBasic

    df = dg.get_concept_board_members(track.concept_board)
    # 顺手把成分名称补进基础表(技术信号等页面显示公司名)
    for _, r in df.iterrows():
        basic = db.get(StockBasic, r["code"])
        if basic is None:
            db.add(StockBasic(code=r["code"], name=r["name"]))
        elif not basic.name:
            basic.name = r["name"]
    codes = set(df["code"])
    known = {c for (c,) in db.execute(
        select(TrackMembership.stock_code).where(TrackMembership.track_id == track.id))}
    for code in codes - known:
        db.add(TrackMembership(track_id=track.id, stock_code=code, source="concept_board"))
    db.commit()
    return len(codes - known)


def candidate_universe(db: Session) -> list[str]:
    """当前候选域:在册指数成分 + 启用赛道成分(去重)。"""
    codes: set[str] = set()
    codes |= {c for (c,) in db.execute(select(IndexMembership.stock_code).where(
        IndexMembership.out_date.is_(None)))}
    track_ids = {t.id for t in db.execute(select(Track).where(Track.enabled == True)).scalars()}  # noqa: E712
    if track_ids:
        codes |= {c for (c,) in db.execute(select(TrackMembership.stock_code).where(
            TrackMembership.track_id.in_(track_ids)))}
    return sorted(codes)


def get_track_map(db: Session) -> dict[str, list[str]]:
    """code -> [track_name];供理由标签与行业池使用。"""
    rows = db.execute(
        select(TrackMembership.stock_code, Track.name)
        .join(Track, Track.id == TrackMembership.track_id)
        .where(Track.enabled == True)  # noqa: E712
    ).all()
    result: dict[str, list[str]] = {}
    for code, name in rows:
        result.setdefault(code, []).append(name)
    return result


def default_tracks() -> list[dict]:
    """会议纪要中的热门赛道初始集;concept_board 为东财板块名。"""
    return [
        {"name": "固态电池", "concept_board": "固态电池", "keywords": "固态电池,硫化物电解质"},
        {"name": "半导体", "concept_board": "中芯概念", "keywords": "半导体,晶圆,光刻,国产替代"},
        {"name": "PCB", "concept_board": "PCB", "keywords": "PCB,印制电路板,覆铜板"},
        {"name": "光模块", "concept_board": "CPO概念", "keywords": "光模块,CPO,800G,1.6T"},
        {"name": "存储芯片", "concept_board": "存储芯片", "keywords": "存储,内存,HBM"},
        {"name": "电机", "concept_board": "减速器", "keywords": "电机,人形机器人,伺服"},
        {"name": "AI算力", "concept_board": "算力概念", "keywords": "算力,AI服务器,英伟达"},
    ]


def ensure_default_tracks(db: Session) -> None:
    for t in default_tracks():
        exists = db.execute(select(Track).where(Track.name == t["name"])).scalar_one_or_none()
        if exists is None:
            db.add(Track(name=t["name"], concept_board=t["concept_board"],
                         keywords=t["keywords"], note="初始赛道"))
    db.commit()


def list_tracks(db: Session) -> list[Track]:
    return list(db.execute(select(Track).order_by(Track.id)).scalars())
