"""Executable v2 rules; fixtures are synthetic, never live market claims."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import copy
import pytest

NOW = datetime(2026, 10, 28, 14, 45, tzinfo=ZoneInfo('Asia/Shanghai'))

def evaluate(payload, now=NOW):
    from app.screener.dual_core_v2 import evaluate as run
    return run(payload, now=now, calendar={'verified': True, 'trade_date': now.date().isoformat(),
                                         'is_trade_day': now.weekday() < 5, 'source': 'synthetic_test_calendar'})


def fixture():
    stamp = {'timestamp': NOW.isoformat(), 'source': 'test_feed'}
    core = dict(stamp, price=110, prev_high=100, prev_low=90, high60=100,
                ma5=100, ma10=99, ma20=98, ma5_prev=99,
                wma5=100, wma10=95, wma20=90, w_pct=1,
                wma20_up4=True, below_ma5_days=0,
                macd_hist=[3, 4, 5], macd_dead_cross=False,
                high=110, pct_chg=2)
    return {'cores': {'883957': dict(core), '883994': dict(core)},
            'emotion': dict(stamp, limit_up=60, broken=10, limit_down=5,
                            height=5, amount=110, amount_ma5=100),
            'industry': dict(stamp, wma5=120,wma10=110,wma20=100,price=130,wma20_up4=True,
                high20w=150,previous_high20w=140,rank=1,amount=130,amount_ma20=100,
                gain5=3,above_ma5_days=4),
            'stock': dict(stamp, code='000001',price=10,open=9,high=10.1,low=9,prev_close=9,
                ma5=9.01,ma10=9.1,ma20=9.2,prev_price=8,prev_ma5=9,volume_ratio=2,
                w_close=10,wma30=9,macd_cross_weeks=1,dif=1,black_swan=False,
                float_cap=3e9,amount_avg=3e8,limit_down5=0,return20=0,
                boards=1,is_core=True,leader1=False,full_resonance=True,
                high_upper_shadow=False,high_volume_bear=False,stagnation=False,one_price_limit_up=False,
                ma5_bad=False),
            'account': dict(daily_pnl=0,weekly_pnl=0,consecutive_losses=0,stopped_today=False,
                data_failure_today=False,frozen_sessions=0,review_complete=True,two_month_low_winrate=False,
                total_weight=0), 'holdings': []}


@pytest.mark.parametrize('seconds,complete', [(120, True), (121, False), (-1, False)])
def test_freshness_exact_boundary(seconds, complete):
    p = fixture()
    p['emotion']['timestamp'] = (NOW - timedelta(seconds=seconds)).isoformat()
    r = evaluate(p)
    assert r['data_quality']['complete'] is complete


@pytest.mark.parametrize('hour,minute,phase', [(9,30,'observation'), (14,30,'warning'), (14,45,'final'), (15,0,'closed')])
def test_server_clock_controls_phase(hour, minute, phase):
    p = fixture()
    now = NOW.replace(hour=hour, minute=minute)
    for group in [*p['cores'].values(), p['emotion'], p['industry'], p['stock']]:
        group['timestamp'] = now.isoformat()
    r = evaluate(p, now)
    assert r['phase'] == phase
    if phase != 'final':
        assert r['decision']['can_open'] is False


def test_manual_evidence_never_becomes_system_final():
    p = fixture()
    p['stock']['source'] = 'manual'
    r = evaluate(p)
    assert r['data_quality']['complete'] is False
    assert 'stock.source' in r['source_missing']
    assert r['decision']['step'] == 0


@pytest.mark.parametrize('patch,key', [({'wma5':111}, 'weekly_veto'), ({'w_pct':-8}, 'weekly_veto'), ({'price':100.2}, 'gun_a')])
def test_veto_overrides_daily_bull(patch, key):
    p=fixture(); p['cores']['883957'].update(patch)
    r=evaluate(p)
    assert r['verdict']['cap']==0
    assert any(c['key']==key and c['pass'] is False for c in r['checks'])


def test_cumulative_ladder_and_unbacktested_lock():
    p=fixture(); p['cores']['883957'].update(ma5=120,ma10=119,ma20=118)
    r=evaluate(p)
    assert r['position']['tier_cap']==25
    assert r['position']['only_core'] is True
    assert r['position']['new_entry_cap']==5
    assert r['verdict']['cap']==25


def test_divergence_requires_two_positive_red_shrinks():
    p=fixture(); p['cores']['883994']['macd_hist']=[5,4,3]
    r=evaluate(p)
    assert r['position']['forbid_high_boards'] is True
    assert r['position']['single_cap']<=30


def test_trial_entry_reserve_is_conflict_not_multiplication():
    p=fixture(); p['cores']['883994']['ma5']=120
    r=evaluate(p)
    assert r['position']['tier_cap']==10
    assert r['position']['new_entry_cap']==0
    assert any('20' in c for c in r['conflicts'])
    assert r['decision']['can_open'] is False


@pytest.mark.parametrize('field,value', [('black_swan',None),('black_swan',True),('w_close',8),('macd_cross_weeks',9),('float_cap',2e9)])
def test_stock_filters_fail_closed(field,value):
    p=fixture(); p['stock'][field]=value
    r=evaluate(p)
    assert r['decision']['step']==10
    assert r['decision']['can_open'] is False


def test_valid_entry_is_advisory_and_reserves_twenty_points():
    r=evaluate(fixture())
    assert r['decision']['step']==11
    assert r['decision']['can_open'] is True
    assert r['position']['new_entry_cap']==30
    assert r['decision']['orders']==[]


@pytest.mark.parametrize('pnl,step', [(-1.5,8),(-2,2)])
def test_daily_loss_freeze(pnl,step):
    p=fixture();p['account']['daily_pnl']=pnl
    r=evaluate(p)
    assert r['decision']['step']==step
    assert not r['decision']['can_open']


def test_numbered_profit_taking_precedes_ma5_cut_and_t1_is_not_filled():
    p=fixture();p['holdings']=[dict(code='000001',black_swan=False,pnl=4,
        high_volume_bear=True,high_upper_shadow=False,price=8,ma5=9,boards=3,
        bought_today=True,limit_down_locked=False,weight=20)]
    r=evaluate(p)
    assert r['decision']['step']==5
    order=r['decision']['orders'][0]
    assert order['status']=='blocked_t1'
    assert order['executed'] is False


@pytest.mark.parametrize('patch,step', [({'black_swan':True},1),({'pnl':-5},4),({'price':8},6)])
def test_holding_priority_before_entries(patch,step):
    p=fixture();h=dict(code='000001',black_swan=False,pnl=0,high_volume_bear=False,
      high_upper_shadow=False,price=10,ma5=9,boards=1,bought_today=False,limit_down_locked=True,weight=20)
    h.update(patch);p['holdings']=[h]
    r=evaluate(p)
    assert r['decision']['step']==step
    assert r['decision']['orders'][0]['status']=='blocked_limit_down'
    assert not r['decision']['can_open']


def test_prior60_excludes_current_and_older_spike():
    import pandas as pd
    from app.screener.dual_core import _core_snapshot
    dates=pd.bdate_range('2026-01-01',periods=200).date
    df=pd.DataFrame(dict(trade_date=dates,open=[100.]*200,high=[101.]*200,low=[99.]*200,close=[100.]*200,amount=[1e8]*200))
    df.loc[0,'high']=999;df.loc[199,'high']=888
    r=_core_snapshot(df)
    assert r['high60']==101
    assert r['high60_bars']==60


def test_http_manual_wrapper_cannot_claim_trusted_feed():
    from app.screener.dual_core_v2 import evaluate_v2
    p=fixture();p['as_of']=NOW.isoformat();p['phase']='final'
    r=evaluate_v2(p)
    assert not r['decision']['can_open']
    assert r['source_missing']


def test_get_daily_data_preserves_actuals_but_blocks_v2():
    from tests.test_dual_core import _init_db, _rows
    from app.screener.dual_core import analyze
    db=_init_db({'883957':_rows('883957'),'883994':_rows('883994')})
    r=analyze(db)
    assert r['data']['883957']['close'] is not None
    assert r['series']['883994']
    assert r['version']=='2.0'
    assert r['source_missing']
    assert r['verdict']['cap']==0
    assert r['status']=='unverified'
    assert 'ma5_ok' in r['verdict']
    assert r['checks'][0]['label']
    assert r['checks'][0]['auto'] is True
    assert r['legacy_analysis']['verdict']['cap']>=0
    db.close()


def test_http_evaluation_cannot_spoof_sources_or_server_clock():
    from app.screener.dual_core_v2 import evaluate_v2
    r=evaluate_v2(fixture(), now=NOW)
    assert not r['decision']['can_open']
    assert 'stock.source' in r['source_missing']


def test_post_route_uses_unverified_manual_evidence():
    from tests.test_dual_core_review_regressions import http_env
    # The public endpoint now consumes DB guard; use the same isolated API fixture.
    env = http_env.__wrapped__()
    client, db, user, cards, app = next(env)
    try:
        r=client.post('/api/screener/dual-core/evaluate-v2',json=fixture())
        assert r.status_code==200
        assert r.json()['version']=='2.0'
        assert r.json()['decision']['can_open'] is False
    finally:
        try: next(env)
        except StopIteration: pass


@pytest.mark.parametrize('field', ['daily_pnl','consecutive_losses','stopped_today','data_failure_today','total_weight'])
def test_missing_account_risk_evidence_cannot_open(field):
    p=fixture();del p['account'][field]
    r=evaluate(p)
    assert r['decision']['can_open'] is False
    assert 'account.'+field in r['source_missing']


def test_manual_evidence_exposes_calculations_without_live_authorization():
    from app.screener.dual_core_v2 import evaluate_v2
    r=evaluate_v2(fixture(),now=NOW)
    assert r['decision']['step']==0
    assert not r['decision']['can_open']
    assert r['evidence_analysis']['verdict']['cap']==50
    assert any(c['key']=='gun_a' for c in r['evidence_analysis']['checks'])


@pytest.mark.parametrize('patch', [{'consecutive_losses':3},{'stopped_today':True},{'data_failure_today':True},{'frozen_sessions':2},{'two_month_low_winrate':True}])
def test_risk_latches_block(patch):
    p=fixture();p['account'].update(patch)
    assert not evaluate(p)['decision']['can_open']


def test_loss_discount_is_visible_in_verdict_and_position():
    p=fixture();p['account']['consecutive_losses']=2
    r=evaluate(p)
    assert r['verdict']['cap']==25
    assert r['position']['new_entry_cap']==5


def test_unknown_holding_risk_blocks_entries():
    p=fixture();p['holdings']=[{'code':'000002','weight':10}]
    r=evaluate(p)
    assert not r['decision']['can_open']
    assert any('holdings' in m for m in r['source_missing'])


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,'bad',True])
def test_invalid_core_numeric_evidence_fails_closed(value):
    p=fixture();p['cores']['883957']['price']=value
    r=evaluate(p)
    assert not r['decision']['can_open']
    assert r['data_quality']['complete'] is False


def test_candidates_list_is_evaluated_without_mutating_input():
    from app.screener.dual_core_v2 import evaluate_v2
    p=fixture();p['candidates']=[p.pop('stock')]
    before=copy.deepcopy(p)
    r=evaluate_v2(p,now=NOW)
    assert len(r['candidate_results'])==1
    assert r['candidate_results'][0]['code']=='000001'
    assert p==before


def test_observation_never_recommends_execution_of_holding_stop():
    p=fixture();p['account']['daily_pnl']=-2
    now=NOW.replace(hour=9,minute=30)
    for group in [*p['cores'].values(),p['emotion'],p['industry'],p['stock']]: group['timestamp']=now.isoformat()
    p['holdings']=[dict(code='000001',black_swan=False,pnl=0,high_volume_bear=False,
      high_upper_shadow=False,price=10,ma5=9,boards=1,bought_today=False,limit_down_locked=False,weight=20)]
    r=evaluate(p,now)
    assert r['decision']['orders'][0]['status']=='warning_only'


@pytest.mark.parametrize('group,field,value', [('emotion','broken','bad'),('stock','price','bad'),('industry','rank','bad'),('account','daily_pnl','bad')])
def test_bad_numeric_other_groups_do_not_crash_or_open(group,field,value):
    p=fixture();p[group][field]=value
    r=evaluate(p)
    assert not r['decision']['can_open']
    assert r['data_quality']['complete'] is False


def test_account_circuit_breaker_target_cap_is_zero():
    p=fixture();p['account']['daily_pnl']=-2
    r=evaluate(p)
    assert r['verdict']['cap']==0
    assert r['position']['tier_cap']==0
    assert any(c['step']==2 for c in r['checks'])


@pytest.mark.parametrize('patch', [{'industry':None},{'cores':[]},{'stock':[]},{'evidence':[]}])
def test_bad_payload_shapes_fail_closed(patch):
    from app.screener.dual_core_v2 import evaluate_v2
    p=fixture();p.update(patch)
    r=evaluate_v2(p,now=NOW)
    assert r['decision']['can_open'] is False


@pytest.mark.parametrize('weak', [False,True])
def test_weekly_macd_below_zero_trial_applies_before_entries(weak):
    p=fixture();p['stock']['dif']=-.1 if weak else .1
    r=evaluate(p)
    assert r['decision']['can_open'] is (not weak)
    if weak:
        assert r['verdict']['cap']==10
        assert r['position']['new_entry_cap']==0


def test_red_macd_must_remain_positive_for_shrink_divergence():
    p=fixture();p['cores']['883994']['macd_hist']=[-1,-2,-3]
    assert not evaluate(p)['position']['forbid_high_boards']


@pytest.mark.parametrize('patch', [{'below_ma5_days':2},{'w_pct':-8}])
def test_weekly_weak_and_double_failed_rebound(patch):
    p=fixture();p['cores']['883994'].update(patch)
    if 'below_ma5_days' in patch:p['cores']['883957'].update(patch)
    r=evaluate(p)
    assert r['verdict']['cap'] in (0,10)
    assert not r['decision']['can_open']


def test_snapshot_from_1430_cannot_be_final_even_when_clock_is_1445():
    p=fixture();p['cores']['883994']['timestamp']=NOW.replace(hour=14,minute=44).isoformat()
    r=evaluate(p)
    assert not r['decision']['can_open']
    assert any('final_snapshot' in m for m in r['source_missing'])


def test_overweight_account_recommends_reduction_never_fabricates_fill():
    p=fixture();p['account']['total_weight']=60
    r=evaluate(p)
    assert r['decision']['action']=='reduce_to_cap'
    assert not r['decision']['can_open']
    assert r['decision']['target_total_weight']==50


def test_missing_data_requests_flatten_without_claiming_success():
    p=fixture();del p['emotion']
    p['holdings']=[dict(code='000001',weight=10,bought_today=True)]
    r=evaluate(p)
    assert r['decision']['orders'][0]['executed'] is False
    assert r['decision']['orders'][0]['status']=='blocked_t1'


def test_recent_eight_week_cross_includes_eighth_week_boundary():
    p=fixture();p['stock']['macd_cross_weeks']=8
    assert evaluate(p)['decision']['can_open'] is True


def test_singleton_attack_entry_has_explicit_table_conflict():
    r=evaluate(fixture())
    assert any('2 holdings' in c for c in r['conflicts'])


def test_missing_data_stops_at_step_zero():
    result = evaluate({})
    assert result['decision']['step'] == 0
    assert result['verdict']['cap'] == 0
    assert result['status'] == 'unverified'
    assert result['data_quality']['missing']
    assert all({'actual', 'threshold', 'reason', 'status', 'timestamp'} <= c.keys() for c in result['checks'])
