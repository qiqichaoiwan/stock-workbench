"""Manual execution-card persistence, not an order execution service."""
import json
import math
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .dual_core_checklist_models import ExecutionCard, CardAudit, VerifiedDayEvent
from zoneinfo import ZoneInfo

Phase = Literal['data_check', 'preview', 'final', 'weekly_review']


class CardInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)
    evidence: dict = Field(default_factory=dict)
    account: dict = Field(default_factory=dict)
    candidates: list[dict] = Field(default_factory=list, max_length=100)
    holdings: list[dict] = Field(default_factory=list, max_length=100)
    checklist: dict = Field(default_factory=dict)
    trades: list[dict] = Field(default_factory=list, max_length=100)
    next_day_plan: dict = Field(default_factory=dict)
    review: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_fields(self):
        # Calculation payload adds a known wrapper and MACD array nesting. Keep
        # ordinary notes at depth six; validate this bounded subtree separately.
        data = self.model_dump()
        calc = data['evidence'].pop('calculation_input', {})
        bounded_json(data)
        bounded_json(calc)
        if not isinstance(calc, dict):
            raise ValueError('calculation_input must be object')
        if calc.keys() - {'trade_date', 'phase', 'evidence', 'account', 'holdings', 'candidates'}:
            raise ValueError('unknown calculation field')
        for key in ('account', 'evidence'):
            if key in calc and not isinstance(calc[key], dict):
                raise ValueError('calculation objects required')
        for key in ('holdings', 'candidates'):
            if key in calc and (not isinstance(calc[key], list) or len(calc[key]) > 100 or any(not isinstance(row, dict) for row in calc[key])):
                raise ValueError('calculation rows required')
        allowed_evidence = {'source', 'captured_at', 'indices', 'emotion', 'industry',
                            'stocks', 'data_quality', 'calculation_input'}
        if self.evidence.keys() - allowed_evidence:
            raise ValueError('unknown evidence section')
        for key, value in self.evidence.items():
            if key not in {'source', 'captured_at'} and not isinstance(value, dict):
                raise ValueError('evidence sections must be objects')
        calc = self.evidence.get('calculation_input', {})
        if 'evidence' in calc and not isinstance(calc['evidence'], dict):
            raise ValueError('calculation evidence must be object')
        if 'captured_at' in self.evidence:
            if not isinstance(self.evidence['captured_at'], str):
                raise ValueError('captured_at must be ISO string')
            stamp = datetime.fromisoformat(self.evidence['captured_at'])
            if stamp.tzinfo is None:
                raise ValueError('captured_at requires timezone')
        if self.evidence.get('source', 'manual') != 'manual':
            raise ValueError('public writes are manual evidence only')
        for key, value in self.account.items():
            if value is None:
                continue
            if key.endswith('_pct') or key in {'daily_pnl', 'weekly_pnl', 'weight', 'total_weight'}:
                number(value, -100 if 'pnl' in key else 0, 100)
            if key in {'consecutive_losses', 'holding_count', 'frozen_sessions'}:
                number(value, 0, 10000, integer=True)
        for value in self.checklist.values():
            if value is not None and not isinstance(value, (bool, dict)):
                raise ValueError('checklist values must be boolean, null or structured checks')
        for row in self.candidates + self.holdings + self.trades:
            if 'code' in row and not re.fullmatch(r'[0-9]{6}', str(row['code'])):
                raise ValueError('code must have six digits')
            for key in ('price', 'quantity', 'shares', 'amount', 'position_pct', 'weight'):
                if key in row and row[key] is not None:
                    number(row[key], 0, 100 if key in {'position_pct', 'weight'} else 1e15)
        groups = [self.account, *self.candidates, *self.holdings,
                  self.evidence.get('emotion', {}), self.evidence.get('industry', {})]
        groups += [v for v in self.evidence.get('indices', {}).values() if isinstance(v, dict)]
        cev = calc.get('evidence', {})
        if cev.keys() - {'cores', 'emotion', 'industry', 'stock'}:
            raise ValueError('unknown calculation evidence section')
        if any(not isinstance(v, dict) for v in cev.values()):
            raise ValueError('calculation evidence sections must be objects')
        if 'cores' in cev and (cev['cores'].keys() - {'883957', '883994'} or any(not isinstance(v, dict) for v in cev['cores'].values())):
            raise ValueError('core evidence objects required')
        if isinstance(cev.get('cores'), dict):
            groups += [v for v in cev['cores'].values() if isinstance(v, dict)]
        groups += [v for k, v in cev.items() if k != 'cores' and isinstance(v, dict)]
        groups += [calc.get('account', {}), *calc.get('holdings', []), *calc.get('candidates', [])]
        booleans = set('wma20_up4 macd_dead_cross black_swan is_core leader1 full_resonance high_upper_shadow high_volume_bear stagnation one_price_limit_up ma5_bad bought_today limit_down_locked limit_up_open stopped_today data_failure_today review_complete two_month_low_winrate'.split())
        numerics = set('price prev_high prev_low high60 ma5 ma10 ma20 ma5_prev wma5 wma10 wma20 w_pct below_ma5_days high pct_chg pct_chg_high limit_up broken limit_down height amount amount_ma5 high20w previous_high20w rank amount_ma20 gain5 above_ma5_days open low prev_close prev_price prev_ma5 volume_ratio w_close wma30 macd_cross_weeks dif float_cap amount_avg limit_down5 return20 boards box_range20 prior_high20 n_bear_age n_bear_high daily_pnl weekly_pnl consecutive_losses total_weight frozen_sessions weight pnl'.split())
        signed = {'w_pct', 'pct_chg', 'pct_chg_high', 'gain5', 'dif', 'return20', 'daily_pnl', 'weekly_pnl', 'pnl'}
        counts = set('below_ma5_days limit_up broken limit_down height rank above_ma5_days macd_cross_weeks limit_down5 boards n_bear_age consecutive_losses frozen_sessions'.split())
        from ..screener.dual_core_v2 import validate_positive_prices
        for group in groups:
            validate_positive_prices(group)
            for key, value in group.items():
                if value is None:
                    continue
                if key in booleans and type(value) is not bool:
                    raise ValueError('boolean or null required')
                if key in numerics:
                    number(value, -1e15 if key in signed else 0, 100 if key in {'weight', 'total_weight'} else 1e15, integer=key in counts)
                if key == 'macd_hist':
                    if not isinstance(value, list) or len(value) > 300:
                        raise ValueError('bounded MACD numeric array required')
                    for item in value:
                        if item is not None:
                            number(item, -1e15, 1e15)
        return self


