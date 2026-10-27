"""Review regressions: synthetic evidence, isolated DB, no live adapter/network."""
import pytest
from tests.test_dual_core_v2 import fixture, evaluate, NOW


def holding(**changes):
    row = dict(code='000002', black_swan=False, pnl=0, high_volume_bear=False,
               high_upper_shadow=False, price=10, ma5=9, boards=1, bought_today=False,
               limit_down_locked=False, weight=20)
    row.update(changes)
    return row


@pytest.mark.parametrize('stock_patch', [{'black_swan': True}, {'w_close': None}])
def test_total_cap_precedes_candidate_rejection(stock_patch):
    p = fixture(); p['account']['total_weight'] = 60
    p['stock'].update(stock_patch); p['holdings'] = [holding(weight=60, bought_today=True)]
    r = evaluate(p)
    assert r['decision']['action'] == 'reduce_to_cap'
    assert r['decision']['target_total_weight'] == 50
    assert r['decision']['orders'][0]['status'] == 'blocked_t1'
    assert r['decision']['orders'][0]['executed'] is False


@pytest.mark.parametrize('risk,action', [({'black_swan': True}, 'clear'),
                                       ({'high_volume_bear': True}, 'take_profit')])
def test_protection_precedes_total_cap(risk, action):
    p = fixture(); p['account']['total_weight'] = 60
    p['holdings'] = [holding(weight=60, **risk)]; p['stock']['black_swan'] = True
    assert evaluate(p)['decision']['action'] == action


@pytest.mark.parametrize('patch', [{'black_swan': True}, {'w_close': None}])
def test_all_existing_single_caps_precede_candidate_filters(patch):
    p = fixture(); p['account']['total_weight'] = 40
    p['holdings'] = [holding(weight=36, bought_today=True), holding(code='000004', weight=4)]
    p['stock']['code'] = '000003'; p['stock'].update(patch)
    r = evaluate(p)
    assert r['decision']['action'] == 'reduce_single_to_cap'
    order = r['decision']['orders'][0]
    assert order['code'] == '000002'
    assert order['target_weight'] == 35
    assert order['status'] == 'blocked_t1'
    assert order['executed'] is False


def test_total_reduction_includes_every_single_cap_even_when_frozen():
    p = fixture(); p['account'].update(total_weight=80, stopped_today=True)
    p['holdings'] = [holding(weight=40), holding(code='000004', weight=40)]
    r = evaluate(p)
    assert r['decision']['target_total_weight'] == 50
    assert {o['code']: o['target_weight'] for o in r['decision']['orders']} == {'000002': 35, '000004': 35}


@pytest.mark.parametrize('calendar', [None, {'verified': False},
    {'verified': True, 'trade_date': '2026-10-28', 'is_trade_day': False, 'source': 'exchange'},
    {'verified': True, 'trade_date': '2026-10-27', 'is_trade_day': True, 'source': 'exchange'}])
def test_calendar_unknown_holiday_or_wrong_date_cannot_open(calendar):
    from app.screener.dual_core_v2 import evaluate as run
    r = run(fixture(), now=NOW, calendar=calendar)
    assert not r['decision']['can_open']
    assert r['decision']['action'] != 'open'
    assert r['calendar_status'] in ('calendar_unverified', 'closed')


def test_client_calendar_cannot_establish_server_trust():
    from app.screener.dual_core_v2 import evaluate as run
    p=fixture(); p['calendar']={'verified': True, 'is_trade_day': True, 'trade_date': '2026-10-28'}
    assert not run(p, now=NOW)['decision']['can_open']


@pytest.fixture
def http_env():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from app.db import PortfolioBase, ScreenerBase, get_db
    from app.screener import api
    from app.shared.security import current_user_id
    from app.portfolio import dual_core_checklist as cards
    engine=create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    PortfolioBase.metadata.create_all(engine); ScreenerBase.metadata.create_all(engine)
    db=Session(engine); user={'id':101}
    app=FastAPI(); app.include_router(api.router)
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[current_user_id]=lambda:user['id']
    with TestClient(app) as client: yield client, db, user, cards, app
    db.close(); engine.dispose()


