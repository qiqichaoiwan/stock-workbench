"""Isolated execution-card API tests; never launch main lifespan/scheduler."""
from datetime import date
import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def env():
    api = importlib.import_module('app.portfolio.dual_core_checklist_api')
    service = importlib.import_module('app.portfolio.dual_core_checklist')
    from app.db import PortfolioBase, get_db
    from app.shared.security import current_user_id
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    PortfolioBase.metadata.create_all(engine)
    db = Session(engine)
    user = {'id': 101}
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: user['id']
    with TestClient(app) as client:
        yield client, db, user, service
    db.close()
    engine.dispose()


BASE = '/api/portfolio/dual-core/cards'
PATH = BASE + '/2026-10-30/final'


def body(revision=0, **kw):
    return dict(revision=revision, evidence={}, account={}, candidates=[], holdings=[],
                checklist={}, trades=[], next_day_plan={}, review={}, **kw)


def test_create_read_and_day_phase_user_isolation(env):
    c, db, user, _ = env
    payload = body()
    payload['review'] = {'notes': 'manual note'}
    r = c.put(PATH, json=payload)
    assert r.status_code == 200, r.text
    card = r.json()
    assert card['revision'] == 1
    assert card['review']['notes'] == 'manual note'
    assert card['provenance'] == 'manual'
    assert card['decision_status'] == 'unverified'
    assert card['calendar_status'] == 'unverified'
    assert c.get(PATH).json() == card
    assert c.get(BASE, params={'trade_date': '2026-10-30'}).json()['cards'] == [card]
    assert c.get(BASE, params={'trade_date': '2026-10-29'}).json()['cards'] == []
    assert c.get(PATH.replace('final', 'preview')).status_code == 404
    user['id'] = 102
    assert c.get(PATH).status_code == 404
    assert c.get(BASE, params={'trade_date': '2026-10-30'}).json()['cards'] == []


def test_revision_audit_soft_delete_and_recreate(env):
    c, _, user, _ = env
    assert c.put(PATH, json=body()).status_code == 200
    assert c.put(PATH, json=body()).status_code == 409
    updated = body(1)
    updated['review'] = {'notes': 'changed'}
    assert c.put(PATH, json=updated).json()['revision'] == 2
    history = c.get(PATH + '/audit').json()['items']
    assert [h['revision'] for h in history] == [1, 2]
    assert history[0]['snapshot']['review'] == {}
    assert history[1]['snapshot']['review'] == {'notes': 'changed'}
    export = c.get(PATH + '/export')
    assert export.status_code == 200
    assert export.headers['content-type'].startswith('application/json')
    assert 'attachment' in export.headers['content-disposition']
    assert export.json()['card']['revision'] == 2
    assert len(export.json()['audit']) == 2
    assert c.delete(PATH, params={'revision': 1}).status_code == 409
    assert c.delete(PATH, params={'revision': 2}).status_code == 200
    assert c.get(PATH).status_code == 404
    assert c.get(BASE, params={'trade_date': '2026-10-30'}).json()['cards'] == []
    assert [h['action'] for h in c.get(PATH + '/audit').json()['items']] == ['create', 'update', 'delete']
    assert c.put(PATH, json=body()).status_code == 409
    assert c.put(PATH, json=body(3)).json()['revision'] == 4
    user['id'] = 102
    assert c.get(PATH + '/audit').status_code == 404
    assert c.get(PATH + '/export').status_code == 404


@pytest.mark.parametrize('field,value', [
    ('session', 'secret'), ('revision', True), ('revision', -1),
    ('account', {'total_position_pct': 101}), ('account', {'consecutive_losses': -1}),
    ('checklist', {'step0': 'yes'}), ('evidence', {'made_up_section': 1}),
    ('evidence', {'source': 'automatic'}), ('review', {'notes': 'x' * 5000}),
    ('review', {'nested': {'api_key': 'never store'}}),
    ('candidates', [{}] * 101), ('holdings', [{'code': '123'}]),
    ('trades', [{'code': '000001', 'price': -1}]),
    ('next_day_plan', {'session_key': 'no'}),
])
def test_invalid_schema_rejected_without_writes(env, field, value):
    c, _, _, _ = env
    payload = body()
    payload[field] = value
    assert c.put(PATH, json=payload).status_code == 422
    assert c.get(PATH).status_code == 404


