"""候选域管理:核心指数成分 + 热门赛道,收窄筛选范围(约1000只)。

候选域 = 指数成分并集(沪深300/中证500/创业板50/科创50·100) ∪ 启用赛道成分
ST/停牌/次新/市值过滤在策略阶段做,这里只管"范围"。
"""
import logging
from datetime import datetime, timezone
from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..shared import data_gateway as dg
from .models import IndexMembership, StockBasic, Track, TrackMembership

log = logging.getLogger("universe")

# sync_track_members 落库映射:with_source 的 source → TrackMembership.source。
# 落库值:concept_board(东财)/concept_board_sina(新浪)/keyword(关键词)/
# concept_board_ths(同花顺核验)/concept_board_local(本地展开);
# local_ths_verified(同花顺核验+本地种子展开)落库复用 concept_board_ths。
TRACK_SOURCE_MAP = {"eastmoney": "concept_board", "sina": "concept_board_sina",
                    "keyword": "keyword", "ths": "concept_board_ths",
                    "local": "concept_board_local",
                    "local_ths_verified": "concept_board_ths"}

# 赛道代表股(库内代码,已验证有效):同一股票可属多赛道,重叠是刻意的。
TRACK_SEED_MAP: dict[str, list[str]] = {
    "固态电池": ["300750", "300014", "300207", "002074", "300037"],
    "半导体": ["688981", "603986", "603501", "688012", "688008", "300223"],
    "PCB": ["002463", "002916", "600183", "002384"],
    "光模块": ["300308", "300502", "300394", "002281"],
    "存储芯片": ["603986", "300223", "002463", "002384"],
    "电机": ["300124", "600580", "603728"],
    "AI算力": ["000977", "603019", "601138", "000938", "002475", "002050",
             "300037", "002635"],
}

# 赛道→行业名映射(StockBasic.industry 等值匹配用)。
TRACK_INDUSTRY_MAP: dict[str, list[str]] = {
    "固态电池": ["电池"],
    "半导体": ["半导体"],
    "PCB": ["元件"],
    "光模块": ["通信设备"],
    "存储芯片": ["半导体", "元件"],
    "电机": ["电机Ⅱ", "自动化设备"],
    "AI算力": ["计算机设备", "消费电子", "IT服务Ⅱ"],
}

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


def _mark_track_sync(db: Session, track: Track, *, source: str, error: str) -> None:
    """写赛道同步状态(成功 source/清空 error;失败记 error)。老库缺列只告警不迁移。"""
    try:
        track.member_source = source
        track.sync_error = error
        track.synced_at = datetime.now(timezone.utc)
        db.add(track)
        db.commit()
    except Exception as exc:  # noqa: BLE001 老库缺列(OperationalError)等只告警
        db.rollback()
        log.warning("track %s sync status write skipped (old DB?): %s", track.name, exc)


def _local_expand_track(db: Session, track: Track) -> tuple[pd.DataFrame, str]:
    """本地展开:seed 交集库内存在 + industry_index 等值 + keywords 逐词并集。

    seed 取 TRACK_SEED_MAP[track.name] 中库内存在的代码(名称取 StockBasic 名);
    industry 取 industry == track.industry_index;keywords 逗号切词逐词
    name/industry 包含(大小写不敏感)。三者并集,为空抛错(由 task 记 errors)。
    返回 (df, "local");df 列 code,name。"""
    rows = db.execute(select(StockBasic.code, StockBasic.name, StockBasic.industry)).all()
    by_code = {c: (n or "", ind or "") for c, n, ind in rows}
    keep: dict[str, str] = {}
    for code in TRACK_SEED_MAP.get(track.name or "", []):
        if code in by_code:
            keep[code] = by_code[code][0] or code
    industries = {getattr(track, "industry_index", "") or ""}
    industries |= set(TRACK_INDUSTRY_MAP.get(track.name or "", []))
    industries.discard("")
    terms = [w.strip() for w in str(getattr(track, "keywords", "") or "")
             .replace("，", ",").replace("、", ",").split(",") if w.strip()]
    for code, (name, ind) in by_code.items():
        if code in keep:
            continue
        if ind and ind in industries:
            keep[code] = name or code
            continue
        hay = f"{name} {ind}".lower()
        if any(t.lower() in hay for t in terms):
            keep[code] = name or code
    if not keep:
        raise RuntimeError(f"local expand matched nothing for {track.name!r}")
    df = pd.DataFrame([{"code": c, "name": n} for c, n in keep.items()])
    return df.drop_duplicates("code"), "local"


