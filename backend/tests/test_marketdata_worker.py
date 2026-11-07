import asyncio

import pytest

from app.marketdata.worker import (
    MarketDataWorker,
    WorkerConfigurationError,
)
from app.marketdata import worker as worker_module


class FakeProvider:
    symbols = ["SH.600000"]

    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def health(self):
        return {
            "provider": "futu",
            "state": "connected",
            "message_age_seconds": 0.1,
            "reconnects": 0,
        }


class FakeStore:
    def __init__(self):
        self.health_payloads = []

    def set_provider_health(self, payload):
        self.health_payloads.append(payload)


class FakeHub:
    def __init__(self, store):
        self._store = store

    def health(self):
        return {
            "status": "ok",
            "shared_storage": {"connected": True},
        }


def configure(monkeypatch):
    monkeypatch.setattr(worker_module.settings, "marketdata_stream_enabled", True)
    monkeypatch.setattr(worker_module.settings, "marketdata_process_role", "worker")
    monkeypatch.setattr(worker_module.settings, "futu_enabled", True)
    monkeypatch.setattr(worker_module.settings, "marketdata_single_tenant", True)
    monkeypatch.setattr(worker_module.settings, "marketdata_storage_mode", "redis")
    monkeypatch.setattr(worker_module.settings, "marketdata_redis_url", "redis://redis:6379/2")
    monkeypatch.setenv("MARKETDATA_AUTHORIZED_PROVIDERS", "futu")


def test_worker_validates_and_publishes_provider_heartbeat(monkeypatch):
    configure(monkeypatch)
    provider = FakeProvider()
    store = FakeStore()
    worker = MarketDataWorker(provider=provider, hub=FakeHub(store))

    report = worker.start()
    assert report["ok"] is True
    assert provider.started == 1
    assert store.health_payloads[-1]["provider"] == "futu"
    worker.stop()
    assert provider.stopped == 1
    assert store.health_payloads[-1]["state"] == "stopped"


def test_worker_rejects_memory_mode_in_production(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(worker_module.settings, "marketdata_storage_mode", "memory")
    with pytest.raises(WorkerConfigurationError, match="MARKETDATA_STORAGE_MODE=redis"):
        MarketDataWorker(provider=FakeProvider(), hub=FakeHub(FakeStore())).validate()


def test_worker_dry_run_does_not_start_provider_or_require_redis(monkeypatch):
    configure(monkeypatch)
    provider = FakeProvider()
    worker = MarketDataWorker(provider=provider, hub=FakeHub(FakeStore()), dry_run=True)

    result = worker.run()

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert provider.started == 0


def test_worker_requires_explicit_single_tenant_and_allowlist(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(worker_module.settings, "marketdata_single_tenant", False)
    monkeypatch.setenv("MARKETDATA_AUTHORIZED_PROVIDERS", "")
    with pytest.raises(WorkerConfigurationError) as exc:
        MarketDataWorker(provider=FakeProvider(), hub=FakeHub(FakeStore())).validate()
    assert "MARKETDATA_SINGLE_TENANT=true" in str(exc.value)
    assert "MARKETDATA_AUTHORIZED_PROVIDERS 包含 futu" in str(exc.value)


def test_worker_requires_worker_process_role(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(worker_module.settings, "marketdata_process_role", "api")
    with pytest.raises(WorkerConfigurationError, match="MARKETDATA_PROCESS_ROLE=worker"):
        MarketDataWorker(provider=FakeProvider(), hub=FakeHub(FakeStore())).validate()


def test_worker_rejects_invalid_marketdata_symbol(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(worker_module.settings, "marketdata_symbols", "600000,abc")
    with pytest.raises(WorkerConfigurationError, match="MARKETDATA_SYMBOLS"):
        MarketDataWorker(provider=FakeProvider(), hub=FakeHub(FakeStore())).validate()


def test_fastapi_lifespan_rejects_worker_role_and_api_does_not_start_opend(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_startup", lambda: None)

    async def enter_lifespan():
        async with main.lifespan(main.app):
            return True

    monkeypatch.setattr(main.settings, "marketdata_process_role", "worker")
    with pytest.raises(RuntimeError, match="MARKETDATA_PROCESS_ROLE=api"):
        asyncio.run(enter_lifespan())

    monkeypatch.setattr(main.settings, "marketdata_process_role", "api")
    assert asyncio.run(enter_lifespan()) is True
