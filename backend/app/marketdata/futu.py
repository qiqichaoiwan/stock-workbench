"""Optional Futu OpenD quote adapter.

The adapter is deliberately opt-in.  ``futu-api`` is not part of the base image
and the OpenD account's quote rights are external to this application.  When it
is enabled, callbacks are normalized into :mod:`app.marketdata.hub`; the hub
still refuses to call a source ``realtime`` unless the operator explicitly lists
``futu`` in ``MARKETDATA_AUTHORIZED_PROVIDERS``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
import re
import threading
import time
from typing import Any

from ..config import settings
from .hub import authorized_providers_from_env, get_hub

log = logging.getLogger("marketdata.futu")
CN = timezone(timedelta(hours=8))

try:  # Optional dependency; the base application must import without it.
    from futu import StockQuoteHandlerBase as _StockQuoteHandlerBase
    from futu import TickerHandlerBase as _TickerHandlerBase
except ImportError:  # pragma: no cover - exercised when optional SDK is absent
    class _StockQuoteHandlerBase:
        pass

    class _TickerHandlerBase:
        pass


def _symbol(code: str) -> str:
    code = str(code).strip().upper()
    if "." in code:
        market, bare = code.split(".", 1)
        if market in {"SH", "SZ"} and re.fullmatch(r"[0-9]{6}", bare):
            return f"{market}.{bare}"
        raise ValueError("MARKETDATA_SYMBOLS 标的格式无效")
    if not re.fullmatch(r"[0-9]{1,6}", code):
        raise ValueError("MARKETDATA_SYMBOLS 必须是最多六位 ASCII 数字代码")
    if code.startswith(("6", "68", "5")):
        return f"SH.{code.zfill(6)}"
    return f"SZ.{code.zfill(6)}"


def _provider_time(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z") or "+" in raw[10:] or "-" in raw[10:]:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return None
    # Futu documents A-share quote/ticker time as Beijing time.
    try:
        local = datetime.fromisoformat(raw.replace("/", "-"))
    except ValueError:
        try:
            local = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S:%f")
        except ValueError:
            try:
                local = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    # A-share provider time is Beijing time; write the explicit offset before
    # the hub normalizes it to UTC.  Never rely on the host timezone.
    return local.replace(tzinfo=CN).astimezone(timezone.utc).isoformat()


class FutuQuoteHandler(_StockQuoteHandlerBase):
    def __init__(self, owner: "FutuProvider") -> None:
        self.owner = owner

    def on_recv_rsp(self, rsp_pb):  # pragma: no cover - exercised with OpenD
        ret_code, data = super().on_recv_rsp(rsp_pb)
        try:
            from futu import RET_OK, RET_ERROR
        except ImportError:
            return ret_code, data
        if ret_code != RET_OK:
            self.owner._set_error(str(data))
            return RET_ERROR, data
        self.owner._received(data)
        for row in data.to_dict("records"):
            stamp = _provider_time(row.get("data_time"))
            if not stamp:
                continue
            self.owner.ingest_quote(row, stamp)
        return RET_OK, data


class FutuTickerHandler(_TickerHandlerBase):
    def __init__(self, owner: "FutuProvider") -> None:
        self.owner = owner

    def on_recv_rsp(self, rsp_pb):  # pragma: no cover - exercised with OpenD
        ret_code, data = super().on_recv_rsp(rsp_pb)
        try:
            from futu import RET_OK, RET_ERROR
        except ImportError:
            return ret_code, data
        if ret_code != RET_OK:
            self.owner._set_error(str(data))
            return RET_ERROR, data
        self.owner._received(data)
        for row in data.to_dict("records"):
            stamp = _provider_time(row.get("time"))
            if not stamp:
                continue
            self.owner.ingest_ticker(row, stamp)
        return RET_OK, data


class FutuProvider:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._reference_lock = threading.RLock()
        # A previous close is valid only on the same Shanghai date as the
        # QUOTE event that supplied it. Never carry it across a new session.
        self._previous_close: dict[str, tuple[str, float, str]] = {}
        self._state = "disabled"
        self._error: str | None = None
        self._reconnects = 0
        self._last_message_at: str | None = None
        self._last_provider_time: str | None = None
        self._connected_at_monotonic: float | None = None
        self._last_message_monotonic: float | None = None

    @property
    def heartbeat_timeout_seconds(self) -> float:
        """Maximum silent interval before OpenD is treated as unhealthy.

        A-share quote callbacks are event driven, so the timeout is deliberately
        longer than the delayed-quality window to avoid reconnecting on one quiet
        symbol while still surfacing a half-open socket during market hours.
        """
        try:
            delayed = float(settings.marketdata_delayed_max_age_seconds)
        except (TypeError, ValueError):
            delayed = 15.0
        return max(15.0, delayed * 2.0)

    @staticmethod
    def heartbeat_required(at: datetime | None = None) -> bool:
        """Whether a silent OpenD connection should be considered unhealthy.

        A-share quote streams are naturally quiet overnight, at lunch, and on
        weekends.  Reconnecting during those windows creates needless load and
        log noise.  Keep a small buffer around the auction/continuous sessions
        so a half-open connection is still detected promptly when the market is
        expected to publish data.
        """
        stamp = (at or datetime.now(CN)).astimezone(CN)
        if stamp.weekday() >= 5:
            return False
        minute = stamp.hour * 60 + stamp.minute
        return 555 <= minute <= 695 or 775 <= minute <= 905

    @property
    def symbols(self) -> list[str]:
        raw = [item.strip() for item in settings.marketdata_symbols.split(",") if item.strip()]
        symbols: list[str] = []
        for item in raw[:max(1, settings.futu_max_symbols)]:
            try:
                symbols.append(_symbol(item))
            except ValueError:
                # The worker validates and reports invalid tokens before start;
                # health/read-only callers should still remain observable.
                continue
        return symbols

    @property
    def invalid_symbols(self) -> list[str]:
        raw = [item.strip() for item in settings.marketdata_symbols.split(",") if item.strip()]
        invalid: list[str] = []
        # Validate the complete operator configuration.  ``symbols`` applies
        # FUTU_MAX_SYMBOLS for the subscription list, but an invalid token
        # beyond that cap should still fail fast instead of being hidden.
        for item in raw:
            try:
                _symbol(item)
            except ValueError:
                invalid.append(item)
        return invalid

    def start(self) -> None:
        if not settings.marketdata_stream_enabled or not settings.futu_enabled:
            self._state = "disabled"
            return
        if not settings.marketdata_single_tenant:
            self._state = "blocked_multi_tenant"
            self._error = "Futu 个人行情需要显式单租户配置"
            return
        invalid = self.invalid_symbols
        if invalid:
            self._state = "unconfigured"
            self._error = f"MARKETDATA_SYMBOLS 标的格式无效: {','.join(invalid[:5])}"
            return
        if not self.symbols:
            self._state = "unconfigured"
            self._error = "MARKETDATA_SYMBOLS 至少包含一个有效标的"
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="futu-marketdata", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._error = str(message)[:300]
            self._state = "error"

    def _received(self, _data: Any) -> None:
        with self._lock:
            self._last_message_at = datetime.now(timezone.utc).isoformat()
            self._last_message_monotonic = time.monotonic()
            self._state = "connected"
            self._error = None

    def ingest(self, event: dict[str, Any]) -> dict[str, Any]:
        event["provider"] = "futu"
        event["authorization"] = "futu" in authorized_providers_from_env()
        result = get_hub().ingest(event)
        if result.get("accepted"):
            with self._lock:
                self._last_provider_time = event.get("provider_time")
        return result

    @staticmethod
    def _positive(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    @staticmethod
    def _local_date(stamp: str) -> str:
        return datetime.fromisoformat(stamp).astimezone(CN).date().isoformat()

    @staticmethod
    def _add_change(event: dict[str, Any], previous_close: float, reference_at: str) -> None:
        price = FutuProvider._positive(event.get("price"))
        if price is None:
            return
        event["pct_chg"] = (price / previous_close - 1) * 100
        event["pct_chg_source"] = "futu_previous_close"
        event["pct_chg_reference_price"] = previous_close
        event["pct_chg_reference_at"] = reference_at

    def ingest_quote(self, row: dict[str, Any], stamp: str) -> dict[str, Any]:
        """Publish a Futu QUOTE and cache its same-day previous-close reference."""
        code = str(row.get("code", "")).split(".")[-1].zfill(6)
        previous_close = self._positive(row.get("prev_close_price"))
        event = {"code": code, "price": row.get("last_price"),
                 "provider_time": stamp, "sequence": None}
        with self._reference_lock:
            if previous_close is not None:
                self._add_change(event, previous_close, stamp)
            result = self.ingest(event)
            if result.get("accepted") and previous_close is not None:
                self._previous_close[code] = (self._local_date(stamp), previous_close, stamp)
            return result

    def ingest_ticker(self, row: dict[str, Any], stamp: str) -> dict[str, Any]:
        """Calculate tick change from an accepted same-day QUOTE reference."""
        code = str(row.get("code", "")).split(".")[-1].zfill(6)
        # Futu's TICKER ``sequence`` is an opaque provider identifier.  The
        # documented values are large and are not a contiguous +1 counter, so
        # keeping it in Hub's ``sequence`` field would manufacture sequence
        # gaps and downgrade every subsequent tick to delayed quality.  Keep a
        # string copy for audit/debugging while opting out of gap detection.
        provider_sequence = row.get("sequence")
        if provider_sequence is not None:
            provider_sequence = str(provider_sequence).strip() or None
        event = {"code": code, "price": row.get("price"),
                 "provider_time": stamp, "sequence": None,
                 "sequence_contiguous": False,
                 "provider_sequence": provider_sequence}
        with self._reference_lock:
            reference = self._previous_close.get(code)
            if reference and reference[0] == self._local_date(stamp):
                if datetime.fromisoformat(reference[2]) <= datetime.fromisoformat(stamp):
                    self._add_change(event, reference[1], reference[2])
            return self.ingest(event)

    def _run(self) -> None:  # pragma: no cover - requires OpenD process
        backoff = 1.0
        while not self._stop.is_set():
            ctx = None
            try:
                import futu

                with self._lock:
                    self._state = "connecting"
                    self._connected_at_monotonic = None
                    self._last_message_monotonic = None
                ctx = futu.OpenQuoteContext(host=settings.futu_opend_host,
                                            port=settings.futu_opend_port)
                # futu-api keeps one handler per protocol type in its
                # HandlerContext, so registering QUOTE and TICKER handlers is
                # supported; the later call does not replace the former.
                ctx.set_handler(FutuQuoteHandler(self))
                ctx.set_handler(FutuTickerHandler(self))
                ret, data = ctx.subscribe(
                    self.symbols,
                    [futu.SubType.QUOTE, futu.SubType.TICKER],
                    is_first_push=True, subscribe_push=True)
                if ret != futu.RET_OK:
                    raise RuntimeError(str(data))
                backoff = 1.0
                with self._lock:
                    self._state = "connected"
                    self._error = None
                    self._connected_at_monotonic = time.monotonic()
                    if self._last_message_monotonic is None:
                        self._last_message_monotonic = self._connected_at_monotonic
                while not self._stop.wait(1):
                    with self._lock:
                        anchor = (self._last_message_monotonic
                                  or self._connected_at_monotonic)
                    if (self.heartbeat_required() and anchor is not None and
                            time.monotonic() - anchor > self.heartbeat_timeout_seconds):
                        raise RuntimeError(
                            f"行情心跳超时(>{self.heartbeat_timeout_seconds:.0f}s)")
            except ImportError:
                self._set_error("futu-api 未安装；请安装 backend/requirements-futu.txt")
                return
            except Exception as exc:  # noqa: BLE001 reconnect is part of the adapter contract
                self._set_error(str(exc))
                with self._lock:
                    self._reconnects += 1
                self._stop.wait(backoff)
                backoff = min(float(settings.futu_reconnect_max_backoff), backoff * 2)
            finally:
                if ctx is not None:
                    try:
                        ctx.close()
                    except Exception:  # noqa: BLE001 close is best effort
                        pass

    def health(self) -> dict[str, Any]:
        with self._lock:
            anchor = self._last_message_monotonic or self._connected_at_monotonic
            age = (max(0.0, time.monotonic() - anchor)
                   if anchor is not None else None)
            state = self._state
            if (state == "connected" and self.heartbeat_required() and age is not None and
                    age > self.heartbeat_timeout_seconds):
                state = "stale"
            return {
                "provider": "futu",
                "enabled": bool(
                    settings.futu_enabled and settings.marketdata_stream_enabled and
                    settings.marketdata_process_role in {"worker", "embedded"}
                ),
                "single_tenant": settings.marketdata_single_tenant,
                "state": state,
                "symbols": self.symbols,
                "reconnects": self._reconnects,
                "last_message_at": self._last_message_at,
                "last_provider_time": self._last_provider_time,
                "message_age_seconds": round(age, 3) if age is not None else None,
                "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
                "error": self._error,
                "authorized": "futu" in authorized_providers_from_env(),
            }


_PROVIDER = FutuProvider()


def get_provider() -> FutuProvider:
    return _PROVIDER