def test_payload_size_and_depth_limits(env):
    c, _, _, _ = env
    payload = body()
    payload['review'] = {'notes': 'x' * 140000}
    assert c.put(PATH, json=payload).status_code == 413
    payload['review'] = {'a': {'a': {'a': {'a': {'a': {'a': {'a': 1}}}}}}}
    assert c.put(PATH, json=payload).status_code == 422



def test_verified_freeze_persists_across_phases_updates_and_delete(env):
    c, db, user, service = env
    service.record_verified_event(db, user['id'], date(2026, 10, 30),
        reason='data_missing', source='market_gateway', reference='observation-1',
        observed_at='2026-10-30T09:30:00+08:00')
    payload = body()
    payload['evidence'] = {'data_quality': {'complete': True}}
    result = c.put(PATH, json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['day_guard']['frozen'] is True
    assert result.json()['day_guard']['reasons'] == ['data_missing']
    assert result.json()['decision_status'] == 'blocked'
    preview = PATH.replace('final', 'preview')
    assert c.put(preview, json=body()).json()['day_guard']['frozen'] is True
    assert c.delete(PATH, params={'revision': 1}).status_code == 200
    assert c.put(PATH, json=body(2)).json()['day_guard']['frozen'] is True
    assert c.get(BASE, params={'trade_date': '2026-10-30'}).json()['day_guard']['frozen'] is True
    user['id'] = 102
    assert c.put(PATH, json=body()).json()['day_guard']['frozen'] is False
    assert c.put(PATH.replace('2026-10-30', '2026-11-02'), json=body()).json()['day_guard']['frozen'] is False


def test_manual_stop_loss_does_not_forge_verified_guard(env):
    c, _, _, _ = env
    payload = body()
    payload['checklist'] = {'stop_loss': True, 'data_missing': True}
    payload['trades'] = [{'code': '000001', 'direction': 'sell', 'reason': 'stop_loss'}]
    assert c.put(PATH, json=payload).json()['day_guard']['frozen'] is False


def test_internal_verified_event_rejects_untrusted_or_wrong_day(env):
    _, db, _, service = env
    for kwargs in [dict(source='manual', observed_at='2026-10-30T14:45:00+08:00'),
                   dict(source='market_gateway', observed_at='2026-10-29T14:45:00+08:00')]:
        with pytest.raises(ValueError):
            service.record_verified_event(db, 101, date(2026, 10, 30), reason='stop_loss',
                reference='bad', **kwargs)


@pytest.mark.parametrize('phase,time,purpose', [
    ('data_check', '09:30', 'data_integrity'), ('preview', '14:30', 'warning_only'),
    ('final', '14:45', 'manual_record_only'), ('weekly_review', 'Friday 15:00+', 'next_trade_review'),
])
def test_phase_schedule_is_explicit_without_fake_snapshot(env, phase, time, purpose):
    c, _, _, _ = env
    result = c.put(PATH.replace('final', phase), json=body()).json()
    assert result['schedule']['timezone'] == 'Asia/Shanghai'
    assert result['schedule']['time'] == time
    assert result['schedule']['purpose'] == purpose
    assert result['effective_trade_date'] is None
    assert result['decision_status'] == 'unverified'
    assert result['evidence'] == {}
    assert result['automatic_evidence'] == []


def test_safe_additive_create_all_and_main_registration(tmp_path, monkeypatch):
    import ast
    from pathlib import Path
    import app.db as database
    engine = create_engine('sqlite:///' + str(tmp_path / 'migration.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE original_sentinel (value TEXT)'))
        connection.execute(text("INSERT INTO original_sentinel VALUES ('preserve')"))
    monkeypatch.setattr(database, 'engine', engine)
    database.create_all()
    database.create_all()
    tables = set(inspect(engine).get_table_names())
    assert {'dual_core_execution_cards', 'dual_core_card_audit', 'dual_core_verified_day_events'} <= tables
    assert not database.missing_tables()
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT value FROM original_sentinel')) == 'preserve'
    tree = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text())
    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and n.module == 'portfolio.dual_core_checklist_api']
    assert imports
    alias = imports[0].names[0].asname
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'include_router' and n.args and isinstance(n.args[0], ast.Name)
        and n.args[0].id == alias for n in ast.walk(tree))
    engine.dispose()


def test_frontend_null_draft_and_bounded_calculation_input_round_trip(env):
    c, _, _, _ = env
    payload = body()
    payload['account'] = {'daily_pnl': None, 'weekly_pnl': None, 'consecutive_losses': None, 'weight': None}
    payload['evidence'] = {'source': 'manual', 'indices': {}, 'emotion': {}, 'industry': {},
        'stocks': {}, 'calculation_input': {'evidence': {'cores': {'883957': {'price': 100}}}}}
    result = c.put(PATH, json=payload)
    assert result.status_code == 200, result.text
    assert result.json()['evidence'] == payload['evidence']
    assert result.json()['account'] == payload['account']


