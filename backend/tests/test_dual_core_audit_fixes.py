"""Regression tests for the audit fixes (V8 serialization, None safety, snapshots, body bounds)."""
import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from tests.test_dual_core_checklist import body, PATH, BASE
from tests.test_dual_core_v2 import NOW, fixture


# ---------- P1-1: unknown values fail closed, never a numeric TypeError ----------

def test_unknown_account_risk_is_checked_before_the_decision_tree():
    from app.screener.dual_core_v2 import evaluate
    p = fixture()
    p['account']['daily_pnl'] = None
    r = evaluate(p, now=NOW)
    assert r['data_quality']['complete'] is False
    assert 'account.daily_pnl' in r['source_missing']
    assert r['decision']['can_open'] is False
    assert any(c['key'] == 'risk_evidence' and c['pass'] is None for c in r['checks'])


def test_unknown_holding_price_fails_closed_instead_of_crashing():
    from app.screener.dual_core_v2 import evaluate
    p = fixture()
    p['holdings'] = [dict(code='000002', weight=10, pnl=0, boards=1, price=None,
                          ma5=9, black_swan=False, high_volume_bear=False,
                          high_upper_shadow=False, bought_today=False,
                          limit_down_locked=False)]
    r = evaluate(p, now=NOW)
    assert r['data_quality']['complete'] is False
    assert 'holdings.0.price' in r['source_missing']
    assert r['decision']['can_open'] is False


def test_unknown_pullback_inputs_never_infer_a_reduction():
    from app.screener.dual_core_v2 import evaluate
    p = fixture()
    p['cores']['883994']['pct_chg_high'] = None
    p['holdings'] = [dict(code='000002', weight=10, pnl=0, boards=3, price=9,
                          ma5=9, black_swan=False, high_volume_bear=False,
                          high_upper_shadow=False, bought_today=False,
                          limit_down_locked=False)]
    r = evaluate(p, now=NOW)
    assert r['decision']['step'] == 11
    assert any(c['key'] == 'pullback_inputs' and c['pass'] is None for c in r['checks'])
    assert not any(c['key'] == 'decision_step_7' for c in r['checks'])


def test_macd_histogram_with_null_items_is_unknown():
    from app.screener.dual_core_v2 import evaluate
    p = fixture()
    p['cores']['883994']['macd_hist'] = [3, None, 5]
    r = evaluate(p, now=NOW)
    assert r['data_quality']['complete'] is False
    assert 'cores.883994.macd_hist' in r['source_missing']


def test_optional_stock_pattern_fields_may_be_null():
    from app.screener.dual_core_v2 import evaluate
    p = fixture()
    for key in ('box_range20', 'prior_high20', 'n_bear_age', 'n_bear_high'):
        p['stock'][key] = None
    r = evaluate(p, now=NOW)
    assert r['decision']['step'] == 11
    assert r['decision']['can_open'] is False
    assert r['data_quality']['complete'] is True


# ---------- P0: one process-wide V8 lane ----------

def test_call_v8_serializes_concurrent_calls():
    from app.shared import data_gateway as dg
    state = {'now': 0, 'max': 0}
    guard = threading.Lock()

    def work():
        with guard:
            state['now'] += 1
            state['max'] = max(state['max'], state['now'])
        time.sleep(0.03)
        with guard:
            state['now'] -= 1
        return 'ok'

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(lambda _: dg.call_v8(work, timeout=5), range(6))) == ['ok'] * 6
    assert state['max'] == 1


def test_call_v8_lock_wait_is_bounded():
    from app.shared import data_gateway as dg
    lock = dg.ak_serialized()
    assert lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            dg.call_v8(lambda: 'never', timeout=0.2)
    finally:
        lock.release()


def test_v8_sources_are_marked_and_cockpit_shares_the_lock():
    from app.screener import cockpit
    from app.shared import data_gateway as dg
    for fn in (dg.get_trade_calendar, dg._daily_hist_sina, dg._index_daily_sina,
               dg._concept_board_cons_ths, dg.get_ths_industry_daily):
        assert getattr(fn, '__v8_serialized__', None) is True
    assert getattr(dg._daily_hist_ak, '__v8_serialized__', None) is False
    assert cockpit._V8_LOCK is dg.ak_serialized()


# ---------- P1-2: reads replay the stored calculation snapshot ----------

