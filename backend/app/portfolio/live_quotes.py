"""用户股票行情:优先逐股报价，缺失时展示有日期的已落库收盘价。

采集时间不能代替行情时间。只有逐股来源时间有效且处于交易时段的报价
才允许用于盘中预警；快照源缺少行情时间时只作为未核验时点的参考。
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import settings
from ..marketdata import get_hub
from ..shared import stock_ref
from ..shared.quote_fetch import bounded_get_spot_quotes, stream_quality_at_age

CN = ZoneInfo('Asia/Shanghai')
WAIT_SECONDS = 8
MAX_AGE_SECONDS = 90


def now():
    return datetime.now(CN)


def _trading(at):
    """Return true only when the server calendar verifies an open session.

    Weekday/time arithmetic alone opens alerts on exchange holidays.  The
    cockpit calendar is singleflight cached, so this check does not perform a
    per-symbol network request; while it is loading or stale we fail closed.
    """
    try:
        from ..screener import cockpit

        if at.tzinfo is None:
            at = at.replace(tzinfo=CN)
        else:
            at = at.astimezone(CN)
        session = cockpit._session(at)
        return bool(session.get("calendar_verified") and session.get("state") == "trading")
    except Exception:  # noqa: BLE001 - alert quality must fail closed
        return False


def _age(at, current):
    try:
        value = datetime.fromisoformat(at)
        if value.tzinfo is None:
            return None
        seconds = (current - value.astimezone(CN)).total_seconds()
        return seconds if 0 <= seconds <= 86400 * 365 else None
    except (ValueError, TypeError):
        return None


def is_realtime(quote):
    current = now()
    age = _age(quote.get('quote_at'), current)
    # Stream quality has its own (usually much shorter) validity window.
    # Recheck it after batches complete; a quote can expire while waiting for
    # another batch even though it was realtime when the Hub returned it.
    limit = _positive(quote.get('realtime_max_age_seconds'))
    if limit is None:
        if quote.get('quote_kind') == 'stream':
            return False
        limit = MAX_AGE_SECONDS
    return bool(quote.get('realtime') and _trading(current)
                and age is not None and age <= limit)


def _positive(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (ValueError, TypeError):
        return None


def _finite(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def fetch(codes, *, priority=False):
    """带有界等待的行情调用；单条价格只使用自己的行情时间。"""
    codes = list(dict.fromkeys(codes))
    if not codes:
        return {}, []
    result = {}
    pending = list(codes)

    def finish(errors):
        current = now()
        for quote in result.values():
            if quote.get('quote_kind') != 'stream':
                continue
            age = _age(quote.get('quote_at'), current)
            quality = stream_quality_at_age(
                quote.get('quote_quality'), age,
                quote.get('realtime_max_age_seconds'), quote.get('delayed_max_age_seconds'))
            quote['age_seconds'] = age
            quote['quote_quality'] = quality
            quote['realtime'] = bool(quality == 'realtime' and _trading(current))
        return result, errors

    if settings.marketdata_stream_enabled:
        try:
            stream = get_hub().snapshot(codes)
            for code, quote in (stream.get('items') or {}).items():
                if quote.get('quality') not in {'realtime', 'best_effort', 'delayed'}:
                    continue
                price = _positive(quote.get('price'))
                if price is None:
                    continue
                result[code] = {
                    'price': price, 'pct_chg': _finite(quote.get('pct_chg')),
                    'quote_at': quote.get('provider_time'), 'captured_at': quote.get('received_at'),
                    'quote_source': quote.get('provider'), 'quote_kind': 'stream',
                    'quote_quality': quote.get('quality'), 'sequence': quote.get('sequence'),
                    'provider_sequence': quote.get('provider_sequence'),
                    'sequence_contiguous': quote.get('sequence_contiguous'),
                    'pct_chg_source': quote.get('pct_chg_source'),
                    'pct_chg_reference_at': quote.get('pct_chg_reference_at'),
                    'pct_chg_reference_price': quote.get('pct_chg_reference_price'),
                    'realtime': quote.get('quality') == 'realtime',
                    'realtime_max_age_seconds': stream.get('max_age_seconds'),
                    'delayed_max_age_seconds': stream.get('delayed_max_age_seconds'),
                    'age_seconds': quote.get('age_seconds'),
                }
            pending = [code for code in codes if code not in result]
        except Exception:  # noqa: BLE001 质量缓存故障继续走公共源
            pending = list(codes)
    if not pending:
        return finish([])
    data, error = bounded_get_spot_quotes(pending, timeout=WAIT_SECONDS, priority=priority)
    if error:
        messages = {'quote_timeout': '行情请求超时', 'quote_busy': '行情请求繁忙',
                    'quote_failed': '行情源不可用'}
        return finish([messages.get(error, '行情源不可用')])
    quotes, source, captured_at, _ = data
    current = now()
    for code in pending:
        quote = quotes.get(code) or {}
        price = _positive(quote.get('price'))
        if price is None:
            continue
        quote_at = quote.get('quote_at')
        age = _age(quote_at, current)
        fresh = _trading(current) and age is not None and age <= MAX_AGE_SECONDS
        result[code] = {
            'price': price, 'pct_chg': _finite(quote.get('pct_chg')),
            'quote_at': quote_at, 'captured_at': None,
            'quote_source': source, 'quote_kind': 'spot',
            'realtime': fresh, 'age_seconds': age,
            'realtime_max_age_seconds': MAX_AGE_SECONDS,
            'delayed_max_age_seconds': MAX_AGE_SECONDS,
        }
    return finish([])


def portfolio_quotes(db, codes):
    codes = list(dict.fromkeys(codes))
    items, errors = fetch(codes)
    for code in codes:
        if code in items:
            continue
        try:
            latest = stock_ref.latest_close(db, code)
        except Exception:  # noqa: BLE001
            # 交易日历不可用时不能猜测截止日；返回缺失并保留可诊断错误，不能让组合页 500。
            latest = None
            if "历史收盘不可用" not in errors:
                errors.append("历史收盘不可用")
        if latest and _positive(latest[1]) is not None:
            items[code] = {'price': latest[1], 'pct_chg': None,
                           'quote_at': latest[0].isoformat(), 'captured_at': None,
                           'quote_source': 'stored_daily', 'quote_kind': 'stored',
                           'realtime': False, 'age_seconds': None}
        else:
            items[code] = {'price': None, 'pct_chg': None, 'quote_at': None,
                           'captured_at': None, 'quote_source': None,
                           'quote_kind': 'missing', 'realtime': False,
                           'age_seconds': None}
    return {'as_of': now().isoformat(), 'items': items, 'errors': errors}
