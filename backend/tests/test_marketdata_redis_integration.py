"""Exercise the actual Redis Lua script when local Redis is available."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import pytest

from app.marketdata.hub import QuoteHub
from app.marketdata.redis_store import RedisQuoteStore


@pytest.fixture
def redis_client():
    redis = pytest.importorskip('redis')
    executable = shutil.which('redis-server')
    if not executable:
        pytest.skip('redis-server is not installed')
    # AF_UNIX paths are short (104 bytes on macOS); pytest's default temp
    # directory can exceed that limit. Keep this isolated socket under /tmp.
    with tempfile.TemporaryDirectory(prefix='swb-redis-', dir='/tmp') as folder:
        socket = Path(folder) / 'r.sock'
        proc = subprocess.Popen(
            [executable, '--port', '0', '--unixsocket', str(socket),
             '--unixsocketperm', '700', '--save', '', '--appendonly', 'no'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        client = redis.Redis(unix_socket_path=str(socket), decode_responses=True,
                             socket_connect_timeout=0.2, socket_timeout=0.2)
        try:
            deadline = time.monotonic() + 5
            while not socket.exists():
                if proc.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail('isolated redis-server failed to start')
                time.sleep(0.02)
            client.ping()
            yield client
        finally:
            client.close()
            proc.terminate()
            proc.wait(timeout=5)


def test_redis_lua_preserves_large_sequences_precision_and_atomic_quality(redis_client):
    now = datetime(2026, 11, 9, 2, tzinfo=timezone.utc)
    hubs = [QuoteHub(now_fn=lambda: now,
                     store=RedisQuoteStore('', prefix='lua-test', client=redis_client))
            for _ in range(2)]
    sequence = 9007199254740991
    price = 10.123456789012345

    def event(seq, stamp=now):
        return {'code': '600000', 'price': price, 'provider': 'licensed',
                'provider_time': stamp.isoformat(), 'sequence': seq}

    assert hubs[0].ingest(event(sequence), authorized=True)['accepted']
    assert hubs[1].ingest(event(sequence + 1), authorized=True)['accepted']
    contiguous = hubs[0].latest()['items']['600000']
    assert contiguous['sequence'] == sequence + 1
    assert contiguous['price'] == price
    assert contiguous['sequence_gap'] is False

    assert hubs[0].ingest(event(sequence + 3), authorized=True)['accepted']
    gap = hubs[1].latest()['items']['600000']
    assert gap['sequence'] == sequence + 3
    assert gap['price'] == price
    assert gap['sequence_gap'] is True
    assert gap['quality'] == 'delayed'
    stored = json.loads(redis_client.xrevrange('lua-test:stream', count=1)[0][1]['event'])
    assert stored['sequence'] == sequence + 3
    assert stored['price'] == price
    assert stored['sequence_gap'] is True

    older = event(sequence + 4, now - timedelta(seconds=1))
    assert hubs[1].ingest(older, authorized=True)['reason'] == 'provider_time_out_of_order'
    assert hubs[1].latest()['items']['600000']['sequence'] == sequence + 3


def test_redis_lua_orders_opaque_provider_ids_at_equal_timestamp(redis_client):
    now = datetime(2026, 11, 9, 2, tzinfo=timezone.utc)
    hub = QuoteHub(now_fn=lambda: now,
                   store=RedisQuoteStore('', prefix='opaque-test', client=redis_client))

    def event(order, price):
        return {'code': '600000', 'price': price, 'provider': 'futu',
                'provider_time': now.isoformat(), 'provider_sequence': str(order)}

    assert hub.ingest(event(9007199254740991, 10.1), authorized=True)['accepted']
    assert hub.ingest(event(9007199254740992, 10.2), authorized=True)['accepted']
    assert hub.ingest(event(9007199254740992, 10.3), authorized=True)['reason'] == (
        'provider_time_out_of_order'
    )
