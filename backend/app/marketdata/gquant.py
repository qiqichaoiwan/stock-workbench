"""东方财富掘金量化(GQuant) tick 到行情 Hub 的桥接器。

该模块只依赖 Python 标准库，``gm`` SDK 在真正运行掘金策略时才延迟导入。
它不会把免费掘金推送升级为交易所级 SLA：服务端仍由 ``MARKETDATA_AUTHORIZED_PROVIDERS``
决定事件是否能显示为 ``realtime``，未授权事件始终是 ``best_effort``。

用法(在 backend 目录执行)::

    GQUANT_INGEST_TOKEN='...' \\
    GQUANT_SYMBOLS='SHSE.600000,SZSE.000001' \\
    python -m app.marketdata.gquant

也可以在掘金策略的 ``on_tick`` 中直接创建 :class:`GQuantBridge` 并调用
``bridge.on_tick(data)``。桥接器只发送标准化字段，不记录 token 或完整响应。
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import json
import math
import logging
import os
import re
import threading
import time
from typing import Any, Callable
from numbers import Integral, Real
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("marketdata.gquant")
UTC = timezone.utc
CN = timezone(timedelta(hours=8))
PROVIDER = "gquant"
DEFAULT_BATCH_SIZE = 100
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_RETRY_TIMES = 3
DEFAULT_RETRY_WAIT_SECONDS = 0.25
DEFAULT_FLUSH_INTERVAL_SECONDS = 0.5
DEFAULT_MAX_PENDING = 1_000
_REJECTION_REASONS = {
    "code_invalid", "event_invalid", "number_invalid", "price_invalid", "provider_invalid",
    "provider_time_required", "provider_time_invalid", "provider_time_timezone_required",
    "provider_time_future", "provider_time_after_received", "provider_time_out_of_order",
    "received_at_required", "received_at_invalid", "received_at_timezone_required",
    "received_at_future", "sequence_invalid", "sequence_out_of_order", "session_invalid",
    "storage_unavailable",
}


class GQuantBridgeError(RuntimeError):
    """桥接器配置或 HTTP 接入失败。消息不包含 token。"""


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, Mapping):
            return converted
    # pandas DataFrame is intentionally handled without importing pandas.
    raise ValueError("tick_invalid")


def _first(data: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = data.get(name)
        if value is not None and value != "":
            return value
    return None


def normalize_code(value: Any) -> str:
    """把 GQuant 的市场前缀/裸代码转换为服务端六位代码。"""
    raw = str(value or "").strip().upper()
    if not raw:
        raise ValueError("code_required")
    # GQuant commonly uses SHSE.600000/SZSE.000001. Accept aliases emitted by
    # other SDK versions but only retain an exact six digit security code.
    if "." in raw:
        raw = raw.rsplit(".", 1)[-1]
    if raw.startswith(("SHSE", "SZSE")) and raw[4:].isdigit():
        raw = raw[4:]
    elif raw.startswith(("SH", "SZ")) and raw[2:].isdigit():
        raw = raw[2:]
    if not re.fullmatch(r"\d{6}", raw):
        raise ValueError("code_invalid")
    return raw


def _parse_time(value: Any) -> str:
    """返回带 UTC 偏移的 ISO 时间；掘金无偏移时间按北京时间解释。"""
    if value is None or value == "":
        raise ValueError("provider_time_required")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # GQuant variants may expose Unix seconds or milliseconds.
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("provider_time_invalid") from exc
        if not math.isfinite(number):
            raise ValueError("provider_time_invalid")
        if number > 10_000_000_000:
            number /= 1000
        try:
            stamp = datetime.fromtimestamp(number, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("provider_time_invalid") from exc
        return stamp.isoformat()
    if isinstance(value, datetime):
        stamp = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            # Some GQuant payloads use a space and no microseconds; strptime
            # keeps this adapter tolerant without accepting ambiguous numbers.
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    stamp = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    stamp = None
            if stamp is None:
                raise ValueError("provider_time_invalid")
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=CN)
    return stamp.astimezone(UTC).isoformat()


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError("price_invalid" if positive else "number_invalid")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("price_invalid" if positive else "number_invalid") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ValueError("price_invalid" if positive else "number_invalid")
    if positive and result <= 0:
        raise ValueError("price_invalid")
    return result


def _sequence(value: Any) -> int:
    """Validate a provider sequence without truncating fractional values."""
    if isinstance(value, bool):
        raise ValueError("sequence_invalid")
    if isinstance(value, Integral):
        result = int(value)
    elif isinstance(value, Real):
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError("sequence_invalid")
        result = int(number)
    elif isinstance(value, str):
        raw = value.strip()
        if not re.fullmatch(r"\d+", raw):
            raise ValueError("sequence_invalid")
        result = int(raw)
    else:
        raise ValueError("sequence_invalid")
    if result < 0:
        raise ValueError("sequence_invalid")
    return result


def normalize_tick(tick: Any, *, sequence: int | None = None,
                   received_at: datetime | None = None) -> dict[str, Any]:
    """将一个 GQuant ``on_tick`` 行转为 ``QuoteEventIn`` 兼容的事件。"""
    data = _as_mapping(tick)
    code = normalize_code(_first(data, "symbol", "code", "sec_id", "security"))
    price = _number(_first(data, "price", "last_price", "last", "close"), positive=True)
    pct_value = _first(data, "pct_chg", "change_rate", "change_percent")
    pct_chg = _number(pct_value) if pct_value is not None else None
    provider_time = _parse_time(_first(data, "created_at", "data_time", "time", "timestamp", "eob"))
    raw_sequence = _first(data, "sequence", "seq", "tick_id")
    if raw_sequence is not None:
        raw_sequence = _sequence(raw_sequence)
    elif sequence is not None:
        raw_sequence = _sequence(sequence)
    if received_at is None:
        received_at = datetime.now(UTC)
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        received_at = received_at.replace(tzinfo=CN)
    return {
        "code": code,
        "price": price,
        "pct_chg": pct_chg,
        "provider": PROVIDER,
        "provider_time": provider_time,
        "received_at": received_at.astimezone(UTC).isoformat(),
        "sequence": raw_sequence,
        "session": _first(data, "session"),
    }


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum and value == value else default


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _validate_ingest_url(url: str) -> None:
    """Require HTTPS for remote ingest endpoints.

    Plain HTTP remains useful for a local sidecar, but accepting it for a
    remote host would expose the bearer token and market events in transit.
    Operators can override this only with an explicit environment flag.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GQuantBridgeError("GQUANT_INGEST_URL 必须是带主机名的 http(s) URL")
    if parsed.scheme == "http":
        hostname = parsed.hostname.lower().rstrip(".")
        loopback = hostname in {"localhost", "127.0.0.1", "::1"}
        if not loopback and not _env_bool("GQUANT_ALLOW_INSECURE_HTTP"):
            raise GQuantBridgeError("远程 GQUANT_INGEST_URL 必须使用 HTTPS")