def test_http_reads_server_guard_for_authenticated_user(http_env):
    client, db, user, cards, app=http_env
    from app.screener import api
    from datetime import datetime
    from app.screener.dual_core_v2 import CN
    today=datetime.now(CN)
    cards.record_verified_event(db,101,today.date(),reason='stop_loss',source='verified_trade_ledger',
                                reference='ledger-1',observed_at=today.isoformat())
    for method,path,kwargs in [('get','/api/screener/dual-core',{}),
                               ('post','/api/screener/dual-core/evaluate-v2',{'json':fixture()})]:
        r=getattr(client,method)(path,**kwargs)
        assert r.status_code==200, r.text
        assert r.json()['day_guard']['frozen'] is True
        assert not r.json()['decision']['can_open']
    user['id']=102
    assert client.post('/api/screener/dual-core/evaluate-v2',json=fixture()).json()['day_guard']['frozen'] is False


def test_verified_failure_http_latches_after_recovery_only_for_same_user(http_env, monkeypatch):
    client, db, user, cards, app=http_env
    from app.screener import dual_core_context as context
    calendar={'verified':True,'trade_date':'2026-10-28','is_trade_day':True,'source':'test_exchange'}
    state={'now':NOW,'calendar':calendar,'verified_snapshot':fixture(),
           'source_verified':True,'reference':'snapshot-1'}
    del state['verified_snapshot']['emotion']
    monkeypatch.setattr(context,'evaluation_context',lambda:state)
    r=client.get('/api/screener/dual-core').json()
    assert r['day_guard']['reasons']==['data_missing']
    state['verified_snapshot']=fixture()
    r=client.get('/api/screener/dual-core').json()
    assert r['day_guard']['frozen'] is True
    assert not r['decision']['can_open']
    assert len(r['day_guard']['events'])==1
    user['id']=102
    assert client.get('/api/screener/dual-core').json()['decision']['can_open'] is True


@pytest.mark.parametrize('hour,verified', [(8, True),(14,False)])
def test_unchecked_or_unverified_sources_never_create_missing_latch(http_env,monkeypatch,hour,verified):
    client, db, user, cards, app=http_env
    from app.screener import dual_core_context as context
    clock=NOW.replace(hour=hour)
    monkeypatch.setattr(context,'evaluation_context',lambda:dict(now=clock,
        calendar={'verified':True,'trade_date':'2026-10-28','is_trade_day':True,'source':'test_exchange'},
        verified_snapshot={},source_verified=verified,reference='snapshot-1'))
    for _ in range(2):
        r=client.get('/api/screener/dual-core').json()
        assert not r['decision']['can_open']
        assert not r['day_guard']['frozen']


@pytest.mark.parametrize('patch', [
    {'candidates':['private-marker']}, {'candidates':{}}, {'holdings':None},
    {'holdings':['private-marker']}, {'account':[]}, {'evidence':[]},
    {'cores':{'883957':[]}}, {'emotion':[]}, {'industry':None}, {'stock':[]},
    {'stock':{'macd_hist':'private-marker'}},
])
def test_http_malformed_containers_422_without_input_echo(http_env,patch):
    client, db, user, cards, app=http_env
    p=fixture(); p.update(patch)
    response=client.post('/api/screener/dual-core/evaluate-v2',json=p)
    assert response.status_code==422
    assert 'private-marker' not in response.text
    assert 'test_feed' not in response.text
    assert 'input' not in response.text


@pytest.mark.parametrize('branch', ['source','core','account','holding','candidate'])
def test_all_missing_branches_keep_flatten_advisory(branch):
    p=fixture(); p['holdings']=[holding(bought_today=True)]
    if branch=='source': del p['emotion']
    if branch=='core': p['cores']['883957']['price']=None
    if branch=='account': del p['account']['daily_pnl']
    if branch=='holding': del p['holdings'][0]['pnl']
    if branch=='candidate': p['stock']['w_close']=None
    r=evaluate(p)
    assert not r['decision']['can_open']
    assert r['data_quality']['complete'] is False
    order=r['decision']['orders'][0]
    assert order['action']=='clear'
    assert order['status']=='blocked_t1'
    assert order['executed'] is False


@pytest.mark.parametrize('patch,reason', [({'daily_pnl':-2},'circuit_breaker'),
                                            ({'holding_stop':True},'stop_loss')])
def test_verified_protective_decisions_are_recorded_not_manual_claims(http_env,monkeypatch,patch,reason):
    client, db, user, cards, app=http_env
    from app.screener import dual_core_context as context
    p=fixture()
    if patch.get('holding_stop'): p['holdings']=[holding(pnl=-5)]
    else: p['account'].update(patch)
    monkeypatch.setattr(context,'evaluation_context',lambda:dict(now=NOW,
        calendar={'verified':True,'trade_date':'2026-10-28','is_trade_day':True,'source':'test_exchange'},
        verified_snapshot=p,source_verified=True,reference='protective-1'))
    r=client.get('/api/screener/dual-core').json()
    assert reason in r['day_guard']['reasons']


