"""有界的行情网关调用；超时线程在实际退出前持续占用容量。"""
import logging
import math
import threading

from . import data_gateway

log = logging.getLogger(__name__)
_SLOTS = threading.BoundedSemaphore(2)
_ALERT_SLOTS = threading.BoundedSemaphore(1)


def stream_quality_at_age(quality, age, realtime_max_age, delayed_max_age):
    """Age a Hub classification without ever upgrading its original quality.

    A response can spend seconds waiting on another quote source.  Reusing the
    Hub's original ``realtime`` flag after that wait would extend its validity
    beyond the configured window. Invalid metadata fails closed as stale.
    """
    try:
        age = float(age)
        realtime_max_age = float(realtime_max_age)
        delayed_max_age = float(delayed_max_age)
    except (TypeError, ValueError):
        return 'stale'
    if (not all(math.isfinite(value) for value in
                (age, realtime_max_age, delayed_max_age))
            or age < 0 or realtime_max_age <= 0 or delayed_max_age < realtime_max_age):
        return 'stale'
    if quality not in {'realtime', 'best_effort', 'delayed'} or age > delayed_max_age:
        return 'stale'
    if quality == 'delayed' or age > realtime_max_age:
        return 'delayed'
    return quality


def bounded_get_spot_quotes(codes, timeout=8, *, priority=False):
    """返回 (网关四元组或 None, 错误码或 None)。"""
    if not codes:
        return ({}, 'none', None, None), None
    slots = _ALERT_SLOTS if priority else _SLOTS
    if not slots.acquire(blocking=False):
        return None, 'quote_busy'
    box = {}

    def run():
        try:
            box['result'] = data_gateway.get_spot_quotes(codes)
        except Exception as exc:  # noqa: BLE001 统一安全降级
            log.warning('spot quotes failed: %s', exc)
            box['error'] = 'quote_failed'
        finally:
            slots.release()

    worker = threading.Thread(target=run, name='bounded-spot-quotes', daemon=True)
    try:
        worker.start()
    except Exception:
        slots.release()
        raise
    worker.join(timeout)
    if worker.is_alive():
        return None, 'quote_timeout'
    return box.get('result'), box.get('error')
