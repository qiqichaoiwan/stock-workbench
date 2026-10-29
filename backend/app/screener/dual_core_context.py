"""Server-owned evaluation context backed by the real-time feed adapter.

HTTP body fields never populate calendar, verified_snapshot, provider identity
or the market precheck. Only ``dual_core_feed.collect`` may supply these values;
a failed/disabled feed falls back to stored daily history (still unverified).
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import dual_core_feed
from .dual_core_v2 import CN
from ..config import settings
from ..portfolio.dual_core_checklist import day_guard

log = logging.getLogger("dual_core_context")


def evaluation_clock() -> datetime:
    """Server clock only; used by manual endpoints that must not hit the feed."""
    return datetime.now(CN)


def evaluation_context() -> dict:
    """Collect server-owned evidence once per request (adapter has 20s cache).

    Outside the live session, fall back to the latest completed close snapshot
    for review. That snapshot is explicitly marked non-realtime and can never
    authorize opening.
    """
    now = evaluation_clock()
    if not settings.dual_core_feed_enabled:
        return {"now": now, "calendar": None, "verified_snapshot": None,
                "source_verified": False, "reference": None,
                "market_precheck": None, "feed_status": "disabled", "errors": []}
    try:
        feed = dual_core_feed.collect(now)
    except Exception:  # noqa: BLE001 采集异常必须降级,不能带崩只读接口
        log.exception("dual core feed failed")
        return {"now": now, "calendar": None, "verified_snapshot": None,
                "source_verified": False, "reference": None,
                "market_precheck": None, "feed_status": "error",
                "errors": ["unexpected_feed_error"]}
    for error in feed.get("errors") or []:
        log.warning("dual core feed: %s", error)
    snapshot = feed.get("snapshot")
    verified = feed.get("source_verified") is True and snapshot is not None
    if verified:
        return {"now": feed.get("now") or now,
                "calendar": feed.get("calendar"),
                "verified_snapshot": snapshot,
                "source_verified": True,
                "reference": feed.get("reference"),
                "market_precheck": feed.get("market_precheck"),
                "server_missing": list(feed.get("server_missing") or []),
                "feed_status": "verified",
                "errors": list(feed.get("errors") or [])}

    # 只在明确不属于实时窗口时取收盘快照;盘中采集失败不能用旧数据补成“实时”。
    errors = list(feed.get("errors") or [])
    review_allowed = (now.time() > dual_core_feed.SESSION_END
                      or "outside_session" in errors or "not_trade_day" in errors)
    if review_allowed:
        try:
            review = dual_core_feed.collect_review(now)
        except Exception:  # noqa: BLE001 复核同样必须降级
            log.exception("dual core review failed")
            review = None
        if review and review.get("source_verified") is True and review.get("snapshot"):
            for error in review.get("errors") or []:
                log.warning("dual core review: %s", error)
            return {"now": now,
                    "evaluation_at": review.get("review_at"),
                    "calendar": review.get("calendar"),
                    "verified_snapshot": review["snapshot"],
                    "source_verified": True,
                    "reference": review.get("reference"),
                    "market_precheck": review.get("market_precheck"),
                    "server_missing": list(review.get("server_missing") or []),
                    "feed_status": "closed",
                    "feed_trade_date": review.get("data_trade_date"),
                    "review_only": True,
                    "errors": list(review.get("errors") or [])}
    return {"now": feed.get("now") or now,
            "calendar": feed.get("calendar"),
            "verified_snapshot": None,
            "source_verified": False,
            "reference": None,
            "market_precheck": None,
            "server_missing": [],
            "feed_status": "unavailable",
            "errors": errors}


def _server_key(key) -> bool:
    """Only cores/emotion are server-owned; user domains must not freeze the day."""
    text = str(key)
    return text.startswith("cores.") or text == "emotion" or text.startswith("emotion.")


def evaluate_server_snapshot(db, user_id, context, fallback):
    """Consume only server-verified snapshots; absent adapter is not a new failure."""
    from .dual_core_v2 import evaluate
    from ..portfolio.dual_core_checklist import record_verified_event
    now = context["now"].astimezone(CN)
    snapshot = context.get("verified_snapshot")
    if context.get("source_verified") is not True or snapshot is None:
        result = fallback()
        if isinstance(result, dict):
            result.setdefault("feed_status", context.get("feed_status") or "unavailable")
        return result
    review_only = context.get("feed_status") == "closed"
    eval_at = context.get("evaluation_at")
    if isinstance(eval_at, str):
        try:
            eval_at = datetime.fromisoformat(eval_at)
        except ValueError:
            eval_at = now
    eval_at = (eval_at or now).astimezone(CN)
    result = evaluate(snapshot, now=eval_at, calendar=context.get("calendar"))
    # Keep stored-history display fields (data/series/industry_top) for Step 1,
    # but the v2 checks/verdict/decision must stay authoritative.
    base = fallback()
    if isinstance(base, dict):
        merged = dict(base)
        merged.update(result)
        merged["legacy_analysis"] = base.get("legacy_analysis")
        merged.pop("message", None)
        result = merged
    precheck = context.get("market_precheck")
    if isinstance(precheck, dict) and isinstance(precheck.get("checks"), list):
        result["market_precheck"] = {key: value for key, value in precheck.items()
                                     if key != "checks"}
        result["checks"] = [*result.get("checks", []), *precheck["checks"]]
    declared = [key for key in (context.get("server_missing") or []) if _server_key(key)]
    combined = list(dict.fromkeys([*(result.get("source_missing") or []), *declared]))
    result["source_missing"] = combined
    quality = result.setdefault("data_quality", {})
    quality["missing"] = list(combined)
    quality["complete"] = not combined
    server_missing = [key for key in combined if _server_key(key)]
    result["feed_status"] = ("closed" if review_only
                             else "partial" if server_missing else "verified")
    result["feed_reference"] = context.get("reference")
    result["feed_error_count"] = len(context.get("errors") or [])
    if review_only:
        # 收盘快照只供复核:数据时间仍是收盘时点,计算时间/核验状态必须显示为非实时。
        result["feed_trade_date"] = context.get("feed_trade_date")
        result["review_only"] = True
        result["as_of"] = now.isoformat()
        decision = result.get("decision")
        if isinstance(decision, dict):
            decision["can_open"] = False
            if decision.get("action") in ("open", "add", "observe", "manual_reference_only"):
                decision["action"] = "manual_reference_only"
                decision["reason"] = "market closed; review snapshot only"
    # Validated exchange calendar + actual checking window, not just a weekday.
    minute = now.hour * 60 + now.minute
    due = (not review_only and result["calendar_status"] == "open"
           and (minute == 570 or 870 <= minute <= 885))
    reference = context.get("reference")
    reason = None
    if server_missing:
        reason = "data_stale" if any(
            ".timestamp" in key or ".final_snapshot" in key for key in server_missing
        ) else "data_missing"
    elif result["decision"]["step"] in (1, 2, 3, 4):
        reason = {1: "clearance", 2: "circuit_breaker", 3: "clearance", 4: "stop_loss"}[
            result["decision"]["step"]
        ]
    if due and reason and isinstance(reference, str) and 1 <= len(reference) <= 128:
        record_verified_event(db, user_id, now.date(), reason=reason, source="market_gateway",
                              reference=reference, observed_at=now.isoformat())
    return result


def apply_day_guard(result, db, user_id, now):
    guard = day_guard(db, user_id, now.astimezone(CN).date())
    def apply(item):
        item['day_guard'] = guard
        if guard['frozen']:
            item['decision'].update(can_open=False)
            if item['decision']['action'] in ('open', 'add', 'observe', 'manual_reference_only'):
                item['decision'].update(action='block', reason='verified day freeze')
        if 'evidence_analysis' in item: apply(item['evidence_analysis'])
        for candidate in item.get('candidate_results', []): apply(candidate)
    apply(result)
    return result
