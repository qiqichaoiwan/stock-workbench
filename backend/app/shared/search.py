"""个股拼音/首字母搜索索引(后端先行,无前端拼音依赖)。

build_search_index(db):用 StockBasic 全表构建 [{code,name,pinyin,initials}];
pypinyin 缺失或导入失败时降级为纯字符串匹配(pinyin/initials 置空,不抛错)。
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger("search")


def _pinyin_of(name: str) -> tuple[str, str]:
    """返回 (pinyin 全拼连写小写, initials 首字母小写);pypinyin 不可用时 ("","")。"""
    try:
        from pypinyin import Style, lazy_pinyin
    except Exception:  # noqa: BLE001 未安装/导入失败:降级纯字符串匹配
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
