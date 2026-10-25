"""个股拼音/首字母搜索索引(后端先行,无前端拼音依赖)。

build_search_index(db):用 StockBasic 全表构建 [{code,name,pinyin,initials}];
pypinyin 缺失或导入失败时降级为纯字符串匹配(pinyin/initials 置空,不抛错)。
get_cached_index(db):进程内缓存(TTL 10min + 表行数变化即失效),
避免每次 /search 请求全表重建拼音索引(~5000 股数百 ms CPU)。
"""
import logging
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger("search")

_INDEX_TTL_SECONDS = 600
_index_cache: dict = {"ts": 0.0, "count": -1, "index": []}
_index_lock = threading.Lock()
_pinyin_missing_warned = False


def _pinyin_of(name: str) -> tuple[str, str]:
    """返回 (pinyin 全拼连写小写, initials 首字母小写);pypinyin 不可用时 ("","")。"""
    global _pinyin_missing_warned
    try:
        from pypinyin import Style, lazy_pinyin
    except Exception as exc:  # noqa: BLE001 未安装/导入失败:降级纯字符串匹配
        # 只告警一次:缺失依赖会让拼音/首字母搜索整体失效,不能静默降级
        if not _pinyin_missing_warned:
            log.warning("pypinyin 不可用,/search 拼音检索降级为纯字符串匹配: %s", exc)
            _pinyin_missing_warned = True
        return "", ""
    try:
        pinyin = "".join(lazy_pinyin(name, style=Style.NORMAL)).lower()
        initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower()
        return pinyin, initials
    except Exception as exc:  # noqa: BLE001 生僻字等异常同样降级
        log.warning("pinyin failed for %r: %s", name, exc)
        return "", ""


def build_search_index(db: Session) -> list[dict]:
    """全表扫描 StockBasic(code/name),返回搜索索引列表。"""
    from ..screener.models import StockBasic

    rows = db.execute(select(StockBasic.code, StockBasic.name)).all()
    index = []
    for code, name in rows:
        name = str(name or "")
        pinyin, initials = _pinyin_of(name)
        index.append({"code": str(code), "name": name,
                      "pinyin": pinyin, "initials": initials})
    return index


def get_cached_index(db: Session) -> list[dict]:
    """带缓存的索引:TTL 10 分钟,或 StockBasic 行数变化即重建(线程安全)。

    /search 每请求直接复用;sync_track_members 等会补新 StockBasic 行,
    行数变化触发重建保证新股票可搜。"""
    from sqlalchemy import func

    from ..screener.models import StockBasic

    count = db.execute(select(func.count()).select_from(StockBasic)).scalar_one()
    now = time.monotonic()
    with _index_lock:
        if (_index_cache["index"]
                and _index_cache["count"] == count
                and now - _index_cache["ts"] < _INDEX_TTL_SECONDS):
            return _index_cache["index"]
    index = build_search_index(db)
    with _index_lock:
        _index_cache.update(ts=now, count=count, index=index)
    return index


def match_search(index: list[dict], q: str, limit: int = 20) -> list[dict]:
    """q 匹配 code 前缀/name 包含/pinyin 前缀/initials 前缀(大小写不敏感)。

    q 为空返回 [];返回 [{code,name}](截断 limit 条)。"""
    q = (q or "").strip().lower()
    if not q:
        return []
    out = []
    for it in index:
        if (it["code"].lower().startswith(q) or q in it["name"].lower()
                or (it["pinyin"] and it["pinyin"].startswith(q))
                or (it["initials"] and it["initials"].startswith(q))):
            out.append({"code": it["code"], "name": it["name"]})
            if len(out) >= limit:
                break
    return out
