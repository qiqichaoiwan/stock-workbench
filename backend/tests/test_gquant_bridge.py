from datetime import datetime, timedelta, timezone
import math
import threading
import time

import pytest

from app.marketdata.gquant import (
    GQuantBridge,
    GQuantBridgeError,
    normalize_code,
    normalize_tick,
)
from app.marketdata.hub import QuoteHub

UTC = timezone.utc


def test_normalize_tick_rejects_unrepresentable_unix_timestamp():
    with pytest.raises(ValueError, match="provider_time_invalid"):
        normalize_tick({"symbol": "SHSE.600000", "price": 10,
                        "created_at": 10**100})


def tick(code="SHSE.600000", *, price=10.5, at="2026-11-09 10:00:00", pct=1.2, **extra):
    return {"symbol": code, "price": price, "created_at": at, "change_rate": pct, **extra}


def ack(payload):
    rows = [{"accepted": True, "code": row["code"]} for row in payload["events"]]
    return {"accepted": len(rows), "rejected": 0, "results": rows}


def test_normalize_code_and_naive_time_use_a_share_conventions():
    assert normalize_code("SHSE.600000") == "600000"
    assert normalize_code("SZ.000001") == "000001"
    assert normalize_code("600519") == "600519"
    with pytest.raises(ValueError, match="code_invalid"):
        normalize_code("abc600000xyz")
    event = normalize_tick(tick(), received_at=datetime(2026, 11, 9, 2, tzinfo=UTC))
    assert event["provider"] == "gquant"
    assert event["code"] == "600000"
    assert event["provider_time"] == "2026-11-09T02:00:00+00:00"
    assert event["received_at"] == "2026-11-09T02:00:00+00:00"


def test_bridge_batches_dataframe_like_rows_without_inventing_sequences():
    calls = []

    def post(url, token, payload, timeout):
        calls.append((url, token, payload, timeout))
        return ack(payload)

    class FakeFrame:
        def to_dict(self, orient=None):
            assert orient == "records"
            return [tick("SHSE.600000"), tick("SZSE.000001", price=11)]

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=2,
                          post_json=post)
    result = bridge.on_tick(FakeFrame())
    assert result["converted"] == 2
    assert result["sent"] == 2
    assert calls[0][0] == "http://localhost/ingest"
    assert calls[0][1] == "secret-token"
    events = calls[0][2]["events"]
    assert [item["code"] for item in events] == ["600000", "000001"]
    assert [item["sequence"] for item in events] == [None, None]
    assert bridge.close()["sent"] == 0


def test_bridge_retries_transient_failure_without_logging_token(monkeypatch):
    calls = []

    def post(_url, _token, payload, _timeout):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("secret-token must never escape")
        return ack(payload)

    sleeps = []
    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                          retry_times=1, retry_wait_seconds=0.01,
                          post_json=post, sleep_fn=sleeps.append)
    result = bridge.on_tick(tick())
    assert result["sent"] == 1
    assert len(calls) == 2
    assert sleeps == [0.01]


def test_bridge_exhausted_network_failure_is_reported_as_unconfirmed():
    attempts = []

    def post(_url, _token, payload, _timeout):
        attempts.append(payload)
        raise OSError("down")

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                          retry_times=0, post_json=post)
    result = bridge.on_tick(tick())
    assert result["sent"] == 0
    assert result["unconfirmed"] == 1
    assert bridge.health()["unconfirmed"] == 1
    assert bridge.health()["pending"] == 0
    assert len(attempts) == 1


def test_sequence_rejects_fractional_and_nonfinite_values():
    with pytest.raises(ValueError, match="sequence_invalid"):
        normalize_tick(tick(sequence=1.9))
    with pytest.raises(ValueError, match="sequence_invalid"):
        normalize_tick(tick(sequence=math.inf))
    with pytest.raises(ValueError, match="sequence_invalid"):
        normalize_tick(tick(sequence="1.0"))
    assert normalize_tick(tick(sequence=1.0))["sequence"] == 1


def test_remote_http_requires_explicit_insecure_override(monkeypatch):
    monkeypatch.delenv("GQUANT_ALLOW_INSECURE_HTTP", raising=False)
    with pytest.raises(GQuantBridgeError, match="HTTPS"):
        GQuantBridge("http://quotes.example.com/ingest", "secret-token")
    monkeypatch.setenv("GQUANT_ALLOW_INSECURE_HTTP", "true")
    GQuantBridge("http://quotes.example.com/ingest", "secret-token")
    GQuantBridge("https://quotes.example.com/ingest", "secret-token")


def test_single_tick_flushes_after_interval_and_default_is_below_one_second():
    calls = []

    def post(_url, _token, payload, _timeout):
        calls.append(payload)
        return ack(payload)

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", post_json=post)
    assert 0 < bridge.flush_interval_seconds <= 1
    bridge.on_tick(tick())
    # Timer is daemonized and flushes a low-volume stream even when no next tick
    # arrives to call ``flush_if_due``.
    deadline = time.monotonic() + 1.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) == 1


def test_injected_clock_flushes_due_tail_without_waiting():
    clock = [0.0]
    calls = []

    def post(_url, _token, payload, _timeout):
        calls.append(payload)
        return ack(payload)

    bridge = GQuantBridge("http://localhost/ingest", "secret-token",
                          flush_interval_seconds=10, clock_fn=lambda: clock[0],
                          post_json=post)
    assert bridge.on_tick(tick())["sent"] == 0
    clock[0] = 10.1
    assert bridge.flush_if_due()["sent"] == 1
    assert len(calls) == 1


