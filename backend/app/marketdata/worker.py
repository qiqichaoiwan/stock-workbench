"""Standalone Futu OpenD market-data worker.

The FastAPI process owns the application scheduler and business database.  This
module is deliberately runnable as a separate process so an OpenD connection
can be restarted independently and publish only normalized events to the
shared Redis latest/Streams store.  It never imports or starts ``app.main``.

Production mode is intentionally strict: Redis must be configured and healthy,
Futu must be explicitly enabled as a single-tenant provider, and ``futu`` must
be present in the server-side authorization allow-list.  ``--dry-run`` checks
configuration without importing/connecting to the optional SDK and is useful
for container validation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import logging
import re
import signal
import threading
from typing import Any

from ..config import settings
from .futu import FutuProvider, get_provider
from .hub import QuoteHub, authorized_providers_from_env, get_hub

UTC = timezone.utc
log = logging.getLogger("marketdata.worker")


class WorkerConfigurationError(RuntimeError):
    """Raised when the worker would run without a safe production contract."""


class WorkerRuntimeError(RuntimeError):
    """Raised when the worker loses its required shared storage."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class MarketDataWorker:
    """Supervise one Futu provider and publish health alongside quote events."""

    def __init__(
        self,
        *,
        provider: FutuProvider | Any | None = None,
        hub: QuoteHub | Any | None = None,
        stop_event: threading.Event | None = None,
        health_interval_seconds: float = 10.0,
        require_redis: bool = True,
        dry_run: bool = False,
    ) -> None:
        self.provider = provider or get_provider()
        self.hub = hub or get_hub()
        self.stop_event = stop_event or threading.Event()
        self.health_interval_seconds = max(1.0, float(health_interval_seconds))
        self.require_redis = bool(require_redis)
        self.dry_run = bool(dry_run)
        self._started = False

    def _configuration_errors(self, *, check_storage: bool) -> list[str]:
        errors: list[str] = []
        if settings.marketdata_process_role != "worker":
            errors.append("MARKETDATA_PROCESS_ROLE=worker")
        if not settings.marketdata_stream_enabled:
            errors.append("MARKETDATA_STREAM_ENABLED=true")
        if not settings.futu_enabled:
            errors.append("FUTU_ENABLED=true")
        if not settings.marketdata_single_tenant:
            errors.append("MARKETDATA_SINGLE_TENANT=true")
        if "futu" not in authorized_providers_from_env():
            errors.append("MARKETDATA_AUTHORIZED_PROVIDERS 包含 futu")
        configured_symbols = [item.strip().upper()
                             for item in settings.marketdata_symbols.split(",")
                             if item.strip()]
        invalid_symbols = [item for item in configured_symbols
                           if not (re.fullmatch(r"[0-9]{1,6}", item) or
                                   re.fullmatch(r"(?:SH|SZ)\.[0-9]{6}", item))]
        invalid_symbols.extend(str(item) for item in
                              (getattr(self.provider, "invalid_symbols", []) or []))
        if invalid_symbols:
            errors.append("MARKETDATA_SYMBOLS 使用最多六位 ASCII 数字或 SH./SZ. 六位代码")
        symbols = list(getattr(self.provider, "symbols", []) or [])
        if not symbols:
            errors.append("MARKETDATA_SYMBOLS 至少包含一个标的")
        # The base API image intentionally omits futu-api.  A real provider
        # worker must fail before opening any OpenD thread when the optional
        # SDK was not installed; injected fakes used by tests/local dry-runs
        # do not need the package.
        if isinstance(self.provider, FutuProvider) and not self.dry_run:
            try:
                import futu  # type: ignore  # noqa: F401
            except ImportError:
                errors.append("安装 backend/requirements-futu.txt (futu-api)")
        if self.require_redis:
            if settings.marketdata_storage_mode != "redis":
                errors.append("MARKETDATA_STORAGE_MODE=redis")
            if not settings.marketdata_redis_url.strip():
                errors.append("MARKETDATA_REDIS_URL 已配置")
            if check_storage:
                health = self.hub.health()
                shared = health.get("shared_storage") or {}
                if not shared.get("connected"):
                    errors.append("Redis shared storage connected")
        return errors

    def validate(self) -> dict[str, Any]:
        """Validate configuration and return a safe, non-secret report."""
        errors = self._configuration_errors(check_storage=not self.dry_run)
        report = {
            "ok": not errors,
            "dry_run": self.dry_run,
            "provider": "futu",
            "symbols": list(getattr(self.provider, "symbols", []) or []),
            "storage_mode": settings.marketdata_storage_mode,
            "require_redis": self.require_redis,
            "errors": errors,
        }
        if errors:
            raise WorkerConfigurationError("; ".join(errors))
        return report

    def _publish_provider_health(self, provider_health: dict[str, Any]) -> None:
        """Publish a sanitized provider heartbeat when Redis is configured."""
        store = getattr(self.hub, "_store", None)
        setter = getattr(store, "set_provider_health", None)
        if not callable(setter):
            return
        payload = {**provider_health, "updated_at": _utc_now()}
        try:
            setter(payload)
        except Exception as exc:  # noqa: BLE001 - heartbeat must not kill provider
            log.warning("写入 provider health 失败: %s", type(exc).__name__)

    def health(self) -> dict[str, Any]:
        provider_health = dict(self.provider.health())
        hub_health = self.hub.health()
        self._publish_provider_health(provider_health)
        return {"provider": provider_health, "hub": hub_health, "as_of": _utc_now()}

    def start(self) -> dict[str, Any]:
        report = self.validate()
        if self.dry_run:
            return report
        self.provider.start()
        self._started = True
        self.health()
        log.info("Futu market-data worker started symbols=%s", report["symbols"])
        return report

    def stop(self) -> None:
        self.stop_event.set()
        if self._started:
            self.provider.stop()
            self._started = False
            # Mark an orderly shutdown immediately.  Otherwise the API would
            # observe the last ``connected`` heartbeat until its timeout even
            # though this worker has already released the OpenD connection.
            stopped = dict(self.provider.health())
            stopped.update({"state": "stopped", "enabled": False,
                            "error": "worker_stopped"})
            self._publish_provider_health(stopped)
            log.info("Futu market-data worker stopped")

    def run(self) -> dict[str, Any]:
        """Run until SIGTERM/stop_event; fail fast if Redis becomes unavailable."""
        report = self.start()
        if self.dry_run:
            return report
        try:
            while not self.stop_event.wait(self.health_interval_seconds):
                status = self.health()
                shared = (status["hub"].get("shared_storage") or {})
                if self.require_redis and not shared.get("connected"):
                    raise WorkerRuntimeError("Redis shared market-data storage unavailable")
                log.info(
                    "market-data heartbeat state=%s age=%s reconnects=%s",
                    status["provider"].get("state"),
                    status["provider"].get("message_age_seconds"),
                    status["provider"].get("reconnects"),
                )
            return self.health()
        finally:
            self.stop()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the standalone Futu OpenD market-data worker")
    parser.add_argument("--dry-run", action="store_true", help="validate configuration without connecting OpenD/Redis")
    parser.add_argument("--allow-memory", action="store_true", help="allow memory mode for local development (never use for SLA)")
    parser.add_argument("--health-interval", type=float, default=10.0, help="provider heartbeat interval in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    worker = MarketDataWorker(
        health_interval_seconds=args.health_interval,
        require_redis=not args.allow_memory,
        dry_run=args.dry_run,
    )

    def stop(_signum: int, _frame: Any) -> None:
        worker.stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is not None:
            signal.signal(signum, stop)
    try:
        result = worker.run()
        if args.dry_run:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (WorkerConfigurationError, WorkerRuntimeError) as exc:
        log.error("worker stopped: %s", exc)
        return 2
    except KeyboardInterrupt:
        worker.stop()
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI process wrapper
    raise SystemExit(main())
