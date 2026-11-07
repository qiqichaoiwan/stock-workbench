"""Optional Redis Streams + latest snapshot storage for market data.

The default application uses the in-process :class:`QuoteHub`.  This module is
kept optional so a developer can run the project without installing ``redis`` or
starting a Redis server.  When enabled, ``RedisQuoteStore.append`` uses a Lua
script to atomically validate sequence ordering, update the latest hash, append
the raw normalized event to a Redis Stream, and increment shared counters.

Redis is a shared transport/snapshot layer, not an exchange data entitlement.
The caller still decides whether a provider is authorized and the Hub still
assigns ``realtime``/``best_effort``/``delayed`` quality.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any


_APPEND_SCRIPT = r"""
local key = ARGV[1]
local payload = ARGV[2]
local seq = ARGV[3]
local provider_time = ARGV[4]
local received_at = ARGV[5]
local stream_maxlen = tonumber(ARGV[6])
local resync = ARGV[7]
local previous_seq = redis.call('HGET', KEYS[2], key)
local previous_time = redis.call('HGET', KEYS[3], key)
local previous_pending = redis.call('HGET', KEYS[6], key)

-- Lua numbers are doubles and lose integer precision above 2^53. Compare and
-- increment the decimal strings directly so large provider sequences remain
-- exact, including a sequence gap near that boundary.
local function compare_decimal(a, b)
  a = string.gsub(a, '^0+', '')
  b = string.gsub(b, '^0+', '')
  if a == '' then a = '0' end
  if b == '' then b = '0' end
  if #a ~= #b then
    if #a < #b then return -1 else return 1 end
  end
  if a == b then return 0 end
  if a < b then return -1 else return 1 end
end

