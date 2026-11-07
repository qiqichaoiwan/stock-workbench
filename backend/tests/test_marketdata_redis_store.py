from datetime import datetime, timezone
import json

from app.marketdata.hub import QuoteHub
from app.marketdata.redis_store import RedisQuoteStore

UTC = timezone.utc


class FakeRedis:
    """Tiny redis-py-shaped fake that executes the store's atomic contract."""

    def __init__(self):
        self.hashes = {}
        self.streams = {}
        self.fail = False
        self.fail_message = "redis down"

    def ping(self):
        if self.fail:
            raise ConnectionError(self.fail_message)
        return True

    def _hash(self, name):
        return self.hashes.setdefault(name, {})

    def eval(self, _script, numkeys, *args):
        if self.fail:
            raise ConnectionError(self.fail_message)
        keys = args[:numkeys]
        latest, sequence, provider_time, stream, metrics, gap_pending, provider_sequence = keys
        key, payload, seq, stamp, received_at, maxlen, resync, opaque = args[numkeys:]
        old_seq = self._hash(sequence).get(key)
        old_time = self._hash(provider_time).get(key)
        old_opaque = self._hash(provider_sequence).get(key)
        if seq and old_seq is not None and int(seq) <= int(old_seq):
            return [0, "sequence_out_of_order", old_seq]
        if old_time is not None and stamp < old_time:
            return [0, "provider_time_out_of_order", old_time]
        if old_time is not None and stamp == old_time and not seq:
            if not opaque or (old_opaque is not None and opaque <= old_opaque):
                return [0, "provider_time_out_of_order", old_time]
        observed_gap = int(bool(seq and old_seq is not None and int(seq) > int(old_seq) + 1))
        previous_pending = self._hash(gap_pending).get(key) == "1"
        gap = 0 if resync == "1" else int(previous_pending or bool(observed_gap))
        event = json.loads(payload)
        event["sequence_gap"] = bool(gap)
        payload = json.dumps(event)
        self._hash(latest)[key] = payload
        if seq:
            self._hash(sequence)[key] = seq
        self._hash(provider_time)[key] = stamp
        if opaque:
            self._hash(provider_sequence)[key] = opaque
        elif old_time is None or stamp != old_time:
            self._hash(provider_sequence).pop(key, None)
        self._hash(gap_pending)[key] = str(gap)
        self.streams.setdefault(stream, []).append({"key": key, "event": payload})
        self._hash(metrics)["accepted"] = str(int(self._hash(metrics).get("accepted", 0)) + 1)
        if observed_gap:
            self._hash(metrics)["sequence_gaps"] = str(int(self._hash(metrics).get("sequence_gaps", 0)) + 1)
        self._hash(metrics)["last_accepted_at"] = received_at
        self._hash(metrics)["last_provider_time"] = stamp
        return [1, gap, observed_gap]

    def hgetall(self, name):
        if self.fail:
            raise ConnectionError("redis down")
        return dict(self._hash(name))

    def hlen(self, name):
        return len(self._hash(name))

    def hincrby(self, name, key, amount):
        self._hash(name)[key] = str(int(self._hash(name).get(key, 0)) + int(amount))
        return int(self._hash(name)[key])

    def xlen(self, name):
        return len(self.streams.get(name, []))

    def delete(self, *names):
        for name in names:
            self.hashes.pop(name, None)
            self.streams.pop(name, None)


def item(stamp, sequence=1, code="600000"):
    return {
        "code": code,
        "price": 10.5,
        "pct_chg": 1.2,
        "provider": "licensed-feed",
        "authorization": True,
        "provider_time": stamp.isoformat(),
        "received_at": stamp.isoformat(),
        "sequence": sequence,
        "sequence_gap": False,
        "session": "trading",
    }


def test_redis_store_persists_latest_and_rejects_duplicate_sequence():
    client = FakeRedis()
    store = RedisQuoteStore("redis://unused", client=client)
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    assert store.append(item(stamp, 1))["accepted"] is True
    assert store.append(item(stamp.replace(second=1), 1))["reason"] == "sequence_out_of_order"
    assert store.latest()[0]["code"] == "600000"
    health = store.health()
    assert health["connected"] is True
    assert health["accepted"] == 1
    assert health["stream_length"] == 1
    assert health["last_accepted_at"] == stamp.isoformat()


def test_redis_store_sequence_gap_is_shared_between_hub_instances():
    client = FakeRedis()
    first = RedisQuoteStore("redis://unused", client=client)
    second = RedisQuoteStore("redis://unused", client=client)
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    assert first.append(item(stamp, 10))["accepted"] is True
    result = second.append(item(stamp.replace(second=1), 12))
    assert result == {"accepted": True, "sequence_gap": True}
    assert second.health()["sequence_gaps"] == 1