def test_cards_never_mutate_live_holding_or_trade_tables(env):
    c, db, _, _ = env
    from app.portfolio.models import Holding, TradeRecord
    from sqlalchemy import func, select
    before = [db.scalar(select(func.count()).select_from(m)) for m in (Holding, TradeRecord)]
    payload = body()
    payload['trades'] = [{'code': '000001', 'price': 12, 'quantity': 100, 'direction': 'buy'}]
    payload['holdings'] = [{'code': '000001', 'quantity': 100}]
    assert c.put(PATH, json=payload).status_code == 200
    assert [db.scalar(select(func.count()).select_from(m)) for m in (Holding, TradeRecord)] == before


def test_authentication_is_required(env):
    c, _, _, _ = env
    from app.shared.security import current_user_id
    del c.app.dependency_overrides[current_user_id]
    assert c.get(PATH).status_code == 401
    assert c.put(PATH, json=body()).status_code == 401



def test_calculation_adapter_never_trusts_client_source_claims(env):
    c, _, _, _ = env
    payload = body()
    payload['evidence'] = {'calculation_input': {'evidence': {
        'cores': {'883957': {'source': 'realtime', 'timestamp': '2026-10-30T14:45:00+08:00'},
                  '883994': {'source': 'realtime', 'timestamp': '2026-10-30T14:45:00+08:00'}},
        'emotion': {'source': 'realtime'}, 'industry': {'source': 'realtime'}, 'stock': {'source': 'realtime'}}}}
    result = c.put(PATH, json=payload).json()
    assert result['evidence'] == payload['evidence']
    assert result['calculation']['decision']['can_open'] is False
    assert result['calculation']['data_quality']['complete'] is False
    assert 'cores.883957.source' in result['calculation']['source_missing']
    assert result['day_guard']['frozen'] is False


@pytest.mark.parametrize('section,row', [
    ('account', {'daily_pnl': 'wrong'}), ('account', {'weight': 101}),
    ('trades', {'code': '000001', 'price': None, 'shares': None, 'weight': None}),
])
def test_typed_manual_numeric_fields_and_null_trade_drafts(env, section, row):
    c, _, _, _ = env
    payload = body()
    payload[section] = row if section == 'account' else [row]
    result = c.put(PATH, json=payload)
    assert result.status_code == (200 if section == 'trades' else 422), result.text


@pytest.mark.parametrize('evidence', [
    {'calculation_input': []}, {'calculation_input': {'evidence': []}},
    {'indices': 'bad'}, {'emotion': 10}, {'captured_at': 'not-a-timestamp'},
])
def test_evidence_section_types_rejected(env, evidence):
    c, _, _, _ = env
    payload = body()
    payload['evidence'] = evidence
    assert c.put(PATH, json=payload).status_code == 422


def test_validation_errors_do_not_echo_credentials(env):
    c, _, _, _ = env
    payload = body()
    payload['review'] = {'api_key': 'sensitive-do-not-echo'}
    result = c.put(PATH, json=payload)
    assert result.status_code == 422
    assert 'sensitive-do-not-echo' not in result.text



def test_guard_exposes_verified_event_provenance_and_idempotence(env):
    c, db, _, service = env
    kwargs = dict(reason='stop_loss', source='verified_trade_ledger', reference='verified-42',
                  observed_at='2026-10-30T14:46:00+08:00')
    service.record_verified_event(db, 101, date(2026, 10, 30), **kwargs)
    service.record_verified_event(db, 101, date(2026, 10, 30), **kwargs)
    result = c.put(PATH, json=body()).json()
    events = result['day_guard']['events']
    assert len(events) == 1
    assert events[0]['source'] == 'verified_trade_ledger'
    assert events[0]['reference'] == 'verified-42'
    assert events[0]['reason'] == 'stop_loss'
    assert c.get(PATH + '/export').json()['card']['day_guard']['events'] == events


@pytest.mark.parametrize('evidence', [{'captured_at': 10}, {'source': {}}, {'captured_at': None}])
def test_evidence_metadata_types_rejected(env, evidence):
    c, _, _, _ = env
    payload = body()
    payload['evidence'] = evidence
    assert c.put(PATH, json=payload).status_code == 422