def number(value, lower, upper, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError('numeric field outside range')
    if integer and type(value) is not int:
        raise ValueError('integer required')


def bounded_json(value, depth=0):
    if depth > 6:
        raise ValueError('maximum JSON depth is six')
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError('too many object fields')
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                raise ValueError('invalid field name')
            normalized = key.lower().replace('-', '_')
            if (normalized != 'frozen_sessions' and any(s in normalized for s in ('password', 'secret', 'token', 'session', 'api_key', 'apikey', 'authorization', 'cookie'))) or normalized == 'key':
                raise ValueError('credential fields forbidden')
            bounded_json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 300:
            raise ValueError('too many array items')
        for item in value:
            bounded_json(item, depth + 1)
    elif isinstance(value, str):
        if len(value) > 4096:
            raise ValueError('text exceeds 4096 characters')
    elif type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError('non-finite number')
    elif value is not None and type(value) is not bool:
        raise ValueError('invalid JSON value')


def day_guard(db, user_id, trade_date):
    events = db.scalars(select(VerifiedDayEvent).where(VerifiedDayEvent.user_id == user_id,
        VerifiedDayEvent.trade_date == str(trade_date)).order_by(VerifiedDayEvent.id)).all()
    return {'frozen': bool(events), 'reasons': sorted({e.reason for e in events}),
            'events': [dict(reason=e.reason, source=e.source, reference=e.reference,
                            observed_at=e.observed_at, recorded_at=e.recorded_at) for e in events]}


def record_verified_event(db, user_id, trade_date, *, reason, source, reference, observed_at):
    """INTERNAL ONLY: caller must validate original provider evidence before calling.

    No HTTP endpoint exposes this method; public manual fields never reach it.
    Append-only events intentionally survive card edits/deletions and data recovery.
    """
    if source not in {'market_gateway', 'verified_trade_ledger', 'dual_core_engine'}:
        raise ValueError('untrusted source')
    if reason not in {'data_missing', 'data_stale', 'stop_loss', 'clearance', 'circuit_breaker'}:
        raise ValueError('unknown freeze reason')
    observed = datetime.fromisoformat(observed_at)
    if observed.tzinfo is None or str(observed.astimezone(ZoneInfo('Asia/Shanghai')).date()) != str(trade_date):
        raise ValueError('observation must belong to this Shanghai trade date')
    if not isinstance(reference, str) or not 1 <= len(reference) <= 128:
        raise ValueError('provider reference required')
    existing = db.scalar(select(VerifiedDayEvent).where(VerifiedDayEvent.user_id == user_id,
        VerifiedDayEvent.trade_date == str(trade_date), VerifiedDayEvent.reason == reason,
        VerifiedDayEvent.source == source, VerifiedDayEvent.reference == reference))
    if existing is None:
        db.add(VerifiedDayEvent(user_id=user_id, trade_date=str(trade_date), reason=reason,
            source=source, reference=reference, observed_at=observed.isoformat(),
            recorded_at=datetime.now(timezone.utc).isoformat()))
        db.commit()
    return day_guard(db, user_id, trade_date)


def find_card(db, user_id, trade_date, phase):
    return db.scalar(select(ExecutionCard).where(ExecutionCard.user_id == user_id,
        ExecutionCard.trade_date == str(trade_date), ExecutionCard.phase == phase))


def manual_calculation(payload, recorded_at):
    """Use the same public/manual boundary as the UI, never a trusted feed."""
    from ..screener.dual_core_v2 import evaluate_v2
    candidate = json.loads(json.dumps(payload.get('evidence', {}).get('calculation_input', {})))
    # Card-level editable state is canonical, not a possibly stale nested copy.
    for key in ('account', 'holdings', 'candidates'):
        candidate[key] = payload.get(key, {} if key == 'account' else [])
    return evaluate_v2(candidate, now=datetime.fromisoformat(recorded_at))


def serialize(db, card):
    guard = day_guard(db, card.user_id, card.trade_date)
    times = {'data_check': ('09:30', 'data_integrity'), 'preview': ('14:30', 'warning_only'),
             'final': ('14:45', 'manual_record_only'), 'weekly_review': ('Friday 15:00+', 'next_trade_review')}
    time, purpose = times[card.phase]
    return dict(trade_date=card.trade_date, phase=card.phase, revision=card.revision,
        **json.loads(card.payload), provenance='manual', decision_status='blocked' if guard['frozen'] else 'unverified',
        calendar_status='unverified', effective_trade_date=None,
        schedule={'timezone': 'Asia/Shanghai', 'time': time, 'purpose': purpose},
        automatic_evidence=[], calculation=manual_calculation(json.loads(card.payload), card.updated_at),
        day_guard=guard, created_at=card.created_at, updated_at=card.updated_at)


def save_card(db, user_id, trade_date, phase, body):
    card = find_card(db, user_id, trade_date, phase)
    if body.revision != (card.revision if card else 0):
        raise HTTPException(409, 'revision conflict')
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(body.model_dump(exclude={'revision'}), ensure_ascii=False)
    action = 'update' if card else 'create'
    try:
        if card is None:
            card = ExecutionCard(user_id=user_id, trade_date=str(trade_date), phase=phase,
                revision=1, payload=payload, created_at=now, updated_at=now, deleted=False)
            db.add(card)
            db.flush()
        else:
            result = db.execute(update(ExecutionCard).where(ExecutionCard.id == card.id,
                ExecutionCard.revision == body.revision).values(revision=body.revision + 1,
                payload=payload, updated_at=now, deleted=False))
            if result.rowcount != 1:
                db.rollback()
                raise HTTPException(409, 'revision conflict')
            db.refresh(card)
        add_audit(db, card, action)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'revision conflict') from None
    return serialize(db, card)


def add_audit(db, card, action):
    db.add(CardAudit(user_id=card.user_id, trade_date=card.trade_date, phase=card.phase,
        revision=card.revision, action=action, recorded_at=card.updated_at,
        snapshot=json.dumps(serialize(db, card), ensure_ascii=False)))


def audit(db, card):
    rows = db.scalars(select(CardAudit).where(CardAudit.user_id == card.user_id,
        CardAudit.trade_date == card.trade_date, CardAudit.phase == card.phase)
        .order_by(CardAudit.revision)).all()
    return [dict(revision=r.revision, action=r.action, recorded_at=r.recorded_at,
                 snapshot=json.loads(r.snapshot)) for r in rows]


def delete_card(db, card, revision):
    result = db.execute(update(ExecutionCard).where(ExecutionCard.id == card.id,
        ExecutionCard.revision == revision, ExecutionCard.deleted.is_(False)).values(
        revision=revision + 1, deleted=True, updated_at=datetime.now(timezone.utc).isoformat()))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, 'revision conflict')
    db.refresh(card)
    add_audit(db, card, 'delete')
    db.commit()
    return {'deleted': True, 'revision': card.revision}