def test_redis_store_accepts_opaque_provider_order_at_equal_timestamp():
    client = FakeRedis()
    store = RedisQuoteStore("redis://unused", client=client)
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    first = {**item(stamp, None), "sequence": None, "provider_sequence": "9007199254740991"}
    second = {**item(stamp, None), "sequence": None, "provider_sequence": "9007199254740992"}
    assert store.append(first)["accepted"] is True
    assert store.append(second)["accepted"] is True
    older = {**item(stamp, None), "sequence": None, "provider_sequence": "9007199254740992"}
    assert store.append(older)["reason"] == "provider_time_out_of_order"


def test_hub_reads_shared_store_after_local_hub_restart():
    client = FakeRedis()
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    first = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    second = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    event = item(stamp, 1)
    assert first.ingest(event)["accepted"] is True
    snapshot = second.latest(["600000"])
    assert snapshot["items"]["600000"]["price"] == 10.5
    assert snapshot["items"]["600000"]["quality"] == "realtime"
    assert second.ingest({**event, "price": 10.6})["reason"] == "sequence_out_of_order"
    health = second.health()
    assert health["rejected"] == 1
    assert health["reject_reasons"]["sequence_out_of_order"] == 1
    assert health["last_accepted_at"] == stamp.isoformat()


def test_hub_uses_shared_sequence_gap_metric_when_workers_interleave():
    client = FakeRedis()
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    first = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    second = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    assert first.ingest(item(stamp, 1))["accepted"] is True
    # This worker has no local sequence state, but Redis knows sequence 1 and
    # must classify sequence 2 as contiguous (no false local gap).
    assert second.ingest(item(stamp.replace(second=1), 2))["accepted"] is True
    assert second.health()["sequence_gaps"] == 0
    assert first.ingest(item(stamp.replace(second=2), 4))["accepted"] is True
    assert second.health()["sequence_gaps"] == 1
    assert second.latest(["600000"])["items"]["600000"]["quality"] == "delayed"


def test_shared_store_persists_atomic_gap_when_new_worker_ingests():
    client = FakeRedis()
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    first = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    second = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    assert first.ingest(item(stamp, 1))["accepted"] is True
    assert second.ingest(item(stamp, 3))["accepted"] is True
    quote = first.latest(["600000"])["items"]["600000"]
    assert quote["sequence_gap"] is True
    assert quote["quality"] == "delayed"
    stored_stream = client.streams[first._store.stream_key][-1]["event"]
    assert json.loads(stored_stream)["sequence_gap"] is True


def test_shared_store_keeps_gap_latched_until_explicit_resync():
    client = FakeRedis()
    first = QuoteHub(now_fn=lambda: datetime(2026, 11, 9, 2, tzinfo=UTC),
                     store=RedisQuoteStore("redis://unused", client=client))
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    assert first.ingest(item(stamp, 1))["accepted"] is True
    assert first.ingest(item(stamp.replace(second=1), 3))["accepted"] is True
    assert first.ingest(item(stamp.replace(second=2), 4))["accepted"] is True
    assert first.latest()["items"]["600000"]["sequence_gap"] is True
    assert first.ingest({**item(stamp.replace(second=2), 5), "sequence_resync": True})["accepted"] is True
    assert first.latest()["items"]["600000"]["sequence_gap"] is False


def test_shared_store_does_not_persist_false_local_gap():
    client = FakeRedis()
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    first = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    second = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    assert first.ingest(item(stamp, 1))["accepted"] is True
    assert second.ingest(item(stamp, 2))["accepted"] is True
    assert first.ingest(item(stamp, 3))["accepted"] is True
    quote = second.latest(["600000"])["items"]["600000"]
    assert quote["sequence_gap"] is False
    assert quote["quality"] == "realtime"


def test_shared_store_rejects_sequenced_old_time_across_workers():
    client = FakeRedis()
    stamp = datetime(2026, 11, 9, 2, 0, 2, tzinfo=UTC)
    first = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    second = QuoteHub(now_fn=lambda: stamp, store=RedisQuoteStore("redis://unused", client=client))
    assert first.ingest(item(stamp, 1))["accepted"] is True
    older = item(stamp.replace(second=1), 2)
    assert second.ingest(older)["reason"] == "provider_time_out_of_order"
    assert second.latest()["items"]["600000"]["sequence"] == 1


def test_unavailable_redis_is_explicit_in_health_and_append():
    client = FakeRedis()
    client.fail = True
    client.fail_message = "dial redis://user:super-secret@example.test:6379/2 password=super-secret"
    store = RedisQuoteStore("redis://unused", client=client)
    stamp = datetime(2026, 11, 9, 2, tzinfo=UTC)
    assert store.append(item(stamp))["reason"] == "storage_unavailable"
    assert store.health()["connected"] is False


def test_redis_health_redacts_connection_credentials():
    client = FakeRedis()
    client.fail = True
    store = RedisQuoteStore("redis://user:super-secret@example.test:6379/2", client=client)
    result = store.health()
    assert result["connected"] is False
    assert "super-secret" not in (result["error"] or "")
    assert "redis://user:super-secret" not in (result["error"] or "")
