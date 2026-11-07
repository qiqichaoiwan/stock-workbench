"""实时行情事件的质量门禁与单进程 latest 缓存。

这里刻意不把公开网页快照包装成交易所级行情。Hub 接收已经由供应商适配器
标准化的事件，负责校验时间、价格、序列和新鲜度，再把可诊断的状态提供给
REST API。当前实现使用进程内缓存，适合开发/试运行；生产需要把 latest、序列
和计数器默认保存在进程内；需要多 worker/多副本时可配置
``MARKETDATA_STORAGE_MODE=redis``，由 ``redis_store`` 提供共享 latest/Streams。
无论使用哪种存储，都必须让授权行情供应商负责数据源 SLA。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import math
import os
import re
import threading
from typing import Any, Callable, Iterable

UTC = timezone.utc
CODE_RE = re.compile(r"^\d{6}$")
DEFAULT_MAX_AGE_SECONDS = 3.0
DEFAULT_DELAYED_MAX_AGE_SECONDS = 15.0
DEFAULT_FUTURE_TOLERANCE_SECONDS = 2.0


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def _parse_time(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{field}_invalid") from exc
    else:
        raise ValueError(f"{field}_required")
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError(f"{field}_timezone_required")
    return stamp.astimezone(UTC)


def _number(value: Any, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("price_invalid" if positive else "number_invalid") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("price_invalid" if positive else "number_invalid")
    return number


def _provider(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 64 or not re.fullmatch(r"[A-Za-z0-9_.:/-]+", name):
        raise ValueError("provider_invalid")
    return name


def _compare_provider_sequence(left: str, right: str) -> int:
    """Compare opaque provider order IDs without turning them into gap seqs."""
    left = str(left)
    right = str(right)
    if left.isdigit() and right.isdigit():
        left = left.lstrip("0") or "0"
        right = right.lstrip("0") or "0"
        if len(left) != len(right):
            return -1 if len(left) < len(right) else 1
    if left == right:
        return 0
    return -1 if left < right else 1


def _session(value: Any, at: datetime) -> str:
    # A provider-reported session is diagnostic metadata only.  Derive the
    # gate from the timestamp received by the server so a bad/late provider
    # label cannot turn an outside-window event into an intraday event.
    if value is not None and str(value).strip():
        text = str(value).strip().lower()
        if text not in {"preopen", "auction", "trading", "lunch", "closed", "unknown"}:
            raise ValueError("session_invalid")
    local = at.astimezone(timezone(timedelta(hours=8)))
    if local.weekday() >= 5:
        return "closed"
    minute = local.hour * 60 + local.minute
    if minute < 570:
        return "preopen"
    # A-share continuous trading windows are half-open: 09:30-11:30 and
    # 13:00-15:00.  The boundary timestamps belong to lunch/closed.
    if minute < 690 or 780 <= minute < 900:
        return "trading"
    if minute < 780:
        return "lunch"
    return "closed"


def _authorized_provider(provider: str) -> bool:
    """Return configured authorization for a provider name.

    The ingest payload may carry ``authorization`` for diagnostics, but it cannot
    elevate a provider by itself. Production operators should list licensed
    providers in MARKETDATA_AUTHORIZED_PROVIDERS. A direct Hub caller can pass
    authorization explicitly for adapter tests.
    """
    raw = os.environ.get("MARKETDATA_AUTHORIZED_PROVIDERS", "")
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    return provider in allowed


class QuoteHub:
    """Thread-safe latest-value cache with event ordering and quality metrics."""

    # Publicly expose the storage mode as ``memory`` so the API contract stays
    # stable when Redis/Streams is added later. ``single_process`` is included
    # separately in health() to make the current SLA limitation explicit.
    mode = "memory"

    def __init__(
        self,
        *,
        now_fn: Callable[[], datetime] | None = None,
        max_age_seconds: float | None = None,
        delayed_max_age_seconds: float | None = None,
        future_tolerance_seconds: float | None = None,
        store: Any | None = None,
    ) -> None:
        try:
            from ..config import settings
            configured_max_age = settings.marketdata_max_age_seconds
            configured_delayed_age = settings.marketdata_delayed_max_age_seconds
            configured_future_tolerance = settings.marketdata_future_tolerance_seconds
        except Exception:  # pragma: no cover - standalone adapter import
            configured_max_age = DEFAULT_MAX_AGE_SECONDS
            configured_delayed_age = DEFAULT_DELAYED_MAX_AGE_SECONDS
            configured_future_tolerance = DEFAULT_FUTURE_TOLERANCE_SECONDS
        self._now_fn = now_fn or _now_utc
        self.max_age_seconds = max_age_seconds or _env_float(
            "MARKETDATA_MAX_AGE_SECONDS", configured_max_age)
        self.delayed_max_age_seconds = delayed_max_age_seconds or _env_float(
            "MARKETDATA_DELAYED_MAX_AGE_SECONDS", configured_delayed_age)
        self.future_tolerance_seconds = future_tolerance_seconds or _env_float(
            "MARKETDATA_FUTURE_TOLERANCE_SECONDS", configured_future_tolerance)
        if self.delayed_max_age_seconds < self.max_age_seconds:
            self.delayed_max_age_seconds = self.max_age_seconds
        self._store = store
        # Keep ``mode`` in the public response stable while allowing a
        # configured Redis store to advertise that latest/sequence state is
        # shared across workers.
        self.mode = getattr(store, "mode", "memory")
        self._lock = threading.RLock()
        self._latest: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_sequence: dict[tuple[str, str], int] = {}
        self._last_provider_time: dict[tuple[str, str], datetime] = {}
        # Futu TICKER exposes a monotonic opaque ID, but it is not a +1
        # sequence. Keep it only as an equal-timestamp tie-breaker so QUOTE and
        # multiple ticks sharing millisecond precision are not dropped.
        self._last_provider_sequence: dict[tuple[str, str], tuple[datetime, str]] = {}
        # A sequence gap is a quality latch.  Once a provider/code stream has
        # skipped an event, later ticks cannot prove that the missing event was
        # recovered merely because their own sequence is increasing.  The latch
        # clears only on an explicit resync/proof event or a full reset.
        self._gap_pending: dict[tuple[str, str], bool] = {}
        self._sequence_gaps = 0
        self._accepted = 0
        self._rejected = 0
        self._reject_reasons: Counter[str] = Counter()
        self._last_accepted_at: datetime | None = None
        self._providers: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"accepted": 0, "rejected": 0, "last_event_at": None})

    def _reject(self, provider: str | None, reason: str) -> dict[str, Any]:
        with self._lock:
            self._rejected += 1
            self._reject_reasons[reason] += 1
            if provider:
                self._providers[provider]["rejected"] += 1
        if self._store is not None:
            # Shared metrics are best effort and must never replace the
            # validation/storage error returned to the caller.
            try:
                self._store.record_rejection(reason)
            except Exception:  # pragma: no cover - defensive custom store
                pass
        return {"accepted": False, "reason": reason}

    def ingest(
        self,
        event: dict[str, Any] | Iterable[dict[str, Any]],
        *,
        authorization: bool | None = None,
        provider: str | None = None,
        authorized: bool | None = None,
    ) -> dict[str, Any]:
        """Validate and publish one normalized event.

        ``authorization`` is an adapter-side fact. Passing it is useful for unit
        tests and trusted in-process adapters; HTTP ingestion still resolves the
        final flag from MARKETDATA_AUTHORIZED_PROVIDERS.
        """
        if not isinstance(event, dict):
            return self.ingest_events(event, provider=provider,
                                      authorized=(authorized if authorized is not None
                                                  else authorization))
        event = dict(event)
        if provider is not None and not event.get("provider"):
            event["provider"] = provider
        if authorized is not None:
            authorization = authorized
        provider_hint = str(event.get("provider") or "").strip() or None
        try:
            code = str(event.get("code") or "").strip()
            if not CODE_RE.fullmatch(code):
                raise ValueError("code_invalid")
            provider = _provider(event.get("provider"))
            provider_time = _parse_time(event.get("provider_time"), "provider_time")
            received_raw = event.get("received_at")
            received_at = (_parse_time(received_raw, "received_at")
                           if received_raw is not None else self._now_fn().astimezone(UTC))
            price = _number(event.get("price"), positive=True)
            pct_chg = event.get("pct_chg")
            if pct_chg is not None:
                pct_chg = _number(pct_chg)
            pct_reference = None
            if event.get("pct_chg_source") is not None:
                if pct_chg is None:
                    raise ValueError("pct_chg_reference_invalid")
                ref_source = _provider(event.get("pct_chg_source"))
                ref_time = _parse_time(event.get("pct_chg_reference_at"), "pct_chg_reference_at")
                ref_price = _number(event.get("pct_chg_reference_price"), positive=True)
                if (ref_time > provider_time or
                    ref_time.astimezone(timezone(timedelta(hours=8))).date() !=
                    provider_time.astimezone(timezone(timedelta(hours=8))).date()):
                    raise ValueError("pct_chg_reference_invalid")
                pct_reference = {"pct_chg_source": ref_source,
                                 "pct_chg_reference_at": ref_time.isoformat(),
                                 "pct_chg_reference_price": ref_price}
            sequence = event.get("sequence")
            if sequence is not None:
                if isinstance(sequence, bool) or int(sequence) != sequence or int(sequence) < 0:
                    raise ValueError("sequence_invalid")
                sequence = int(sequence)
            # Some providers expose an opaque event identifier that is not a
            # contiguous counter (Futu TICKER is one example).  Preserve it
            # for audit/debugging, but never feed it into the gap detector.
            provider_sequence = event.get("provider_sequence")
            if provider_sequence is not None:
                provider_sequence = str(provider_sequence).strip()
                if not provider_sequence or len(provider_sequence) > 128:
                    raise ValueError("provider_sequence_invalid")
            sequence_contiguous = event.get("sequence_contiguous")
            if sequence_contiguous is not None and not isinstance(sequence_contiguous, bool):
                raise ValueError("sequence_contiguous_invalid")
            sequence_resync = event.get("sequence_resync")
            if sequence_resync is not None and not isinstance(sequence_resync, bool):
                raise ValueError("sequence_resync_invalid")
            session = _session(event.get("session"), provider_time)
            now = self._now_fn().astimezone(UTC)
            if provider_time > now + timedelta(seconds=self.future_tolerance_seconds):
                raise ValueError("provider_time_future")
            if received_at > now + timedelta(seconds=self.future_tolerance_seconds):
                raise ValueError("received_at_future")
            if provider_time > received_at + timedelta(seconds=self.future_tolerance_seconds):
                raise ValueError("provider_time_after_received")
        except (TypeError, ValueError, OverflowError) as exc:
            return self._reject(provider_hint, str(exc) or "event_invalid")

        key = (provider, code)
        with self._lock:
            last_sequence = self._last_sequence.get(key)
            last_time = self._last_provider_time.get(key)
            if sequence is not None and last_sequence is not None and sequence <= last_sequence:
                return self._reject(provider, "sequence_out_of_order")
            # Increasing sequence alone cannot justify replacing a newer
            # provider timestamp with an older quote.  Allow equal timestamps
            # only for distinct sequenced events (some feeds have second-level
            # timestamp precision); unsequenced updates must be strictly newer.
            if last_time is not None and (provider_time < last_time or
                    (sequence is None and provider_time == last_time and
                     provider_sequence is None)):
                return self._reject(provider, "provider_time_out_of_order")
            if (sequence is None and provider_time == last_time and
                    provider_sequence is not None):
                previous_opaque = self._last_provider_sequence.get(key)
                if (previous_opaque is not None and
                        previous_opaque[0] == provider_time and
                        _compare_provider_sequence(provider_sequence,
                                                   previous_opaque[1]) <= 0):
                    return self._reject(provider, "provider_time_out_of_order")
            observed_gap = bool(sequence is not None and last_sequence is not None
                                and sequence > last_sequence + 1)
            proof = bool(sequence_resync or sequence_contiguous is True)
            pending = self._gap_pending.get(key, False)
            if proof:
                pending = False
            elif observed_gap:
                pending = True
            sequence_gap = pending
            # Redis mode performs this check atomically in the shared store;
            # only memory mode owns the authoritative local counter.
            if observed_gap and self._store is None:
                self._sequence_gaps += 1
            auth = bool(authorization) if authorization is not None else bool(event.get("authorization"))
            item = {
                "code": code,
                "price": price,
                "pct_chg": pct_chg,
                "provider": provider,
                "authorization": auth,
                "provider_time": provider_time.isoformat(),
                "received_at": received_at.isoformat(),
                "sequence": sequence,
                "sequence_gap": sequence_gap,
                "session": session,
            }
            if provider_sequence is not None:
                item["provider_sequence"] = provider_sequence
            if sequence_contiguous is not None:
                item["sequence_contiguous"] = sequence_contiguous
            if sequence_resync is not None:
                item["sequence_resync"] = sequence_resync
            if pct_reference is not None:
                item.update(pct_reference)
            if self._store is not None:
                # The store repeats the ordering check atomically in Redis so
                # two API workers cannot accept the same or an older sequence.
                persisted = self._store.append(item)
                if not persisted.get("accepted"):
                    return self._reject(provider, str(persisted.get("reason") or
                                                       "storage_unavailable"))
                if "sequence_gap" in persisted:
                    sequence_gap = bool(persisted["sequence_gap"])
                    item["sequence_gap"] = sequence_gap
                    pending = sequence_gap
                    self._gap_pending[key] = pending
            self._latest[key] = item
            if self._store is None:
                self._gap_pending[key] = pending
            if sequence is not None:
                self._last_sequence[key] = sequence
            self._last_provider_time[key] = provider_time
            if provider_sequence is not None:
                self._last_provider_sequence[key] = (provider_time, provider_sequence)
            elif last_time is None or provider_time > last_time:
                # A new timestamp without an opaque order ID starts a fresh
                # tie-break window; do not compare it with a prior tick.
                self._last_provider_sequence.pop(key, None)
            self._accepted += 1
            self._last_accepted_at = now
            stats = self._providers[provider]
            stats["accepted"] += 1
            stats["last_event_at"] = now.isoformat()
        return {"accepted": True, "code": code, "provider": provider,
                "sequence": sequence}

    def ingest_many(
        self,
        events: Iterable[dict[str, Any]],
        *,
        authorization_providers: set[str] | None = None,
    ) -> dict[str, Any]:
        allowed = authorization_providers
        results = []
        for raw in events:
            provider = str(raw.get("provider") or "").strip()
            auth = provider in allowed if allowed is not None else None
            results.append(self.ingest(raw, authorization=auth))
        accepted = sum(1 for result in results if result.get("accepted"))
        return {"accepted": accepted, "rejected": len(results) - accepted,
                "results": results}

    def ingest_events(
        self,
        events: Iterable[dict[str, Any]],
        *,
        provider: str | None = None,
        authorized: bool | None = None,
    ) -> dict[str, Any]:
        """Compatibility entry point for provider adapters.

        An adapter may pass provider/authorized once for a batch instead of
        repeating them in every event. HTTP ingestion uses ingest_many() so its
        authorization is still resolved from the server-side allow-list.
        """
        prepared = []
        for raw in events:
            item = dict(raw)
            if provider is not None and not item.get("provider"):
                item["provider"] = provider
            if authorized is not None:
                item["authorization"] = authorized
            prepared.append(item)
        return self.ingest_many(prepared)

    def _status(self, item: dict[str, Any], now: datetime) -> tuple[str, float | None]:
        provider_time = datetime.fromisoformat(item["provider_time"])
        age = max(0.0, (now - provider_time).total_seconds())
        if age > self.delayed_max_age_seconds:
            return "stale", age
        if item.get("sequence_gap"):
            return "delayed", age
        # A fresh tick outside an exchange trading window is still useful as
        # a last-session snapshot, but it must not appear as live data to the
        # health/latest consumers.  The session was derived from the server
        # timestamp at ingest, so provider labels cannot bypass this gate.
        current_session = _session(None, now)
        if item.get("session") != "trading" or current_session != "trading":
            return "delayed", age
        if age <= self.max_age_seconds:
            return ("realtime" if item["authorization"] else "best_effort"), age
        if age <= self.delayed_max_age_seconds:
            return "delayed", age
        return "stale", age

    @staticmethod
    def _quality_rank(quality: str) -> int:
        # Fresh authorized data wins over a free snapshot; a fresh source also
        # wins over an older authorized value that has already gone stale.
        return {"realtime": 4, "best_effort": 3, "delayed": 2, "stale": 1}.get(quality, 0)

    def _visible_items(self, rows: list[dict[str, Any]], now: datetime) -> dict[str, dict[str, Any]]:
        """Choose one provider per code without letting fallback data overwrite
        a better source merely because it arrived later in the dictionary."""
        chosen: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
        for row in rows:
            quality, age = self._status(row, now)
            item = {**row, "age_seconds": round(age, 3), "quality": quality}
            try:
                received = datetime.fromisoformat(row["received_at"]).timestamp()
            except (KeyError, TypeError, ValueError):
                received = 0.0
            key = (self._quality_rank(quality), -age, received, bool(row.get("authorization")))
            current = chosen.get(row["code"])
            if current is None or key > current[0]:
                chosen[row["code"]] = (key, item)
        return {code: item for code, (_key, item) in chosen.items()}

    def latest(self, codes: Iterable[str] | None = None) -> dict[str, Any]:
        wanted = {str(code).zfill(6) for code in codes or [] if str(code).strip()}
        now = self._now_fn().astimezone(UTC)
        if self._store is not None:
            try:
                rows = self._store.latest(wanted or None)
            except Exception:
                # A shared-store outage must be visible as unavailable; do not
                # silently serve a worker-local copy and call it shared data.
                rows = []
        else:
            with self._lock:
                rows = list(self._latest.values())
        if wanted:
            rows = [row for row in rows if row["code"] in wanted]
        items = self._visible_items(rows, now)
        if wanted:
            for code in sorted(wanted - set(items)):
                items[code] = {"code": code, "quality": "unavailable",
                               "price": None, "provider": None,
                               "authorization": False, "age_seconds": None,
                               "provider_time": None, "received_at": None,
                               "sequence": None, "sequence_gap": False, "session": None,
                               "pct_chg": None}
        return {"mode": self.mode, "items": items,
                "as_of": now.isoformat(),
                "max_age_seconds": self.max_age_seconds,
                "delayed_max_age_seconds": self.delayed_max_age_seconds}

    def health(self) -> dict[str, Any]:
        now = self._now_fn().astimezone(UTC)
        storage_health = None
        if self._store is not None:
            storage_health = self._store.health()
            try:
                rows = self._store.latest()
            except Exception:
                rows = []
        else:
            rows = None
        with self._lock:
            qualities = [item["quality"] for item in
                         self._visible_items(
                             list(self._latest.values()) if rows is None else rows,
                             now).values()]
            if not qualities:
                status = "unavailable"
            elif any(value == "stale" for value in qualities):
                status = "degraded"
            elif any(value in {"best_effort", "delayed"} for value in qualities):
                # A fresh public snapshot is useful for research, but it is not
                # an authorized real-time feed and must not look healthy enough
                # for trading alerts.
                status = "degraded"
            elif any(value == "realtime" for value in qualities):
                status = "ok"
            else:
                status = "unavailable"
            providers = {name: dict(value) for name, value in self._providers.items()}
            result = {
                "status": status,
                "mode": self.mode,
                "storage": self.mode,
                "single_process": self._store is None,
                "sla_ready": False,
                "latest_count": (len(self._latest) if rows is None else
                                  int((storage_health or {}).get("latest_count", 0))),
                "accepted": (self._accepted if storage_health is None else
                              int(storage_health.get("accepted", 0))),
                "rejected": (self._rejected if storage_health is None else
                              int(storage_health.get("rejected", 0))),
                "sequence_gaps": (self._sequence_gaps if storage_health is None else
                                   int(storage_health.get("sequence_gaps", 0))),
                "reject_reasons": (dict(self._reject_reasons) if storage_health is None else
                                   dict(storage_health.get("reject_reasons", {}))),
                "last_accepted_at": (self._last_accepted_at.isoformat()
                                     if storage_health is None and self._last_accepted_at
                                     else (storage_health or {}).get("last_accepted_at")),
                "providers": providers,
                "as_of": now.isoformat(),
                "max_age_seconds": self.max_age_seconds,
                "delayed_max_age_seconds": self.delayed_max_age_seconds,
            }
            if storage_health is not None:
                result["shared_storage"] = storage_health
                if not storage_health.get("connected"):
                    result["status"] = "unavailable"
            return result

    def reset(self) -> None:
        if self._store is not None:
            self._store.reset()
        with self._lock:
            self._latest.clear()
            self._last_sequence.clear()
            self._last_provider_time.clear()
            self._last_provider_sequence.clear()
            self._gap_pending.clear()
            self._sequence_gaps = 0
            self._accepted = self._rejected = 0
            self._reject_reasons.clear()
            self._last_accepted_at = None
            self._providers.clear()

    # ``snapshot`` is the provider-facing name; latest() remains for REST
    # callers and backwards-compatible tests.
    def snapshot(self, codes: Iterable[str] | None = None) -> dict[str, Any]:
        return self.latest(codes)


def _configured_store() -> Any | None:
    """Build the optional shared store without importing redis at startup."""
    from ..config import settings

    if settings.marketdata_storage_mode == "memory":
        return None
    from .redis_store import RedisQuoteStore

    # The optional redis client and connectivity failures are already exposed
    # by RedisQuoteStore.health().  Unexpected configuration/programming errors
    # must propagate: silently returning None here would turn a requested
    # shared store into process-local memory while reporting the wrong mode.
    return RedisQuoteStore(
        settings.marketdata_redis_url,
        prefix=settings.marketdata_redis_prefix,
        stream_maxlen=settings.marketdata_redis_stream_maxlen,
    )


_HUB = QuoteHub(store=_configured_store())


def get_hub() -> QuoteHub:
    return _HUB


def authorized_providers_from_env() -> set[str]:
    raw = os.environ.get("MARKETDATA_AUTHORIZED_PROVIDERS", "")
    if not raw:
        try:
            from ..config import settings
            raw = settings.marketdata_authorized_providers
        except Exception:  # pragma: no cover - import-time fallback for tiny adapters
            raw = ""
    return {item.strip() for item in raw.split(",") if item.strip()}
