"""受保护的实时行情事件接入与质量查询 API。"""
from __future__ import annotations

import hmac
import os
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .hub import authorized_providers_from_env, get_hub
from .health import evaluate_slo
from ..config import settings
from ..shared.security import current_user_id

router = APIRouter(prefix="/api/marketdata", tags=["marketdata"])


class QuoteEventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=32)
    price: float
    pct_chg: float | None = None
    provider: str = Field(min_length=1, max_length=64)
    provider_time: str
    received_at: str | None = None
    sequence: int | None = Field(default=None, ge=0)
    # A provider may explicitly prove that its sequence baseline was rebuilt
    # after a gap.  These fields are opt-in diagnostics; the server still
    # derives the session and authorization gates itself.
    sequence_contiguous: bool | None = None
    sequence_resync: bool | None = None
    session: str | None = None
    # 仅用于事件审计。HTTP 接入最终以 MARKETDATA_AUTHORIZED_PROVIDERS 为准。
    authorization: bool = False


class QuoteIngestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[QuoteEventIn] = Field(min_length=1, max_length=1000)


_RATE_LOCK = threading.Lock()
_RATE_WINDOWS: dict[str, tuple[float, int, int]] = {}
_REQUESTS_PER_MINUTE = 120
_EVENTS_PER_MINUTE = 10_000


def _check_ingest_token(request: Request) -> None:
    configured = (os.environ.get("MARKETDATA_INGEST_TOKEN", "").strip()
                  or settings.marketdata_ingest_token.strip())
    if not configured:
        raise HTTPException(503, "MARKETDATA_INGEST_TOKEN 未配置，已关闭行情写入口")
    supplied = request.headers.get("x-marketdata-token", "")
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(401, "行情写入口令无效")


def _check_ingest_rate(request: Request, event_count: int) -> None:
    token = request.headers.get("x-marketdata-token", "")
    # Do not retain the bearer token itself in process memory or diagnostics.
    key = hmac.new(b"marketdata-rate", token.encode(), "sha256").hexdigest()
    now = time.monotonic()
    with _RATE_LOCK:
        started, requests, events = _RATE_WINDOWS.get(key, (now, 0, 0))
        if now - started >= 60:
            started, requests, events = now, 0, 0
        if requests >= _REQUESTS_PER_MINUTE or events + event_count > _EVENTS_PER_MINUTE:
            raise HTTPException(429, "行情写入口频率超限，请降低批量频率")
        _RATE_WINDOWS[key] = (started, requests + 1, events + event_count)


@router.post("/ingest")
def ingest(body: QuoteIngestBody, request: Request):
    _check_ingest_token(request)
    if not settings.marketdata_stream_enabled:
        raise HTTPException(503, "实时行情接入未启用，请设置 MARKETDATA_STREAM_ENABLED=true")
    if any(event.provider == "futu" for event in body.events) and not settings.marketdata_single_tenant:
        raise HTTPException(403, "富途个人行情仅允许显式单租户部署")
    _check_ingest_rate(request, len(body.events))
    result = get_hub().ingest_many(
        [event.model_dump() for event in body.events],
        authorization_providers=authorized_providers_from_env(),
    )
    return {**result, "health": get_hub().health()}


@router.get("/latest")
def latest(codes: str = "", user_id: int = Depends(current_user_id)):
    wanted = [item.strip() for item in codes.split(",") if item.strip()]
    return get_hub().latest(wanted or None)


@router.get("/health")
def health(user_id: int = Depends(current_user_id)):
    from .futu import get_provider

    hub = get_hub()
    hub_health = hub.health()
    adapter_health = get_provider().health()
    slo = evaluate_slo(
        hub_health,
        hub.latest(),
        adapter_health,
        stream_enabled=settings.marketdata_stream_enabled,
        storage_mode=settings.marketdata_storage_mode,
        max_age_seconds=settings.marketdata_max_age_seconds,
        delayed_max_age_seconds=settings.marketdata_delayed_max_age_seconds,
        expected_symbols=settings.marketdata_symbols.split(","),
    )
    # ``sla_ready`` remains False for the contractual exchange-SLA boundary;
    # ``slo_ready`` is the local application readiness gate for operators.
    return {**hub_health,
            "stream_enabled": settings.marketdata_stream_enabled,
            "adapter": adapter_health,
            "slo_ready": slo["ready"],
            "slo": slo}