def sync_track_members(db: Session, track: Track) -> int:
    """四源降级拉取赛道成分(东财→同花顺名单核验→新浪名字匹配→关键词过滤→本地展开),只增不减。

    ths 源只做"名单存在性核验+占位":丢弃 THS: 占位行,按 TRACK_SEED_MAP 展开为
    真实成分(列 code,name 取 StockBasic 名),source 改写为 "local_ths_verified";
    无 concept_board 的 track 直接走本地展开(source "local");
    keyword 源返回时再用 track 参数重跑一遍本地展开做并集合并去重(source 仍记 keyword)。
    成功写 track.member_source/synced_at 并清空 sync_error;失败写 sync_error
    后 commit 再 re-raise(由 task 记 errors)。成分名称回填 StockBasic 保留。
    """
    from .models import StockBasic

    try:
        if not track.concept_board:
            df, source = _local_expand_track(db, track)
        else:
            try:
                df, source = dg.get_concept_board_members_with_source(
                    track.concept_board, track)
            except Exception:
                # with_source 四源皆失败 → 本地展开兜底(source "local")
                df, source = _local_expand_track(db, track)
            if source == "ths":
                # 丢弃 THS: 占位行,按种子码过滤库内存在代码展开
                df, _ = _local_expand_track(db, track)
                source = "local_ths_verified"
            elif source == "keyword":
                try:
                    local_df, _ = _local_expand_track(db, track)
                    df = pd.concat([df, local_df]).drop_duplicates("code")
                except Exception as exc:  # noqa: BLE001 本地展开失败不污染 keyword 结果
                    log.warning("track %s keyword+local union skipped: %s",
                                track.name, exc)
    except Exception as exc:
        _mark_track_sync(db, track, source="", error=str(exc)[:200])
        raise
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
    member_source = TRACK_SOURCE_MAP.get(source, "concept_board")
    for code in codes - known:
        db.add(TrackMembership(track_id=track.id, stock_code=code, source=member_source))
    db.commit()
    _mark_track_sync(db, track, source=source, error="")
    return len(codes - known)


def prune_track_members(db: Session, track: Track, incoming: set[str] | None = None) -> int:
    """清理赛道过期成分(TrackMembership 只增不减的补位)。

    默认按该赛道概念板块最新拉取结果做差集;显式传入 incoming 时按传入集合清理。
    只删自动同步来源的行(non-manual),manual 人工维护的行不受影响。返回删除数。
    调用方(sync_track 定时任务/API 手动清理)应在用户确认后调用,避免接口抖动误删。
    """
    if incoming is None:
        if not track.concept_board:
            return 0
        incoming = set(dg.get_concept_board_members(track.concept_board)["code"])
    stale = db.execute(select(TrackMembership).where(
        TrackMembership.track_id == track.id,
        TrackMembership.source != "manual",
        ~TrackMembership.stock_code.in_(sorted(incoming)) if incoming else True,
    )).scalars().all()
    n = len(stale)
    for m in stale:
        db.delete(m)
    if n:
        db.commit()
    return n


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
        {"name": "固态电池", "concept_board": "固态电池", "industry_index": "电池",
         "keywords": "固态电池,硫化物电解质"},
        {"name": "半导体", "concept_board": "中芯概念", "industry_index": "半导体",
         "keywords": "半导体,晶圆,光刻,国产替代"},
        {"name": "PCB", "concept_board": "PCB", "industry_index": "元件",
         "keywords": "PCB,印制电路板,覆铜板"},
        {"name": "光模块", "concept_board": "CPO概念", "industry_index": "通信设备",
         "keywords": "光模块,CPO,800G,1.6T"},
        {"name": "存储芯片", "concept_board": "存储芯片", "industry_index": "半导体",
         "keywords": "存储,内存,HBM"},
        {"name": "电机", "concept_board": "减速器", "industry_index": "自动化设备",
         "keywords": "电机,人形机器人,伺服"},
        {"name": "AI算力", "concept_board": "算力概念", "industry_index": "计算机设备",
         "keywords": "算力,AI服务器,英伟达"},
    ]


def ensure_default_tracks(db: Session) -> None:
    for t in default_tracks():
        exists = db.execute(select(Track).where(Track.name == t["name"])).scalar_one_or_none()
        if exists is None:
            db.add(Track(name=t["name"], concept_board=t["concept_board"],
                         industry_index=t.get("industry_index", ""),
                         keywords=t["keywords"], note="初始赛道"))
        elif not getattr(exists, "industry_index", ""):
            # 已存在的赛道若 industry_index 为空则回填;用户手工改过的非空值不碰
            exists.industry_index = t.get("industry_index", "")
            db.add(exists)
    db.commit()


def backfill_all_tracks(db: Session) -> dict:
    """一键回填全部赛道成分:ensure_default_tracks + 逐 track sync_track_members。

    返回 {track_name: {"added": n, "source": s}, "errors": [...]}
    (与 task_sync_tracks 风格一致,供 task_tracks_backfill/API 复用)。"""
    ensure_default_tracks(db)
    results: dict = {}
    errors: list[str] = []
    for t in list_tracks(db):
        try:
            n = sync_track_members(db, t)
            results[t.name] = {"added": n,
                               "source": getattr(t, "member_source", "") or ""}
        except Exception as exc:  # noqa: BLE001 单赛道失败不拖垮整体
            errors.append(f"{t.name}: {exc}")
    return {"results": results, "errors": errors}


def list_tracks(db: Session) -> list[Track]:
    return list(db.execute(select(Track).order_by(Track.id)).scalars())
