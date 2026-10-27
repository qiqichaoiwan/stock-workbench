"""Real JS payload -> HTTP routers -> rule engine; no transport/engine stubs."""
import json
from pathlib import Path
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.test_dual_core_checklist import env, PATH
from tests.test_dual_core_v2 import fixture


def ui_payload():
    p = fixture()
    # Synthetic measurements, explicit user-recorded timestamp, no trusted source.
    p['holdings'] = [dict(code='000002',weight=20,pnl=0,price=10,ma5=9,boards=1,
        black_swan=False,high_volume_bear=False,high_upper_shadow=False,
        bought_today=False,limit_down_locked=False)]
    p['account']['total_weight'] = 20
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    module = (Path(__file__).parents[2] / 'frontend/src/utils/dualCoreCard.mjs').as_uri()
    script = f"""import {{createCard,buildManualPayload,toCardBody}} from '{module}';
let text='';for await(const chunk of process.stdin)text+=chunk;
const p=JSON.parse(text),c=createCard();c.evidence.indices=p.cores;c.evidence.emotion=p.emotion;
c.evidence.industry=p.industry;c.evidence.captured_at={json.dumps(stamp)};
c.account=p.account;c.holdings=p.holdings;c.candidates=[p.stock];
console.log(JSON.stringify({{payload:buildManualPayload(c,'2026-10-28','final'),body:toCardBody(c,'2026-10-28','final')}}));"""
    return json.loads(subprocess.run(['node','--input-type=module','-e',script],input=json.dumps(p),
        text=True,capture_output=True,check=True).stdout)


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), -float('inf')])
def test_fresh_open_invalid_post_is_422(env, monkeypatch, value):
    from app.screener import api, dual_core_context
    from app.shared.security import current_user_id
    from app.db import get_db
    from tests.test_dual_core_v2 import NOW
    monkeypatch.setattr(dual_core_context, 'evaluation_context', lambda: {
        'now': NOW, 'calendar': None, 'verified_snapshot': None})
    p = fixture()
    p['stock']['open'] = value
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[get_db] = lambda: env[1]
    app.dependency_overrides[current_user_id] = lambda: 101
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/api/screener/dual-core/evaluate-v2',
            content=json.dumps(p), headers={'Content-Type': 'application/json'})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), -float('inf')])
def test_real_ui_invalid_open_put_has_no_card_or_revision(env, value):
    original_client, db, _, service = env
    body = ui_payload()['body']
    body['candidates'][0]['open'] = value
    with TestClient(original_client.app, raise_server_exceptions=False) as client:
        response = client.put(PATH, content=json.dumps(body),
            headers={'Content-Type': 'application/json'})
        assert response.status_code == 422, response.text
        assert client.get(PATH).status_code == 404
        assert service.find_card(db, 101, '2026-10-30', 'final') is None
        valid = ui_payload()['body']
        assert client.put(PATH, json=valid).status_code == 200
        before = client.get(PATH).json()
        assert_reference(before['calculation'])
        body['revision'] = 1
        assert client.put(PATH, content=json.dumps(body),
            headers={'Content-Type': 'application/json'}).status_code == 422
        assert client.get(PATH).json() == before
        assert len(client.get(PATH + '/audit').json()['items']) == 1
        assert all(not o['executed'] for o in before['calculation']['evidence_analysis']['decision']['orders'])


@pytest.mark.parametrize('field', [
    'price', 'open', 'high', 'low', 'prev_close', 'prev_price', 'prev_high', 'prev_low',
    'high60', 'ma5', 'ma10', 'ma20', 'ma5_prev', 'prev_ma5', 'wma5', 'wma10',
    'wma20', 'wma30', 'w_close', 'high20w', 'previous_high20w', 'prior_high20', 'n_bear_high',
])
@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), -float('inf'), True, '1'])
def test_price_validation_is_shared_by_payload_and_card(field, value):
    from app.screener.dual_core_v2 import validate_payload_shape
    from app.portfolio.dual_core_checklist import CardInput
    p = fixture(); p['stock'][field] = value
    with pytest.raises(ValueError):
        validate_payload_shape(p)
    body = dict(revision=0, candidates=[p['stock']])
    with pytest.raises(ValueError):
        CardInput.model_validate(body)