def _tick_rows(ticks: Any) -> list[Any]:
    """把 GQuant 回调的 mapping/list/DataFrame 形态统一成行列表。"""
    if isinstance(ticks, Mapping):
        return [ticks]
    if isinstance(ticks, (str, bytes)):
        raise GQuantBridgeError("tick_invalid")
    to_dict = getattr(ticks, "to_dict", None)
    if callable(to_dict):
        # pandas.DataFrame.to_dict("records") is the shape used by some SDK
        # versions; a single tick object normally returns a mapping.
        try:
            records = to_dict("records")
        except (TypeError, ValueError):
            records = to_dict()
        if isinstance(records, list):
            return records
        if isinstance(records, Mapping):
            return [records]
    try:
        return list(ticks)
    except TypeError as exc:
        raise GQuantBridgeError("tick_invalid") from exc


class GQuantBridge:
    """将 tick 批量推送到受保护的 ``/api/marketdata/ingest``。

    ``post_json`` 可注入测试实现或内部 sidecar；默认使用标准库 urllib，避免
    为免费研究桥接器额外安装客户端。每次重试只记录 HTTP 状态，不记录请求头。
    """

    def __init__(self, url: str | None = None, token: str | None = None, *,
                 batch_size: int | None = None, timeout_seconds: float | None = None,
                 retry_times: int | None = None, retry_wait_seconds: float | None = None,
                 flush_interval_seconds: float | None = None,
                 max_pending: int | None = None,
                 post_json: Callable[[str, str, dict[str, Any], float], dict[str, Any]] | None = None,
                 sleep_fn: Callable[[float], None] | None = None,
                 clock_fn: Callable[[], float] | None = None) -> None:
        self.url = (url or os.environ.get(
            "GQUANT_INGEST_URL", "http://127.0.0.1:8000/api/marketdata/ingest")).strip()
        self.token = token if token is not None else os.environ.get("GQUANT_INGEST_TOKEN", "")
        self.batch_size = batch_size or _env_int("GQUANT_BATCH_SIZE", DEFAULT_BATCH_SIZE)
        self.timeout_seconds = timeout_seconds or _env_float(
            "GQUANT_HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS, minimum=0.1)
        self.retry_times = retry_times if retry_times is not None else _env_int(
            "GQUANT_RETRY_TIMES", DEFAULT_RETRY_TIMES, minimum=0)
        self.retry_wait_seconds = retry_wait_seconds if retry_wait_seconds is not None else _env_float(
            "GQUANT_RETRY_WAIT_SECONDS", DEFAULT_RETRY_WAIT_SECONDS)
        self.flush_interval_seconds = flush_interval_seconds if flush_interval_seconds is not None else _env_float(
            "GQUANT_FLUSH_INTERVAL_SECONDS", DEFAULT_FLUSH_INTERVAL_SECONDS, minimum=0.001)
        self.max_pending = max_pending if max_pending is not None else _env_int(
            "GQUANT_MAX_PENDING", DEFAULT_MAX_PENDING)
        _validate_ingest_url(self.url)
        if not self.token.strip():
            raise GQuantBridgeError("GQUANT_INGEST_TOKEN 未配置")
        if self.batch_size < 1:
            raise GQuantBridgeError("GQUANT_BATCH_SIZE 必须大于 0")
        if self.max_pending < self.batch_size:
            raise GQuantBridgeError("GQUANT_MAX_PENDING 不能小于 GQUANT_BATCH_SIZE")
        self._post_json = post_json or self._default_post_json
        self._sleep = sleep_fn or time.sleep
        self._clock = clock_fn or time.monotonic
        self._pending: list[dict[str, Any]] = []
        self._pending_started_at: float | None = None
        self._flush_timer: threading.Timer | None = None
        self._timer_retry_attempt = 0
        self._lock = threading.RLock()
        # Serialize dequeue + HTTP send across the GQuant callback and the
        # timer callback. This preserves provider sequence order under load.
        self._dispatch_lock = threading.Lock()
        self._accepted_total = 0
        self._rejected_total = 0
        self._unconfirmed_total = 0
        self._rejection_totals: dict[str, int] = {}

    def health(self) -> dict[str, Any]:
        """Return process-local delivery counts without credentials or raw responses."""
        with self._lock:
            return {"accepted": self._accepted_total, "rejected": self._rejected_total,
                    "unconfirmed": self._unconfirmed_total,
                    "reject_reasons": dict(self._rejection_totals),
                    "pending": len(self._pending)}

    @staticmethod
    def _validated_results(response: Any, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Require an ordered acknowledgement for each submitted event."""
        if not isinstance(response, dict):
            raise GQuantBridgeError("行情接入响应缺少逐条确认")
        rows = response.get("results")
        if not isinstance(rows, list) or len(rows) != len(events):
            raise GQuantBridgeError("行情接入响应缺少逐条确认")
        for event, row in zip(events, rows):
            if not isinstance(row, dict) or type(row.get("accepted")) is not bool:
                raise GQuantBridgeError("行情接入逐条确认无效")
            if row["accepted"]:
                # The Hub includes the normalized code in every positive
                # acknowledgement.  Require it so a truncated or reordered
                # response cannot be mistaken for acceptance of this event.
                if row.get("code") != event["code"]:
                    raise GQuantBridgeError("行情接入逐条确认不匹配")
            elif not isinstance(row.get("reason"), str) or not row["reason"]:
                raise GQuantBridgeError("行情接入拒绝原因缺失")
        accepted = sum(row["accepted"] for row in rows)
        if (type(response.get("accepted")) is not int or
                type(response.get("rejected")) is not int or
                response["accepted"] != accepted or response["rejected"] != len(rows) - accepted):
            raise GQuantBridgeError("行情接入确认计数不一致")
        return rows

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {"sent": 0, "rejected": 0, "unconfirmed": 0,
                "reject_reasons": {}, "batches": 0, "responses": []}

    @staticmethod
    def _merge_result(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key in ("sent", "rejected", "unconfirmed", "batches"):
            target[key] += source[key]
        for reason, count in source["reject_reasons"].items():
            target["reject_reasons"][reason] = target["reject_reasons"].get(reason, 0) + count
        target["responses"].extend(source["responses"])

    def _deliver_batch(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Deliver one batch; only confirmed accepts count as sent.

        An individual storage outage is retried with a bounded budget. Invalid
        or duplicate events are never retried. An ambiguous network/response
        failure is reported as unconfirmed (the server may have committed it).
        Exhausted events are not silently requeued forever.
        """
        result = self._empty_result()
        result["batches"] = 1
        pending = events
        attempt = 0
        while pending:
            try:
                response = self._post_with_retry(pending)
                rows = self._validated_results(response, pending)
            except GQuantBridgeError:
                result["unconfirmed"] += len(pending)
                break
            result["responses"].append(response)
            retry = []
            for event, row in zip(pending, rows):
                if row["accepted"]:
                    result["sent"] += 1
                    continue
                reason = row["reason"] if row["reason"] in _REJECTION_REASONS else "other"
                if reason == "storage_unavailable" and attempt < self.retry_times:
                    retry.append(event)
                else:
                    result["rejected"] += 1
                    result["reject_reasons"][reason] = result["reject_reasons"].get(reason, 0) + 1
            pending = retry
            if pending:
                attempt += 1
                self._sleep(min(30.0, self.retry_wait_seconds * (2 ** (attempt - 1))))
        with self._lock:
            self._accepted_total += result["sent"]
            self._rejected_total += result["rejected"]
            self._unconfirmed_total += result["unconfirmed"]
            for reason, count in result["reject_reasons"].items():
                self._rejection_totals[reason] = self._rejection_totals.get(reason, 0) + count
        if result["rejected"] or result["unconfirmed"]:
            log.warning("行情接入未完全确认: accepted=%d rejected=%d unconfirmed=%d reasons=%s",
                        result["sent"], result["rejected"], result["unconfirmed"],
                        result["reject_reasons"])
        return result

    def _default_post_json(self, url: str, token: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Marketdata-Token": token,
        })
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 URL is operator configured
            raw = response.read().decode("utf-8")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GQuantBridgeError("行情接入响应不是 JSON") from exc
        return parsed if isinstance(parsed, dict) else {}

    def _post_with_retry(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"events": events}
        attempt = 0
        while True:
            try:
                return self._post_json(self.url, self.token, payload, self.timeout_seconds)
            except HTTPError as exc:
                status = int(exc.code)
                retryable = status == 408 or status == 429 or status >= 500
                if not retryable or attempt >= self.retry_times:
                    raise GQuantBridgeError(f"行情接入 HTTP {status}") from None
            except (URLError, TimeoutError, OSError, GQuantBridgeError) as exc:
                if attempt >= self.retry_times:
                    if isinstance(exc, GQuantBridgeError):
                        raise
                    raise GQuantBridgeError("行情接入网络失败") from None
            except Exception:
                # A user supplied transport (or a version-specific HTTP
                # client) may raise an exception outside urllib's hierarchy.
                # Treat it as an ambiguous delivery failure and keep the
                # bounded retry contract.  Never expose the exception text:
                # adapters sometimes include URLs, headers, or credentials.
                if attempt >= self.retry_times:
                    raise GQuantBridgeError("行情接入网络失败") from None
            attempt += 1
            self._sleep(min(30.0, self.retry_wait_seconds * (2 ** (attempt - 1))))

    def _schedule_flush_locked(self, delay: float | None = None) -> None:
        if not self._pending or self._flush_timer is not None:
            return
        wait = self.flush_interval_seconds if delay is None else max(0.001, float(delay))
        timer = threading.Timer(wait, self._timer_flush)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _cancel_flush_timer_locked(self) -> None:
        timer = self._flush_timer
        self._flush_timer = None
        if timer is not None:
            timer.cancel()

    def _timer_flush(self) -> None:
        # Timer callbacks use wall time; the injected clock remains the source
        # of truth for explicit ``flush_if_due`` calls and deterministic tests.
        with self._lock:
            self._flush_timer = None
        try:
            self.flush()
        except Exception as exc:  # noqa: BLE001 keep the bridge alive for retry
            # Never log exception text from an injected transport: it may carry
            # a URL or credential. The bridge itself exposes safe messages.
            log.warning("行情桥接定时刷新失败 (%s)", type(exc).__name__)
            with self._lock:
                # Do not hammer a down backend every 0.5s. The queue remains
                # intact and the next retry backs off to at most 30 seconds.
                self._timer_retry_attempt += 1
                if self._flush_timer is not None:
                    self._flush_timer.cancel()
                    self._flush_timer = None
                delay = min(30.0, max(self.flush_interval_seconds,
                                      self.retry_wait_seconds)
                            * (2 ** min(self._timer_retry_attempt - 1, 6)))
                self._schedule_flush_locked(delay)

    def _drain_full_batches(self, result: dict[str, Any]) -> None:
        """Send full batches under one cross-callback dispatch lock."""
        with self._dispatch_lock:
            while True:
                with self._lock:
                    if len(self._pending) < self.batch_size:
                        break
                    batch = self._pending[:self.batch_size]
                    self._pending = self._pending[self.batch_size:]
                try:
                    delivered = self._deliver_batch(batch)
                except Exception:
                    # Keep the failed batch ahead of newer events. This
                    # preserves provider sequence order when the next retry
                    # succeeds.
                    with self._lock:
                        self._pending = batch + self._pending
                        if self._pending_started_at is None:
                            self._pending_started_at = self._clock()
                        self._schedule_flush_locked()
                    raise
                self._merge_result(result, delivered)
                self._timer_retry_attempt = 0
            with self._lock:
                if self._pending:
                    if self._pending_started_at is None:
                        self._pending_started_at = self._clock()
                    self._schedule_flush_locked()
                else:
                    self._pending_started_at = None
                    self._cancel_flush_timer_locked()

    def flush(self) -> dict[str, Any]:
        with self._dispatch_lock:
            with self._lock:
                if not self._pending:
                    self._pending_started_at = None
                    self._timer_retry_attempt = 0
                    self._cancel_flush_timer_locked()
                    return self._empty_result()
                events = self._pending
                self._pending = []
                self._pending_started_at = None
                self._cancel_flush_timer_locked()
            result = self._empty_result()
            offset = 0
            while offset < len(events):
                batch = events[offset:offset + self.batch_size]
                try:
                    delivered = self._deliver_batch(batch)
                except Exception:
                    # Do not silently discard events if a later batch fails.
                    # Requeue the failed batch and unsent suffix; accepted
                    # batches stay out.
                    with self._lock:
                        self._pending = events[offset:] + self._pending
                        if self._pending_started_at is None:
                            self._pending_started_at = self._clock()
                        self._schedule_flush_locked()
                    raise
                self._merge_result(result, delivered)
                offset += len(batch)
            with self._lock:
                self._timer_retry_attempt = 0
                if self._pending:
                    if self._pending_started_at is None:
                        self._pending_started_at = self._clock()
                    self._schedule_flush_locked()
            return result

    def flush_if_due(self) -> dict[str, Any]:
        """按注入的 monotonic clock 刷新超过 ``flush_interval`` 的尾部。"""
        with self._lock:
            if not self._pending:
                return self._empty_result()
            started = self._pending_started_at
            if started is None:
                self._pending_started_at = self._clock()
                self._schedule_flush_locked()
                return self._empty_result()
            if self._clock() - started < self.flush_interval_seconds:
                self._schedule_flush_locked()
                return self._empty_result()
        return self.flush()

    def on_tick(self, ticks: Any) -> dict[str, Any]:
        """适配掘金 ``on_tick(data)``；支持单行、list 或可迭代对象。"""
        rows = _tick_rows(ticks)
        errors: list[str] = []
        converted = 0
        result = {"converted": converted, "errors": errors, **self._empty_result()}
        with self._lock:
            for tick in rows:
                try:
                    raw = _as_mapping(tick)
                    event = normalize_tick(raw)
                    if len(self._pending) >= self.max_pending:
                        self._schedule_flush_locked()
                        raise GQuantBridgeError("行情桥接队列已满")
                    if not self._pending:
                        self._pending_started_at = self._clock()
                    self._pending.append(event)
                    converted += 1
                except (TypeError, ValueError, OverflowError) as exc:
                    errors.append(str(exc) or "tick_invalid")
            result["converted"] = converted
            result["errors"] = errors
        self._drain_full_batches(result)
        with self._lock:
            due = bool(self._pending and self._pending_started_at is not None
                       and self._clock() - self._pending_started_at
                       >= self.flush_interval_seconds)
        if due:
            tail = self.flush()
            self._merge_result(result, tail)
        return result

    def close(self) -> dict[str, Any]:
        """发送不足一批的尾部事件，供策略结束或进程退出时调用。"""
        return self.flush()


def _configured_symbols() -> list[str]:
    raw = os.environ.get("GQUANT_SYMBOLS", os.environ.get("MARKETDATA_SYMBOLS", ""))
    return [item.strip() for item in raw.split(",") if item.strip()]


_RUNTIME_BRIDGE: GQuantBridge | None = None
_RUNTIME_SYMBOLS: list[str] = []


def _runtime_init(_context: Any) -> None:  # pragma: no cover - requires gm SDK
    from gm.api import subscribe
    subscribe(symbols=",".join(_RUNTIME_SYMBOLS), frequency="tick", count=1)


def _runtime_on_tick(_context: Any, tick: Any) -> None:  # pragma: no cover - requires gm SDK
    if _RUNTIME_BRIDGE is None:
        return
    try:
        _RUNTIME_BRIDGE.on_tick(tick)
    except GQuantBridgeError as exc:
        log.error("行情桥接失败: %s", exc)


def _runtime_on_error(_context: Any, code: Any, _info: Any) -> None:  # pragma: no cover
    log.error("GQuant 错误 code=%s", code)


# Names expected by the GQuant strategy runner. They delegate to the runtime
# helpers so importing this module remains safe when ``gm`` is unavailable.
def init(context: Any) -> None:  # pragma: no cover - requires gm SDK
    _runtime_init(context)


def on_tick(context: Any, tick: Any) -> None:  # pragma: no cover - requires gm SDK
    _runtime_on_tick(context, tick)


def on_error(context: Any, code: Any, info: Any) -> None:  # pragma: no cover
    _runtime_on_error(context, code, info)


def run_gquant() -> None:  # pragma: no cover - requires gm SDK and live account
    """作为独立掘金策略运行的入口；SDK 缺失时给出不含 token 的清晰错误。"""
    try:
        from gm.api import MODE_LIVE, run
    except ImportError as exc:
        raise SystemExit("未安装掘金 gm SDK；请在掘金终端策略环境中运行本模块") from exc
    global _RUNTIME_BRIDGE, _RUNTIME_SYMBOLS
    _RUNTIME_SYMBOLS = _configured_symbols()
    if not _RUNTIME_SYMBOLS:
        raise SystemExit("请配置 GQUANT_SYMBOLS，例如 SHSE.600000,SZSE.000001")
    _RUNTIME_BRIDGE = GQuantBridge()
    strategy_id = os.environ.get("GQUANT_STRATEGY_ID", "").strip()
    sdk_token = os.environ.get("GQUANT_TOKEN", "").strip()
    if not strategy_id or not sdk_token:
        raise SystemExit("请配置 GQUANT_STRATEGY_ID 和 GQUANT_TOKEN（掘金 SDK 凭证）")
    # GQuant discovers init/on_tick callbacks from this filename.  The SDK
    # token is distinct from GQUANT_INGEST_TOKEN used by this application.
    run(strategy_id=strategy_id, filename=__file__, mode=MODE_LIVE, token=sdk_token,
        serv_addr=os.environ.get("GQUANT_SERV_ADDR", ""))


if __name__ == "__main__":  # pragma: no cover
    run_gquant()
