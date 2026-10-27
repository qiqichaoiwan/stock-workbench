"""Server-owned evaluation context. No live adapter is installed in this release.

HTTP body fields never populate calendar, verified_snapshot or provider identity.
A future adapter must verify its source before supplying these internal values.
"""
from datetime import datetime
from .dual_core_v2 import CN
from ..portfolio.dual_core_checklist import day_guard


def evaluation_context():
    return {'now': datetime.now(CN), 'calendar': None, 'verified_snapshot': None}


def evaluate_server_snapshot(db, user_id, context, fallback):
    """Consume only server-verified snapshots; absent adapter is not a new failure."""
    from .dual_core_v2 import evaluate
    from ..portfolio.dual_core_checklist import record_verified_event
    now=context['now'].astimezone(CN)
    snapshot=context.get('verified_snapshot')
    if context.get('source_verified') is not True or snapshot is None:
        return fallback()
    result=evaluate(snapshot, now=now, calendar=context.get('calendar'))
    # Validated exchange calendar + actual checking window, not just a weekday.
    minute=now.hour*60+now.minute
    due=result['calendar_status']=='open' and (minute==570 or 870<=minute<=885)
    reference=context.get('reference')
    reason=None
    if result['source_missing']:
        reason='data_stale' if any('.timestamp' in key or '.final_snapshot' in key for key in result['source_missing']) else 'data_missing'
    elif result['decision']['step'] in (1,2,3,4):
        reason={1:'clearance',2:'circuit_breaker',3:'clearance',4:'stop_loss'}[result['decision']['step']]
    if due and reason and isinstance(reference,str) and 1<=len(reference)<=128:
        record_verified_event(db,user_id,now.date(),reason=reason,source='market_gateway',
                              reference=reference,observed_at=now.isoformat())
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