@pytest.mark.parametrize('location', ['nested_stock', 'nested_candidate', 'core', 'holding', 'zero_price_and_open'])
def test_card_price_validation_covers_nested_and_canonical_inputs(env, location):
    client, _, _, _ = env
    body = ui_payload()['body']
    calc = body['evidence']['calculation_input']
    if location == 'nested_stock':
        calc['evidence']['stock'] = {'open': 0}
    elif location == 'nested_candidate':
        calc['candidates'][0]['open'] = 0
    elif location == 'core':
        calc['evidence']['cores']['883957']['price'] = 0
    elif location == 'holding':
        body['holdings'][0]['ma5'] = 0
    else:
        body['candidates'][0].update(price=0, open=0)
    assert client.put(PATH, json=body).status_code == 422
    assert client.get(PATH).status_code == 404


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), -float('inf')])
def test_internal_and_manual_reference_invalid_open_fail_closed(value):
    from tests.test_dual_core_v2 import NOW, evaluate
    from app.screener.dual_core_v2 import evaluate_v2
    p = fixture(); p['stock']['open'] = value
    internal = evaluate(p)
    assert 'stock.open' in internal['source_missing']
    for result in (internal, evaluate_v2(p, now=NOW)):
        assert result['decision']['can_open'] is False
        assert result['data_quality']['complete'] is False
        assert result['source_missing']
        assert all(not o['executed'] for o in result['decision']['orders'])


def assert_reference(result):
    assert result['decision']['can_open'] is False
    analysis = result['evidence_analysis']
    assert analysis['decision']['can_open'] is False
    assert analysis['status'] == 'unverified_manual'
    assert any(c['key']=='gun_a' and c['pass'] is True for c in analysis['checks'])
    assert analysis['verdict']['cap'] == 50
    assert not any(m.startswith(('account.', 'holdings.')) for m in analysis['source_missing'])


def test_real_ui_payload_reaches_http_engine_and_saved_calculation(env):
    from app.screener import api
    from app.shared.security import current_user_id
    from app.db import get_db
    data = ui_payload()
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides[get_db]=lambda:env[1]
    app.dependency_overrides[current_user_id]=lambda:101
    with TestClient(app) as client:
        response=client.post('/api/screener/dual-core/evaluate-v2',json=data['payload'])
    assert response.status_code==200,response.text
    assert_reference(response.json())
    client,_,_,_=env
    response=client.put(PATH,json=data['body'])
    assert response.status_code==200,response.text
    saved=client.get(PATH).json()
    assert saved['evidence']['calculation_input']==data['payload']
    assert_reference(saved['calculation'])


@pytest.mark.parametrize('section,field,value', [
    ('account','total_weight',101),('account','review_complete','false'),
    ('account','frozen_sessions',-1),('holdings','bought_today',0),
    ('holdings','pnl','bad'),('candidates','black_swan','false'),
])
def test_card_structured_engine_fields_reject_invalid_types(env,section,field,value):
    client,_,_,_=env
    body=ui_payload()['body']
    target=body[section] if section=='account' else body[section][0]
    target[field]=value
    response=client.put(PATH,json=body)
    assert response.status_code==422,response.text


@pytest.mark.parametrize('evidence', [
    {'cores': []}, {'cores': {'883957': 'bad'}}, {'emotion': []},
    {'stock': {'macd_hist': ['bad']}}, {'industry': {'wma20_up4': 'false'}},
])
def test_card_calculation_evidence_has_structured_sections(env,evidence):
    client,_,_,_=env
    body=ui_payload()['body']
    body['evidence']['calculation_input']['evidence']=evidence
    response=client.put(PATH,json=body)
    assert response.status_code==422


def test_saved_calculation_uses_current_account_and_holdings_not_nested_override(env):
    client,_,_,_=env
    data=ui_payload()
    data['body']['account']['daily_pnl']=-2
    response=client.put(PATH,json=data['body'])
    assert response.status_code==200,response.text
    analysis=response.json()['calculation']['evidence_analysis']
    assert analysis['decision']['step']==2
    assert analysis['decision']['orders'][0]['code']=='000002'
    assert analysis['decision']['orders'][0]['executed'] is False