@pytest.fixture
def card_env():
    from app.db import PortfolioBase, get_db
    from app.shared.security import current_user_id
    from app.portfolio import dual_core_checklist_api as api
    from app.portfolio import dual_core_checklist as service
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    PortfolioBase.metadata.create_all(engine)
    db = Session(engine)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: 101
    with TestClient(app) as client:
        yield client, db, service
    db.close()
    engine.dispose()


def test_get_and_list_reuse_the_audit_snapshot_without_recomputing(card_env, monkeypatch):
    client, _, service = card_env
    assert client.put(PATH, json=body()).status_code == 200
    saved = client.get(PATH).json()

    def explode(*_args, **_kwargs):
        raise AssertionError('read path must not recompute the rule engine')

    monkeypatch.setattr(service, 'manual_calculation', explode)
    assert client.get(PATH).json() == saved
    assert client.get(BASE, params={'trade_date': '2026-10-30'}).json()['cards'] == [saved]


def test_legacy_card_without_snapshot_still_reads(card_env, monkeypatch):
    from app.portfolio.dual_core_checklist_models import CardAudit
    client, db, service = card_env
    assert client.put(PATH, json=body()).status_code == 200
    db.execute(delete(CardAudit))
    db.commit()
    calls = []
    original = service.manual_calculation

    def counting(payload, recorded_at):
        calls.append(recorded_at)
        return original(payload, recorded_at)

    monkeypatch.setattr(service, 'manual_calculation', counting)
    assert client.get(PATH).status_code == 200
    assert len(calls) == 1


# ---------- P1-3: bounded bodies on the public evaluation/checklist routes ----------

@pytest.fixture
def screener_env():
    from app.db import PortfolioBase, ScreenerBase, get_db
    from app.screener import api
    from app.shared.security import current_user_id
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
    db = Session(engine)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user_id] = lambda: 101
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    db.close()
    engine.dispose()


def test_evaluate_v2_rejects_oversized_body(screener_env):
    p = fixture()
    p['stock']['note'] = 'x' * 140000
    r = screener_env.post('/api/screener/dual-core/evaluate-v2',
                          content=json.dumps(p),
                          headers={'Content-Type': 'application/json'})
    assert r.status_code == 413, r.text


def test_evaluate_v2_rejects_deep_or_credential_payloads(screener_env):
    depth = {}
    cursor = depth
    for _ in range(7):
        cursor['a'] = {}
        cursor = cursor['a']
    p = fixture()
    p['stock']['nested'] = depth
    r = screener_env.post('/api/screener/dual-core/evaluate-v2', json=p)
    assert r.status_code == 422, r.text

    p = fixture()
    p['stock']['api_key'] = 'nope'
    assert screener_env.post('/api/screener/dual-core/evaluate-v2', json=p).status_code == 422


def test_legacy_checklist_rejects_oversized_deep_and_credential_bodies(screener_env):
    url = '/api/screener/dual-core/checklist'
    oversized = {'items': {'note': 'x' * 140000}, 'review': {}}
    r = screener_env.put(url, content=json.dumps(oversized),
                         headers={'Content-Type': 'application/json'})
    assert r.status_code == 413, r.text

    depth = {}
    cursor = depth
    for _ in range(7):
        cursor['a'] = {}
        cursor = cursor['a']
    assert screener_env.put(url, json={'items': {}, 'review': depth}).status_code == 422
    assert screener_env.put(url, json={'items': {'api_key': 'nope'},
                                       'review': {}}).status_code == 422
    assert screener_env.put(url, json={'items': {'step0': True},
                                       'review': {'q1': 'ok'}}).status_code == 200


def test_body_limit_middleware_stops_chunked_upload_before_app():
    from app.shared.body_limit import PathBodyLimitMiddleware
    called = []

    async def downstream(_scope, receive, send):
        called.append(True)

    async def scenario():
        received = [{'type': 'http.request', 'body': b'x' * 100000, 'more_body': True},
                    {'type': 'http.request', 'body': b'x' * 40000, 'more_body': False}]
        sent = []

        async def receive():
            return received.pop(0)

        async def send(message):
            sent.append(message)

        middleware = PathBodyLimitMiddleware(downstream)
        scope = {'type': 'http', 'path': '/api/screener/dual-core/evaluate-v2',
                 'headers': []}
        await middleware(scope, receive, send)
        return sent

    sent = asyncio.run(scenario())
    assert called == []
    assert sent[0]['status'] == 413