local function increment_decimal(s)
  local reversed = {}
  local carry = 1
  for i = #s, 1, -1 do
    local digit = string.byte(s, i) - 48 + carry
    if digit >= 10 then digit = digit - 10; carry = 1 else carry = 0 end
    reversed[#reversed + 1] = string.char(48 + digit)
  end
  if carry == 1 then reversed[#reversed + 1] = '1' end
  return string.reverse(table.concat(reversed))
end

if seq ~= '' and previous_seq ~= false and compare_decimal(seq, previous_seq) <= 0 then
  return {0, 'sequence_out_of_order', previous_seq}
end
local provider_sequence = ARGV[8]
local previous_provider_sequence = redis.call('HGET', KEYS[7], key)
local function compare_opaque(a, b)
  local a_digits = string.match(a, '^%d+$') ~= nil
  local b_digits = string.match(b, '^%d+$') ~= nil
  if a_digits and b_digits then
    return compare_decimal(a, b)
  end
  if a == b then return 0 end
  if a < b then return -1 else return 1 end
end
if previous_time ~= false and provider_time < previous_time then
  return {0, 'provider_time_out_of_order', previous_time}
end
if previous_time ~= false and provider_time == previous_time and seq == '' then
  if provider_sequence == '' then
    return {0, 'provider_time_out_of_order', previous_time}
  end
  if previous_provider_sequence ~= false and
      compare_opaque(provider_sequence, previous_provider_sequence) <= 0 then
  return {0, 'provider_time_out_of_order', previous_time}
  end
end

local observed_gap = 0
if seq ~= '' and previous_seq ~= false and compare_decimal(seq, increment_decimal(previous_seq)) > 0 then
  observed_gap = 1
end

-- A gap is sticky across subsequent events.  An explicitly marked resync or
-- a provider proof that the sequence is contiguous is the only way to clear
-- the latch; an increasing sequence by itself is insufficient.
local gap_pending = 0
if resync == '1' then
  gap_pending = 0
elseif previous_pending == '1' or observed_gap == 1 then
  gap_pending = 1
end

-- The Redis sequence check is authoritative across workers.  The incoming
-- payload's flag reflects only the sending worker's local state, so persist
-- the atomic result in both latest and the stream before publishing it. Do
-- not decode/re-encode the JSON with cjson: that rounds prices and sequences.
if gap_pending == 1 then
  payload = string.gsub(payload, '"sequence_gap":false', '"sequence_gap":true', 1)
else
  payload = string.gsub(payload, '"sequence_gap":true', '"sequence_gap":false', 1)
end
redis.call('HSET', KEYS[1], key, payload)
if seq ~= '' then
  redis.call('HSET', KEYS[2], key, seq)
end
redis.call('HSET', KEYS[3], key, provider_time)
if provider_sequence ~= '' then
  redis.call('HSET', KEYS[7], key, provider_sequence)
elseif previous_time == false or provider_time ~= previous_time then
  redis.call('HDEL', KEYS[7], key)
end
redis.call('HSET', KEYS[6], key, tostring(gap_pending))
redis.call('XADD', KEYS[4], 'MAXLEN', '~', stream_maxlen, '*', 'key', key, 'event', payload)
redis.call('HINCRBY', KEYS[5], 'accepted', 1)
if observed_gap == 1 then
  redis.call('HINCRBY', KEYS[5], 'sequence_gaps', 1)
end
redis.call('HSET', KEYS[5], 'last_accepted_at', received_at)
redis.call('HSET', KEYS[5], 'last_provider_time', provider_time)
return {1, gap_pending, observed_gap}
"""


class RedisQuoteStore:
    """Shared latest snapshot and append-only stream backed by Redis.

    ``client`` is injectable for tests and for applications that already own a
    configured redis-py connection.  A real client is created lazily from
    ``url`` only when no client was supplied.
    """

    mode = "redis"

    def __init__(
        self,
        url: str,
        *,
        prefix: str = "stock-workbench:marketdata",
        stream_maxlen: int = 100_000,
        client: Any | None = None,
        connect: bool = True,
    ) -> None:
        self.url = str(url or "").strip()
        self.prefix = str(prefix or "stock-workbench:marketdata").strip().rstrip(":")
        self.stream_maxlen = max(1, int(stream_maxlen))
        self._client = client
        self._lock = threading.RLock()
        self._error: str | None = None
        self._available = False
        if self._client is None and connect:
            self._client = self._create_client()
        if self._client is not None and connect:
            self._probe()

    @property
    def latest_key(self) -> str:
        return f"{self.prefix}:latest"

    @property
    def sequence_key(self) -> str:
        return f"{self.prefix}:sequence"

    @property
    def time_key(self) -> str:
        return f"{self.prefix}:provider_time"

    @property
    def stream_key(self) -> str:
        return f"{self.prefix}:stream"

    @property
    def metrics_key(self) -> str:
        return f"{self.prefix}:metrics"

    @property
    def provider_health_key(self) -> str:
        """Shared provider heartbeat hash.

        The Futu worker and the API commonly run as separate processes.  The
        API therefore cannot rely on its local adapter object to decide
        readiness; the worker publishes its sanitized heartbeat here.
        """
        return f"{self.prefix}:provider_health"

    @property
    def provider_sequence_key(self) -> str:
        """Opaque provider order IDs used only for equal-time tie-breaking."""
        return f"{self.prefix}:provider_sequence"

    @property
    def gap_key(self) -> str:
        return f"{self.prefix}:gap_pending"

    def _create_client(self) -> Any | None:
        if not self.url:
            self._error = "MARKETDATA_REDIS_URL 未配置"
            return None
        try:
            import redis  # type: ignore
        except ImportError:
            self._error = "redis 客户端未安装；请安装 backend/requirements-marketdata-redis.txt"
            return None
        try:
            return redis.Redis.from_url(
                self.url,
                decode_responses=True,
                health_check_interval=15,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as exc:  # noqa: BLE001 - health is reported to operators
            self._error = self._safe_error(exc)
            return None

    def _safe_error(self, exc: Any) -> str:
        """Redact passwords/tokens from redis-py errors before health output."""
        text = str(exc)
        text = re.sub(r"(?i)(redis(?:s)?://)([^:@/]*):([^@/]+)@",
                      r"\1\2:<redacted>@", text)
        text = re.sub(r"(?i)(password|passwd|token|secret)=([^&\s]+)",
                      r"\1=<redacted>", text)
        if self.url:
            text = text.replace(self.url, "<redis-url>")
        return text[:300]

    def _probe(self) -> bool:
        if self._client is None:
            self._available = False
            return False
        try:
            self._client.ping()
            self._available = True
            self._error = None
            return True
        except Exception as exc:  # noqa: BLE001 - optional dependency/service
            self._available = False
            self._error = self._safe_error(exc)
            return False

    def _ensure_client(self) -> Any:
        if self._client is None:
            self._client = self._create_client()
        # redis-py already performs connection health checks and reconnects at
        # command time. Avoid an extra PING for every tick; probe only on first
        # use or after the previous operation marked the client unavailable.
        if self._client is None or (not self._available and not self._probe()):
            raise RuntimeError(self._error or "Redis 不可用")
        return self._client

    @staticmethod
    def _decode(value: Any) -> Any:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    @staticmethod
    def _metric_suffix(value: Any) -> str:
        """Keep caller-provided reasons safe as Redis hash field suffixes."""
        suffix = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", str(value or "unknown"))
        return suffix[:96] or "unknown"

    def record_rejection(self, reason: str) -> bool:
        """Increment shared rejection metrics without raising on Redis failure.

        This method is intentionally best effort: the Hub already has the
        original validation/storage error and must not replace it with a
        metrics connection error.
        """
        try:
            client = self._ensure_client()
            client.hincrby(self.metrics_key, "rejected", 1)
            client.hincrby(self.metrics_key,
                           f"reject_reason:{self._metric_suffix(reason)}", 1)
            self._available = True
            self._error = None
            return True
        except Exception as exc:  # noqa: BLE001 - diagnostics must be non-fatal
            self._available = False
            self._error = self._safe_error(exc)
            return False

    def set_provider_health(self, health: dict[str, Any]) -> bool:
        """Publish one provider heartbeat for the read-only API health view."""
        provider = str(health.get("provider") or "").strip()
        if not provider:
            return False
        payload = dict(health)
        payload["updated_at"] = str(payload.get("updated_at") or
                                     datetime.now(timezone.utc).isoformat())
        try:
            client = self._ensure_client()
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                                 sort_keys=True)
            client.hset(self.provider_health_key, provider, encoded)
            self._available = True
            self._error = None
            return True
        except Exception as exc:  # noqa: BLE001 - heartbeat is best effort
            self._available = False
            self._error = self._safe_error(exc)
            return False

    def get_provider_health(self, provider: str | None = None) -> dict[str, Any] | None | dict[str, dict[str, Any]]:
        """Read worker-published provider heartbeat(s), decoding JSON safely."""
        try:
            client = self._ensure_client()
            rows = client.hgetall(self.provider_health_key) or {}
            decoded: dict[str, dict[str, Any]] = {}
            for raw_provider, raw_payload in rows.items():
                name = str(self._decode(raw_provider) or "").strip()
                try:
                    value = json.loads(self._decode(raw_payload))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if name and isinstance(value, dict):
                    decoded[name] = value
            self._available = True
            self._error = None
            if provider is not None:
                return decoded.get(str(provider).strip())
            return decoded
        except Exception as exc:  # noqa: BLE001 - health endpoint must not crash
            self._available = False
            self._error = self._safe_error(exc)
            return None if provider is not None else {}

    def append(self, item: dict[str, Any]) -> dict[str, Any]:
        """Atomically append a normalized event and update its latest value.

        Returns ``accepted``/``reason`` in the same shape as Hub ingestion.  A
        Redis outage raises ``storage_unavailable`` instead of silently
        pretending that a local process snapshot is production-safe.
        """
        code = str(item.get("code") or "").strip()
        provider = str(item.get("provider") or "").strip()
        key = f"{provider}|{code}"
        sequence = item.get("sequence")
        sequence_arg = "" if sequence is None else str(int(sequence))
        provider_time = str(item.get("provider_time") or "")
        # A canonical boolean field lets the Lua script update only the gap
        # flag without passing the remaining numeric values through Lua cjson.
        item = {**item, "sequence_gap": bool(item.get("sequence_gap"))}
        resync = bool(item.get("sequence_resync")) or item.get("sequence_contiguous") is True
        payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        try:
            client = self._ensure_client()
            result = client.eval(
                _APPEND_SCRIPT,
                7,
                self.latest_key,
                self.sequence_key,
                self.time_key,
                self.stream_key,
                self.metrics_key,
                self.gap_key,
                self.provider_sequence_key,
                key,
                payload,
                sequence_arg,
                provider_time,
                str(item.get("received_at") or provider_time),
                str(self.stream_maxlen),
                "1" if resync else "0",
                str(item.get("provider_sequence") or ""),
            )
            result = [self._decode(value) for value in (result or [])]
            if not result or int(result[0]) != 1:
                return {"accepted": False,
                        "reason": str(result[1] if len(result) > 1 else "out_of_order")}
            gap = bool(int(result[1])) if len(result) > 1 else False
            observed_gap = bool(int(result[2])) if len(result) > 2 else gap
            self._available = True
            self._error = None
            return {"accepted": True, "sequence_gap": gap}
        except Exception as exc:  # noqa: BLE001 - surface as a quality failure
            self._available = False
            self._error = self._safe_error(exc)
            return {"accepted": False, "reason": "storage_unavailable", "error": self._error}

    def latest(self, codes: set[str] | None = None) -> list[dict[str, Any]]:
        client = self._ensure_client()
        rows = client.hgetall(self.latest_key) or {}
        result: list[dict[str, Any]] = []
        wanted = {str(code).zfill(6) for code in (codes or set())}
        for raw_key, raw_payload in rows.items():
            try:
                item = json.loads(self._decode(raw_payload))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if wanted and str(item.get("code")) not in wanted:
                continue
            result.append(item)
        self._available = True
        self._error = None
        return result

    def health(self) -> dict[str, Any]:
        """Return a sanitized connectivity and shared-state snapshot."""
        try:
            client = self._ensure_client()
            metrics = client.hgetall(self.metrics_key) or {}
            provider_health: dict[str, dict[str, Any]] = {}
            for raw_provider, raw_payload in (client.hgetall(self.provider_health_key) or {}).items():
                name = str(self._decode(raw_provider) or "").strip()
                try:
                    value = json.loads(self._decode(raw_payload))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if name and isinstance(value, dict):
                    updated_at = value.get("updated_at")
                    try:
                        raw_stamp = str(updated_at).replace("Z", "+00:00")
                        stamp = datetime.fromisoformat(raw_stamp)
                        if stamp.tzinfo is not None and stamp.utcoffset() is not None:
                            value["heartbeat_age_seconds"] = round(
                                max(0.0, (datetime.now(timezone.utc) -
                                          stamp.astimezone(timezone.utc)).total_seconds()), 3)
                    except (TypeError, ValueError):
                        value["heartbeat_age_seconds"] = None
                    provider_health[name] = value
            latest_count = int(client.hlen(self.latest_key)) if hasattr(client, "hlen") else len(client.hgetall(self.latest_key) or {})
            stream_length = int(client.xlen(self.stream_key)) if hasattr(client, "xlen") else None
            self._available = True
            self._error = None
            return {
                "mode": self.mode,
                "configured": bool(self.url or self._client),
                "connected": True,
                "single_process": False,
                "latest_count": latest_count,
                "stream_length": stream_length,
                "accepted": int(metrics.get("accepted", 0) or 0),
                "rejected": int(metrics.get("rejected", 0) or 0),
                "sequence_gaps": int(metrics.get("sequence_gaps", 0) or 0),
                "last_accepted_at": self._decode(metrics.get("last_accepted_at")),
                "last_provider_time": self._decode(metrics.get("last_provider_time")),
                "reject_reasons": {
                    str(self._decode(key))[len("reject_reason:"):]: int(value or 0)
                    for key, value in metrics.items()
                    if str(self._decode(key)).startswith("reject_reason:")
                },
                "provider_health": provider_health,
                "error": None,
            }
        except Exception as exc:  # noqa: BLE001 - health endpoint must not crash
            self._available = False
            self._error = self._safe_error(exc)
            return {
                "mode": self.mode,
                "configured": bool(self.url or self._client),
                "connected": False,
                "single_process": False,
                "latest_count": 0,
                "stream_length": None,
                "accepted": 0,
                "rejected": 0,
                "sequence_gaps": 0,
                "last_accepted_at": None,
                "last_provider_time": None,
                "reject_reasons": {},
                "provider_health": {},
                "error": self._error,
            }

    def reset(self) -> None:
        client = self._ensure_client()
        client.delete(self.latest_key, self.sequence_key, self.time_key,
                      self.stream_key, self.metrics_key, self.gap_key,
                      self.provider_health_key, self.provider_sequence_key)

    @property
    def available(self) -> bool:
        return bool(self._available)
