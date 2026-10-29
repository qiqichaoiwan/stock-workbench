"""Fail-closed, side-effect-free dual-core v2 advisory rules.

All percentages use percentage points (e.g. daily_pnl=-2, weight=20).
Step 0..11 numbered tree wins over conflicting prose: profit-taking Step 5
precedes MA5 reduction Step 6. Orders are recommendations, never executions.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

CN = ZoneInfo('Asia/Shanghai')

# Shared by HTTP/manual-card validation and the internal fail-closed engine.
POSITIVE_PRICE_FIELDS = frozenset('price open high low prev_close prev_price prev_high prev_low high60 ma5 ma10 ma20 ma5_prev prev_ma5 wma5 wma10 wma20 wma30 w_close high20w previous_high20w prior_high20 n_bear_high'.split())


def valid_positive_price(value):
    import math
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_positive_prices(group):
    for key, value in group.items():
        if key in POSITIVE_PRICE_FIELDS and value is not None and not valid_positive_price(value):
            raise ValueError('invalid positive price')


def evaluate(payload: dict, *, now: datetime, calendar: dict | None = None) -> dict:
    now = now.astimezone(CN)
    ev = payload.get('evidence', payload)
    groups = {f'cores.{k}': ev.get('cores', {}).get(k, {}) for k in ('883957', '883994')}
    groups.update({k: ev.get(k, {}) for k in ('emotion', 'industry', 'stock')})
    missing = []
    for name, group in groups.items():
        if not group:
            missing.append(name)
            continue
        if not group.get('source') or group.get('source') in ('manual', 'daily', 'unknown'):
            missing.append(name + '.source')
        try:
            stamp = datetime.fromisoformat(group.get('timestamp', ''))
            if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= 120:
                missing.append(name + '.timestamp')
            if name.startswith('cores.') and now.hour==14 and now.minute==45 and stamp.tzinfo is not None and stamp.astimezone(CN).strftime('%Y-%m-%d %H:%M') != now.strftime('%Y-%m-%d %H:%M'):
                missing.append(name+'.final_snapshot')
        except (ValueError, TypeError):
            missing.append(name + '.timestamp')
    minute = now.hour * 60 + now.minute
    phase = ('final' if minute == 885 else 'warning' if 870 <= minute < 885
             else 'observation' if 570 <= minute < 870 else 'closed')
    calendar = calendar or {}
    calendar_verified = (calendar.get('verified') is True and bool(calendar.get('source'))
                         and calendar.get('trade_date') == now.date().isoformat()
                         and type(calendar.get('is_trade_day')) is bool)
    calendar_status = ('open' if calendar['is_trade_day'] else 'closed') if calendar_verified else 'calendar_unverified'
    if calendar_status != 'open': phase = 'closed'
    checks = []
    def check(key, step, actual, threshold, passed, reason):
        checks.append(dict(key=key, step=step, actual=actual, threshold=threshold,
                           **{'pass': passed}, status='missing' if passed is None else 'pass' if passed else 'fail',
                           reason=reason, timestamp=now.isoformat(), label=key, auto=True,
                           value=actual, hint=reason, veto=step==0))
        return passed
    check('data', 0, missing, 'complete; age <=120s', not missing, 'source_missing' if missing else 'fresh sources')
    result = {'version': '2.0', 'status': 'unverified', 'as_of': now.isoformat(),
              'phase': phase, 'calendar_status': calendar_status, 'source_missing': missing,
              'data_quality': {'missing': missing, 'complete': not missing},
              'checks': checks, 'verdict': {'level': '空仓', 'cap': 0, 'veto': True,
                    'daily_ok': False, 'weekly_ok': False, 'ma5_ok': None, 'position_text': '0%', 'reasons': []},
              'decision': {'step': 0, 'action': 'block', 'can_open': False, 'orders': [], 'reason': 'source_missing'},
              'position': {'tier_cap': 0, 'new_entry_cap': 0, 'single_cap': 0, 'max_holdings': 0,
                           'only_core': False, 'forbid_high_boards': False},
              'conflicts': ['Explicit numbered Step 5 profit-taking precedes Step 6 MA5 reduction.']}
    def missing_result():
        result['data_quality']['complete']=False
        result['decision']['orders'] = [dict(code=h.get('code'),action='clear',fraction=1,executed=False,
            status='blocked_t1' if h.get('bought_today') else 'blocked_limit_down' if h.get('limit_down_locked') else 'unverified_sellability')
            for h in payload.get('holdings',[]) if isinstance(h,dict)]
        result['decision']['reason']='source_missing: flatten advisory only, no assumed fills'
        return result
    if missing: return missing_result()
    a, b, e = groups['cores.883957'], groups['cores.883994'], groups['emotion']
    import math

    required = ['price','prev_high','prev_low','ma5','ma10','ma20','ma5_prev','wma5','wma10','wma20','w_pct','below_ma5_days','wma20_up4']
    absent = [f'cores.{code}.{k}' for code,c in [('883957',a),('883994',b)] for k in required if c.get(k) is None]
    absent += ['cores.883994.'+k for k in ('high60','macd_dead_cross') if b.get(k) is None]
    hist_b = b.get('macd_hist')
    # Three MACD histogram values are required for the divergence rule; null/NaN
    # items are unknown, never a smaller-but-valid shift.
    if (not isinstance(hist_b, list) or len(hist_b) < 3
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in hist_b[-3:])):
        absent.append('cores.883994.macd_hist')
    absent += ['emotion.'+k for k in ['limit_up','broken','limit_down','height','amount','amount_ma5'] if e.get(k) is None]
    for code,c in [('883957',a),('883994',b)]:
        for key in required + (['high60'] if code=='883994' else []):
            value=c.get(key)
            if key=='wma20_up4':
                valid=isinstance(value,bool)
            else:
                valid=type(value) in (int,float) and math.isfinite(value) and (key in ('w_pct',) or value>=0)
            if not valid: absent.append(f'cores.{code}.{key}')
    numeric_fields = set('price prev_high prev_low high60 ma5 ma10 ma20 ma5_prev wma5 wma10 wma20 w_pct below_ma5_days high pct_chg pct_chg_high limit_up broken limit_down height amount amount_ma5 high20w previous_high20w rank amount_ma20 gain5 above_ma5_days open low prev_close prev_price prev_ma5 volume_ratio w_close wma30 macd_cross_weeks dif float_cap amount_avg limit_down5 return20 boards box_range20 prior_high20 n_bear_age n_bear_high daily_pnl weekly_pnl consecutive_losses total_weight frozen_sessions weight pnl'.split())
    candidate_invalid=[]
    for name, group in [*groups.items(), ('account',payload.get('account',{})),
                        *[(f'holdings.{idx}',h) for idx,h in enumerate(payload.get('holdings',[]))]]:
        if not isinstance(group,dict):
            absent.append(name);continue
        for key,value in group.items():
            if key in POSITIVE_PRICE_FIELDS and value is not None and not valid_positive_price(value):
                (candidate_invalid if name in ('industry','stock') else absent).append(name+'.'+key)
            if key in numeric_fields and value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                (candidate_invalid if name in ('industry','stock') else absent).append(name+'.'+key)
    if absent:
        missing.extend(absent); result['data_quality']['complete']=False
        check('values',0,absent,'all required',False,'missing critical values')
        return missing_result()
    weekly = not (a['price'] < a['wma5'] or a['w_pct'] <= -8 or e['limit_down'] >= 50)
    check('weekly_veto',1,{'price':a['price'],'wma5':a['wma5'],'w_pct':a['w_pct'],'limit_down':e['limit_down']},
          'price >= wma5; weekly > -8%; limit_down <50',weekly,'weekly hard veto')
    ga = check('gun_a',9,a['price'],a['prev_high']*1.003,a['price'] >= a['prev_high']*1.003,'current price, never intraday high')
    gb = check('gun_b',9,b['price'],b['high60']*1.005,b['price'] >= b['high60']*1.005,'prior 60 sessions excluding current session')
    failed_rebound = a['below_ma5_days'] >= 2 and b['below_ma5_days'] >= 2
    check('failed_rebound',1,[a['below_ma5_days'],b['below_ma5_days']],'<2 for at least one core',not failed_rebound,'dual failed rebound veto')
    score = sum([e['limit_up']>=50, e['broken']/(e['limit_up']+e['broken'])<=.25 if e['limit_up']+e['broken'] else False,
                 e['height']>=4,e['limit_down']<=10,e['amount']>=e['amount_ma5']])
    check('emotion',1,score,'>=4 normal; 3 downgrade; <=2 two levels; <=1 no buys',True,'five-item score')
    bull = a['price']>a['wma5'] and a['price']>a['wma10'] and a['price']>a['wma20'] and b['wma5']>b['wma10']>b['wma20'] and b['wma20_up4']
    tier = 2 if bull and b['ma5']>b['ma5_prev'] else 1
    broken = {n:a['price'] < a['ma'+str(n)]*.997 for n in (5,10,20)}
    weak_b = b['price'] < b['wma5'] or b['w_pct'] <= -8
    tier = max(0,tier-int(broken[5])-2*int(weak_b)-(1 if score==3 else 2 if score<=2 else 0))
    if b['price'] < b['ma5']*.997:
        tier=0
    hist=b['macd_hist']
    divergence=bool(gb and (len(hist)>=3 and hist[-3]>hist[-2]>hist[-1]>0 or b['macd_dead_cross']))
    result['divergence']=divergence  # Step 5 conclusion; absent when evidence is missing
    cap = [10,50,100][tier]
    if broken[10]: cap /= 2
    if divergence: cap=min(cap,50)
    cap=min(cap,50)  # unverified v2 cannot unlock by client boolean
    if not weekly or not ga or not gb or failed_rebound: cap=0
    pos=result['position']; pos.update(tier_cap=cap,new_entry_cap=max(0,cap-20),
        single_cap=min(30 if divergence else 35,cap),max_holdings=[1,3,5][tier],
        only_core=broken[20],forbid_high_boards=weak_b or divergence or broken[20])
    for n in (5,10,20): check('ma'+str(n),1,a['price'],a['ma'+str(n)]*.997,not broken[n],'cumulative downgrade / half / core-only')
    if cap and cap<=20: result['conflicts'].append('New-entry reserve subtracts 20 percentage points; trial cap becomes 0: block, not cap*0.8.')
    result['verdict'].update(level=['试错','震荡','进攻'][tier] if cap else '空仓',cap=cap,position_text=f'0%-{cap}%',
        veto=not bool(cap),daily_ok=bool(ga and gb),weekly_ok=bull,reasons=[c['reason'] for c in checks if c['pass'] is False])
    account=payload.get('account', {})
    holdings=payload.get('holdings', [])
    # Risk evidence is collected before the numbered tree: an unknown account or
    # holding field must block entries, never default to a benign zero/false.
    risk_missing=['account.'+k for k in ('daily_pnl','weekly_pnl','consecutive_losses','stopped_today','data_failure_today','total_weight','frozen_sessions','review_complete','two_month_low_winrate') if account.get(k) is None]
    risk_missing += [f'holdings.{idx}.{k}' for idx,h in enumerate(holdings)
                     for k in ('black_swan','pnl','high_volume_bear','high_upper_shadow','price','ma5','boards','bought_today','limit_down_locked','weight') if h.get(k) is None]
    if risk_missing:
        missing.extend(risk_missing);result['data_quality']['complete']=False
        check('risk_evidence',0,risk_missing,'all known',None,'unknown account / holding risks')
        return missing_result()
    def finish(step, action, reason, targets=(), fraction=1):
        orders=[]
        for h in targets:
            blocked=('blocked_t1' if h.get('bought_today') else 'blocked_limit_down' if h.get('limit_down_locked') else
                     'unverified_sellability' if h.get('bought_today') is None or h.get('limit_down_locked') is None else 'recommended')
            if phase != 'final' and blocked == 'recommended': blocked='warning_only'
            orders.append(dict(code=h.get('code'),action=action,fraction=fraction,status=blocked,executed=False))
        check('decision_step_'+str(step),step,action,'numbered Step 0..11 priority',False,reason)
        if action=='clear_all':
            result['verdict'].update(cap=0,level='空仓',position_text='0%',veto=True)
            result['position'].update(tier_cap=0,new_entry_cap=0)
        result['decision'].update(step=step,action=action,reason=reason,orders=orders,can_open=False)
        return result
    # Numbered tree is global: highest-priority hit across all holdings wins.
    bad=[h for h in holdings if h.get('black_swan') is True]
    if bad: return finish(1,'clear','holding black swan; no new entries today',bad)
    if account.get('daily_pnl',0)<=-2: return finish(2,'clear_all','daily loss circuit breaker',holdings)
    if a['price']<a['prev_low']*.997: return finish(3,'clear_all','退军线; no entries today',holdings)
    bad=[h for h in holdings if h.get('pnl',0)<=-5]
    if bad: return finish(4,'clear','stock -5% stop; no new entries today',bad)
    bad=[h for h in holdings if h.get('high_volume_bear') or h.get('high_upper_shadow')]
    if bad: return finish(5,'take_profit','numbered Step 5 precedes MA5 reduction; batch size requires manual execution',bad,.5)
    bad=[h for h in holdings if h.get('price',float('inf'))<h.get('ma5',0)*.997]
    if bad: return finish(6,'reduce_half','holding MA5 exception never applies',bad,.5)
    pct_b, high_b = b.get('pct_chg_high'), b.get('high')
    pullback = pct_b is not None and high_b is not None and pct_b >= 3 and b['price'] <= high_b * .98
    if pct_b is None or high_b is None:
        check('pullback_inputs',7,{'pct_chg_high':pct_b,'high':high_b},
              'known core B pullback inputs',None,'unknown; no reduction inferred')
    bad=[h for h in holdings if h.get('boards',0)>=3 and pullback]
    if bad: return finish(7,'reduce_half','core B pullback; do not compound half reductions',bad,.5)
    # Existing portfolio compliance never depends on the next candidate passing.
    if account.get('consecutive_losses',0)>=2:
        pos['tier_cap']/=2;pos['new_entry_cap']=max(0,pos['tier_cap']-20)
    if account.get('weekly_pnl',0)<=-5: pos['tier_cap']=min(pos['tier_cap'],50)
    result['verdict'].update(cap=pos['tier_cap'], position_text=f"0%-{pos['tier_cap']}%")
    pos['single_cap']=min(pos['single_cap'],pos['tier_cap'])
    oversized=[h for h in holdings if h['weight']>pos['single_cap']]
    total_over=account['total_weight']>pos['tier_cap']
    if total_over or oversized:
        if total_over: result['decision']['target_total_weight']=pos['tier_cap']
        answer=finish(11,'reduce_to_cap' if total_over else 'reduce_single_to_cap',
                      'hard portfolio caps; recommendation only',holdings if total_over else oversized)
        for order in answer['decision']['orders']:
            h=next(h for h in holdings if h.get('code')==order['code'])
            order['target_weight']=min(h['weight'],pos['single_cap'])
            order['fraction']=(h['weight']-order['target_weight'])/h['weight'] if h['weight'] else 0
            if total_over: order['allocation_status']='single_ceiling_only; total allocation requires manual review'
        return answer
    if (account.get('daily_pnl',0)<=-1.5 or account.get('stopped_today') or account.get('data_failure_today')
        or account.get('consecutive_losses',0)>=3 or account.get('frozen_sessions',0)>0
        or account.get('two_month_low_winrate') or account.get('review_complete') is False
        or any(h.get('limit_down_locked') for h in holdings) or score<=1):
        return finish(8,'block','loss freeze / stop-day / data failure latch / locked limit-down')
    if not cap or not ga or not gb: return finish(9,'block','weekly veto or daily gun not satisfied')
    if candidate_invalid:
        missing.extend(candidate_invalid)
        return missing_result()
    i,s=groups['industry'],groups['stock']
    def known(group,key,default):
        # Optional stock patterns may be omitted or explicitly null; null means
        # "pattern not available", so fall back to a value that never triggers it.
        value=group.get(key)
        return default if value is None else value
    def rule(key,group,fields,test,threshold):
        absent=[f for f in fields if group.get(f) is None]
        if absent: missing.extend(key+'.'+f for f in absent)
        passed=None if absent else bool(test())
        check(key,10,{f:group.get(f) for f in fields},threshold,passed,'missing '+','.join(absent) if absent else key)
        return passed is True
    filters=[]
    filters.append(rule('industry_weekly',i,['price','wma5','wma10','wma20','wma20_up4','high20w','previous_high20w'],
        lambda:i['wma5']>i['wma10']>i['wma20'] and i['price']>i['wma20'] and i['wma20_up4'] and i['high20w']>i['previous_high20w'],'upward weekly channel'))
    filters.append(rule('industry_rank_volume',i,['rank','amount','amount_ma20','gain5','above_ma5_days'],
        lambda:1<=i['rank']<=3 and i['rank']<=10 and i['amount']>=i['amount_ma20']*1.3 and i['gain5']>=3 and i['above_ma5_days']>=4,'top3; volume>=1.3x; gain5>=3; >=4 closes above MA5'))
    filters.append(rule('stock_weekly',s,['w_close','wma30','macd_cross_weeks','dif'],
        lambda:s['w_close']>=s['wma30'] and 0<=s['macd_cross_weeks']<=8,'weekly >= MA30; MACD cross within 8 weeks'))
    filters.append(rule('black_swan',s,['black_swan','float_cap','amount_avg','limit_down5','return20','one_price_limit_up'],
        lambda:s['black_swan'] is False and s['float_cap']>=3e9 and s['amount_avg']>=3e8 and s['limit_down5']<2 and s['return20']>=-30 and not s['one_price_limit_up'],'all black-swan evidence false, cap>=3bn; avg amount>=300mn'))
    filters.append(rule('stock_daily',s,['price','open','ma5','ma10','ma20','volume_ratio','high_upper_shadow','stagnation'],
        lambda: not s['high_upper_shadow'] and not s['stagnation'] and (
            ((s['price']/s['open']-1)*100>=3 and s['open']<min(s['ma5'],s['ma10'],s['ma20']) and s['price']>max(s['ma5'],s['ma10'],s['ma20']) and s['volume_ratio']>=1.5)
            or (known(s,'box_range20',100)<=15 and s['price']>known(s,'prior_high20',float('inf')) and s['volume_ratio']>=1.5)
            or (known(s,'n_bear_age',100)<=3 and s['price']>known(s,'n_bear_high',float('inf')) and s['volume_ratio']>=1.2)),
        'one-yang / prior20 box / N reversal; reject upper shadow or stagnation'))
    filters.append(rule('stock_ma5',s,['price','low','ma5','prev_price','prev_ma5','volume_ratio','ma5_bad'],
        lambda:s['ma5_bad'] or (s['low']>=s['ma5'] and s['price']>=s['ma5']) or (s['price']>s['ma5'] and s['prev_price']<s['prev_ma5'] and s['volume_ratio']>=1.5),'MA5 pullback or volume breakout; bad MA5 trial only'))
    if missing:
        missing_result()
        result['decision']['step']=10
        return result
    if not all(filters): return finish(10,'block','selection filter failed or unknown')
    if s['dif']<0 or s['ma5_bad']:
        pos['tier_cap']=min(pos['tier_cap'],10);pos['new_entry_cap']=0
        result['conflicts'].append('Trial <=10 minus 20 percentage points leaves zero: block new entry.')
    result['verdict'].update(cap=pos['tier_cap'], position_text=f"0%-{pos['tier_cap']}%")
    if pos['only_core'] and not s.get('is_core'): return finish(10,'block','MA20 broken: core only')
    if pos['forbid_high_boards'] and (known(s,'boards',0)>=3 or (divergence and not known(s,'is_core',False) and s.get('boards')!=1)):
        return finish(10,'block','divergence/weekly weak forbids high boards')
    if account.get('total_weight',0)>pos['tier_cap']:
        result['decision']['target_total_weight']=pos['tier_cap']
        return finish(11,'reduce_to_cap','hard account cap; recommendation only',holdings)
    count=len(holdings)
    existing=next((h for h in holdings if h.get('code')==s.get('code')),None)
    if existing and (existing.get('pnl',0)<3 or not s.get('full_resonance') or existing.get('limit_up_open')):
        return finish(11,'block','add requires full resonance and >=3% profit; no limit-up-open adding')
    if not existing and count>=min(5,pos['max_holdings']): return finish(11,'block','holding count cap')
    if tier==2 and count+(0 if existing else 1)<2:
        result['conflicts'].append('Attack table starts at 2 holdings; first entry is capped by regular 35%, no invented 70% minimum allocation.')
    upper={2:{2:40,3:33,4:25,5:20},1:{1:30,2:25,3:17},0:{1:10}}[tier].get(count+(0 if existing else 1),35)
    special=s.get('leader1') and s.get('full_resonance') and tier==2
    pos['single_cap']=min(upper,40 if special else 35,30 if divergence else 40,pos['tier_cap'])
    budget=pos['tier_cap'] if existing else pos['new_entry_cap']
    if budget<=account.get('total_weight',0) or existing and existing.get('weight',0)>=pos['single_cap']:
        return finish(11,'block','position capacity exhausted; new-entry 20 point reserve conflict')
    if calendar_status!='open': return finish(11,'block',calendar_status)
    if phase!='final': return finish(11,'observe','only 14:45 snapshot may authorize advisory entry')
    result['decision'].update(step=11,action='add' if existing else 'open',can_open=True,reason='advisory only; no execution',orders=[])
    return result


def validate_payload_shape(payload):
    """Reject structural JSON errors with a constant message, never echo evidence."""
    from ..shared.json_guard import bounded_json
    bounded_json(payload)
    def obj(value):
        if not isinstance(value,dict): raise ValueError('invalid evaluation structure')
        return value
    obj(payload)
    ev=obj(payload.get('evidence',payload))
    for container in (payload,ev):
        cores=obj(container.get('cores',{}))
        groups=[obj(v) for v in cores.values()]
        groups += [obj(container[k]) for k in ('emotion','industry','stock') if k in container]
        for group in groups:
            validate_positive_prices(group)
            if 'macd_hist' in group and (not isinstance(group['macd_hist'],list) or
                any(type(v) not in (int,float) for v in group['macd_hist'])):
                raise ValueError('invalid evaluation structure')
    validate_positive_prices(obj(payload.get('account',{})))
    for key in ('candidates','holdings'):
        rows=payload.get(key,[])
        if not isinstance(rows,list) or len(rows)>100: raise ValueError('invalid evaluation structure')
        for row in rows: validate_positive_prices(obj(row))


def evaluate_v2(payload: dict, *, now: datetime | None = None) -> dict:
    """Public/manual boundary: caller source labels and clock never establish trust.

    Use evaluate(..., now=...) only inside a trusted server-side feed adapter.
    """
    import copy
    try:
        validate_payload_shape(payload)
    except ValueError:
        return evaluate({}, now=now or datetime.now(CN))
    safe = copy.deepcopy(payload)
    original_ev=safe.get('evidence',safe)
    if not isinstance(original_ev,dict) or not isinstance(original_ev.get('cores',{}),dict):
        return evaluate({},now=now or datetime.now(CN))
    candidates=safe.pop('candidates', [])
    if candidates:
        results=[]
        for candidate in candidates:
            single=copy.deepcopy(safe)
            single.setdefault('evidence', {k:single[k] for k in ('cores','emotion','industry') if k in single})['stock']=candidate
            item=evaluate_v2(single,now=now)
            item['code']=candidate.get('code')
            results.append(item)
        result=copy.deepcopy(results[0])
        result['candidate_results']=results
        return result
    ev = safe.get('evidence', safe)
    for group in [*ev.get('cores', {}).values(), *(ev.get(k, {}) for k in ('emotion','industry','stock'))]:
        if isinstance(group, dict) and group:
            group['source'] = 'manual'
    clock = now or datetime.now(CN)
    result = evaluate(safe, now=clock)
    # Manual numbers may be calculated, but hypothetical evaluation is isolated
    # from the top-level fail-closed live verdict, even if every field is filled.
    hypothetical = copy.deepcopy(payload)
    hev = hypothetical.get('evidence', hypothetical)
    for group in [*hev.get('cores', {}).values(), *(hev.get(k, {}) for k in ('emotion','industry','stock'))]:
        if isinstance(group, dict) and group:
            group['source'] = 'hypothetical_manual_calculation'
    analysis = evaluate(hypothetical, now=clock)
    analysis['decision']['can_open'] = False
    analysis['decision']['action'] = 'manual_reference_only'
    analysis['status'] = 'unverified_manual'
    result['evidence_analysis'] = analysis
    return result
