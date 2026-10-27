"""龙虎榜席位→游资匹配与展示数据规范化(纯函数,不碰网络/数据库)。"""
import json

from ..shared import data_gateway as dg

# 券商合并后的席位更名(2025 国泰君安吸收合并海通 → 国泰海通):
# 营业部名称不变,只换公司前缀;映射表仍按旧名维护,查找时做前缀归一。
_SEAT_ALIASES = (
    ("国泰海通证券股份有限公司", "国泰君安证券股份有限公司"),
)


def _clean(value) -> str:
    return str(value or "").strip()


def _lookup(seat: str, seats_map: dict) -> dict | None:
    hit = seats_map.get(seat)
    if hit is not None:
        return hit
    for old, new in _SEAT_ALIASES:
        if seat.startswith(old):
            hit = seats_map.get(new + seat[len(old):])
            if hit is not None:
                return hit
    return None


def _kind(seat: str, trader: str, kind=None) -> str:
    """席位类别;映射表显式 kind 优先,否则按游资/机构/北向推断。"""
    return _clean(kind) or dg.seat_kind(seat, trader)


def filter_hot_money(entries) -> list[dict]:
    """过滤掉机构/北向,只保留真正的游资标签。"""
    return [e for e in entries if e.get("kind", "游资") == "游资"]


def match_seats(buy_seats, sell_seats=None, seats_map: dict | None = None) -> list[dict]:
    """买方 + 卖方席位匹配游资,输出带方向和类别的命中列表。

    - side 标明"买入"/"卖出"(同一游资两边都上榜时各留一条,便于判断合力方向);
      同一方向的多个席位只保留首次命中,避免前端标签重复;
    - kind 标明游资/机构/北向,展示层用 filter_hot_money 过滤机构与北向;
    - 保留命中的席位全称,供前端 tooltip 展示;
    - 券商更名(国泰海通→国泰君安)按前缀归一后再查映射;
    - seats_map 缺省时读 data/hot_money_seats.json;映射缺失返回 []。
    """
    if seats_map is None:
        seats_map = dg.load_hot_money_seats()
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    sides = (("买入", buy_seats), ("卖出", sell_seats))
    for side, seats in sides:
        for seat in list(seats or []):
            name = _clean(seat)
            if not name:
                continue
            hit = _lookup(name, seats_map)
            if not hit:
                continue
            trader = _clean(hit.get("trader")) or name
            key = (trader, side)
            if key in seen:
                continue
            seen.add(key)
            out.append({"seat": name, "trader": trader, "side": side,
                        "kind": _kind(name, trader, hit.get("kind")),
                        "style": _clean(hit.get("style")),
                        "risk": _clean(hit.get("risk"))})
    return out


def normalize_matched(raw) -> list[dict]:
    """把库存 JSON 规范成前端直接可用的 [{seat,trader,side,kind,style,risk}]。

    兼容历史格式:字符串/对象混排、非法 JSON、同游资重复命中;历史数据
    没有 side/kind 字段时按名称推断,前端按"无方向/游资"降级展示。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except ValueError:
            raw = []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if isinstance(item, str):
            name = _clean(item)
            key = (name, "")
            if not name or key in seen:
                continue
            seen.add(key)
            out.append({"seat": name, "trader": name, "side": "",
                        "kind": _kind(name, name), "style": "", "risk": ""})
            continue
        if not isinstance(item, dict):
            continue
        seat, trader = _clean(item.get("seat")), _clean(item.get("trader"))
        side = _clean(item.get("side"))
        key = (trader or seat, side)
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append({"seat": seat, "trader": key[0], "side": side,
                    "kind": _kind(seat, key[0], item.get("kind")),
                    "style": _clean(item.get("style")),
                    "risk": _clean(item.get("risk"))})
    return out