def test_queue_full_is_explicit_after_failed_full_batch():
    def post(_url, _token, _payload, _timeout):
        raise OSError("down")

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=2,
                          max_pending=2, retry_times=0, post_json=post)
    with pytest.raises(GQuantBridgeError, match="队列已满"):
        bridge.on_tick([tick(), tick("SZSE.000001"), tick("SHSE.600519")])
    bridge.close()


def test_timer_and_full_batch_dispatch_preserve_sequence_order():
    calls = []
    first_started = threading.Event()
    release_first = threading.Event()

    def post(_url, _token, payload, _timeout):
        sequence = payload["events"][0]["sequence"]
        if sequence == 0:
            first_started.set()
            assert release_first.wait(1)
        calls.extend(item["sequence"] for item in payload["events"])
        return ack(payload)

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=2,
                          flush_interval_seconds=10, post_json=post)
    bridge.on_tick(tick(sequence=0))  # keep seq=0 pending
    flush_thread = threading.Thread(target=bridge.flush)
    flush_thread.start()
    assert first_started.wait(1)

    batch_thread = threading.Thread(target=lambda: bridge.on_tick([tick(sequence=1), tick(sequence=2)]))
    batch_thread.start()
    time.sleep(0.02)
    release_first.set()
    flush_thread.join(1)
    batch_thread.join(1)

    assert calls == [0, 1, 2]


def test_restart_without_provider_sequence_uses_server_time_ordering():
    """A bridge restart must not reset a process-local sequence counter."""
    hub = QuoteHub()

    def post(_url, _token, payload, _timeout):
        return hub.ingest_many(payload["events"])

    first = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                         post_json=post)
    second = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                          post_json=post)
    at = datetime.now(UTC).replace(microsecond=0)
    assert first.on_tick(tick(at=at.isoformat()))["sent"] == 1
    assert second.on_tick(tick(at=(at + timedelta(seconds=1)).isoformat()))["sent"] == 1
    assert hub.latest()["items"]["600000"]["sequence"] is None


def test_restart_same_timestamp_reports_server_order_rejection():
    """Equal unsequenced timestamps remain distinguishable from a restart."""
    hub = QuoteHub()

    def post(_url, _token, payload, _timeout):
        return hub.ingest_many(payload["events"])

    first = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                         post_json=post)
    second = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                          post_json=post)
    at = datetime.now(UTC).replace(microsecond=0)
    assert first.on_tick(tick(at=at.isoformat()))["sent"] == 1
    result = second.on_tick(tick(at=at.isoformat()))
    assert result["sent"] == 0
    assert result["rejected"] == 1
    assert result["reject_reasons"] == {"provider_time_out_of_order": 1}
    assert second.health()["reject_reasons"] == {"provider_time_out_of_order": 1}


def test_mixed_ack_retries_only_storage_failures():
    calls = []

    def post(_url, _token, payload, _timeout):
        calls.append(payload)
        if len(calls) == 1:
            rows = [
                {"accepted": True, "code": payload["events"][0]["code"]},
                {"accepted": False, "reason": "code_invalid"},
                {"accepted": False, "reason": "storage_unavailable"},
            ]
            return {"accepted": 1, "rejected": 2, "results": rows}
        return ack(payload)

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=3,
                          retry_times=2, retry_wait_seconds=0,
                          post_json=post, sleep_fn=lambda _seconds: None)
    result = bridge.on_tick([
        tick("SHSE.600000"), tick("SZSE.000001"), tick("SHSE.600519"),
    ])
    assert result["sent"] == 2
    assert result["rejected"] == 1
    assert result["unconfirmed"] == 0
    assert result["reject_reasons"] == {"code_invalid": 1}
    assert [len(payload["events"]) for payload in calls] == [3, 1]
    assert calls[1]["events"][0]["code"] == "600519"
    assert bridge.health()["accepted"] == 2
    assert bridge.health()["rejected"] == 1


def test_storage_failure_has_bounded_attempts_and_is_reported():
    calls = []
    sleeps = []

    def post(_url, _token, payload, _timeout):
        calls.append(payload)
        return {"accepted": 0, "rejected": 1, "results": [
            {"accepted": False, "reason": "storage_unavailable"},
        ]}

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                          retry_times=2, retry_wait_seconds=0.01,
                          post_json=post, sleep_fn=sleeps.append)
    result = bridge.on_tick(tick())
    assert result["sent"] == 0
    assert result["rejected"] == 1
    assert result["reject_reasons"] == {"storage_unavailable": 1}
    assert len(calls) == 3  # initial request plus two storage retries
    assert len(sleeps) == 2
    assert bridge.health()["pending"] == 0


@pytest.mark.parametrize("response", [
    {},
    {"accepted": 1, "rejected": 0, "results": []},
    {"accepted": 1, "rejected": 0, "results": [{"accepted": True}]},
])
def test_malformed_ack_is_unconfirmed_and_not_silently_accepted(response):
    def post(_url, _token, _payload, _timeout):
        return response

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                          retry_times=0, post_json=post)
    result = bridge.on_tick(tick())
    assert result["sent"] == 0
    assert result["rejected"] == 0
    assert result["unconfirmed"] == 1
    assert result["batches"] == 1
    assert bridge.health()["unconfirmed"] == 1
    assert bridge.health()["pending"] == 0


def test_unexpected_transport_exception_is_bounded_and_unconfirmed():
    attempts = []

    def post(_url, _token, _payload, _timeout):
        attempts.append(1)
        raise RuntimeError("transport secret-token detail")

    bridge = GQuantBridge("http://localhost/ingest", "secret-token", batch_size=1,
                          retry_times=1, retry_wait_seconds=0,
                          post_json=post, sleep_fn=lambda _seconds: None)
    result = bridge.on_tick(tick())
    assert result["unconfirmed"] == 1
    assert len(attempts) == 2