@pytest.mark.parametrize('hour,minute', [(11,31),(12,0),(13,0),(14,46)])
def test_failure_latch_only_in_actual_check_windows(http_env,monkeypatch,hour,minute):
    client, db, user, cards, app=http_env
    from app.screener import dual_core_context as context
    monkeypatch.setattr(context,'evaluation_context',lambda:dict(now=NOW.replace(hour=hour,minute=minute),
        calendar={'verified':True,'trade_date':'2026-10-28','is_trade_day':True,'source':'test_exchange'},
        verified_snapshot={},source_verified=True,reference='not-due'))
    assert not client.get('/api/screener/dual-core').json()['day_guard']['events']


def test_manual_spoof_never_creates_verified_event(http_env):
    client, db, user, cards, app=http_env
    p=fixture(); p.update(user_id=999,source_verified=True,reference='spoof',
        verified_snapshot={},calendar={'verified':True,'is_trade_day':True})
    p['account'].update(daily_pnl=-2, data_failure_today=True)
    r=client.post('/api/screener/dual-core/evaluate-v2',json=p).json()
    assert not r['day_guard']['events']
    assert not r['decision']['can_open']


@pytest.mark.parametrize('candidate_patch', [{'price':'bad'}, {'rank':'bad'}])
def test_portfolio_cap_precedes_invalid_candidate_numeric_evidence(candidate_patch):
    p=fixture(); p['account']['total_weight']=60
    p['stock' if 'price' in candidate_patch else 'industry'].update(candidate_patch)
    assert evaluate(p)['decision']['target_total_weight']==50


@pytest.mark.parametrize('patch,cap', [({'consecutive_losses':2},25), ({},35)])
def test_existing_single_cap_never_exceeds_discounted_total_cap(patch,cap):
    p=fixture(); p['account'].update(total_weight=36,**patch)
    p['holdings']=[holding(weight=36)]
    r=evaluate(p)
    assert r['decision']['orders'][0]['target_weight']==cap
    assert r['decision']['orders'][0]['fraction']==pytest.approx((36-cap)/36)


@pytest.mark.parametrize('calendar', [None,
    {'verified':True,'trade_date':'2026-10-28','is_trade_day':False,'source':'test_exchange'}])
def test_unknown_or_holiday_calendar_never_latches(http_env,monkeypatch,calendar):
    client, db, user, cards, app=http_env
    from app.screener import dual_core_context as context
    monkeypatch.setattr(context,'evaluation_context',lambda:dict(now=NOW,calendar=calendar,
        verified_snapshot={},source_verified=True,reference='unverified-day'))
    r=client.get('/api/screener/dual-core').json()
    assert not r['day_guard']['events']
    assert not r['decision']['can_open']


def test_verified_stale_snapshot_latches_and_deduplicates(http_env,monkeypatch):
    client, db, user, cards, app=http_env
    from app.screener import dual_core_context as context
    from datetime import timedelta
    p=fixture(); p['emotion']['timestamp']=(NOW-timedelta(seconds=121)).isoformat()
    monkeypatch.setattr(context,'evaluation_context',lambda:dict(now=NOW,
        calendar={'verified':True,'trade_date':'2026-10-28','is_trade_day':True,'source':'test_exchange'},
        verified_snapshot=p,source_verified=True,reference='stale-1'))
    for _ in range(2):
        r=client.get('/api/screener/dual-core').json()
        assert r['day_guard']['reasons']==['data_stale']
        assert len(r['day_guard']['events'])==1


def test_saturday_verified_closed_calendar_no_open():
    from app.screener.dual_core_v2 import evaluate as run
    now=NOW.replace(day=31); p=fixture()
    for group in [*p['cores'].values(),p['emotion'],p['industry'],p['stock']]:
        group['timestamp']=now.isoformat()
    r=run(p,now=now,calendar={'verified':True,'trade_date':'2026-10-31','is_trade_day':False,'source':'test_exchange'})
    assert r['phase']=='closed'
    assert not r['decision']['can_open']


def test_no_adapter_missing_source_remains_blocked_without_permanent_event(http_env):
    client, db, user, cards, app=http_env
    for _ in range(2):
        r=client.get('/api/screener/dual-core').json()
        assert not r['decision']['can_open']
        assert not r['day_guard']['events']




