"""真实行情展示和预警必须能区分缺失、延迟与新鲜逐股报价。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import PortfolioBase, ScreenerBase
from app.portfolio import holding_service
from app.portfolio.models import Holding, User, WatchAlertLog, Watchlist
from app.screener.models import DailyQuote
from app.shared import data_gateway as dg
from app import task_runner

CN = ZoneInfo('Asia/Shanghai')
NOW = datetime(2026, 11, 6, 10, 0, tzinfo=CN)

@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username='quote_user', password_hash='x', nickname='q'))
        session.commit()
        yield session
    engine.dispose()


def test_holding_without_quote_is_unknown_not_buy_price(db):
    db.add(Holding(user_id=1, code='600000', name='test', quantity=100,
                   buy_price=10, buy_date=date(2026, 11, 6), breakeven_price=10.01,
                   initial_stop_price=9, stop_price=9, highest_price=10, risk_r=1))
    db.commit()
    row = holding_service.holdings_view(db, 1)[0]
    assert row['close'] is None
    assert row['unrealized_pct'] is None
    assert row['quote_at'] is None


def test_alert_uses_fresh_stock_quote_and_refuses_stale(db, monkeypatch):
    from app.portfolio import live_quotes
    db.add_all([Watchlist(user_id=1, code='600000', name='fresh', take_profit=12),
                Watchlist(user_id=1, code='000001', name='stale', take_profit=12)])
    db.commit()
    monkeypatch.setattr(live_quotes, 'now', lambda: NOW)
    # The production path requires a verified exchange calendar.  This unit
    # test supplies that contract explicitly so it does not depend on the
    # asynchronous network-backed cockpit feed.
    monkeypatch.setattr(live_quotes, '_trading', lambda _: True)
    monkeypatch.setattr(dg, 'get_spot_quotes', lambda codes: ({
        '600000': {'price': 13, 'pct_chg': 5, 'quote_at': '2026-11-06T09:59:30+08:00'},
        '000001': {'price': 13, 'pct_chg': 5, 'quote_at': '2026-11-05T15:00:00+08:00'},
    }, 'sina', '2026-11-06T09:59:30+08:00', 30))
    result = task_runner.task_watch_alert(db)
    alerts = db.execute(select(WatchAlertLog)).scalars().all()
    assert result['alerts'] == 1
    assert [a.code for a in alerts] == ['600000']
    assert result.get('errors')


def test_live_quote_fallback_labels_stored_and_missing(db, monkeypatch):
    from app.portfolio import live_quotes
    db.add(DailyQuote(code='600000', trade_date=date(2026, 11, 5), close=10, pct_chg=1))
    db.commit()
    monkeypatch.setattr(live_quotes, 'now', lambda: NOW)
    monkeypatch.setattr(dg, 'get_spot_quotes', lambda codes: (_ for _ in ()).throw(RuntimeError('offline')))
    monkeypatch.setattr(dg, 'completed_trade_day', lambda: date(2026, 11, 5))
    result = live_quotes.portfolio_quotes(db, ['600000', '000001'])
    assert result['items']['600000']['quote_kind'] == 'stored'
    assert result['items']['600000']['quote_at'] == '2026-11-05'
    assert result['items']['000001']['quote_kind'] == 'missing'
    assert result['errors']


def test_calendar_failure_fails_closed_without_portfolio_quote_500(db, monkeypatch):
    from app.portfolio import live_quotes
    monkeypatch.setattr(live_quotes, 'bounded_get_spot_quotes', lambda *a, **kw: (None, 'quote_failed'))
    monkeypatch.setattr(dg, 'completed_trade_day',
                        lambda: (_ for _ in ()).throw(RuntimeError('calendar down')))
    result = live_quotes.portfolio_quotes(db, ['600000'])
    assert result['items']['600000']['quote_kind'] == 'missing'
    assert '历史收盘不可用' in result['errors']


def test_quote_endpoint_only_reads_current_users_codes(db, monkeypatch):
    from app.portfolio import api, live_quotes
    db.add(User(id=2, username='other', password_hash='x', nickname='other'))
    db.add_all([Watchlist(user_id=1, code='600000', name='mine'),
                Watchlist(user_id=2, code='000001', name='other')])
    db.commit()
    monkeypatch.setattr(live_quotes, 'portfolio_quotes', lambda db, codes: {'codes': codes})
    assert api.portfolio_quote_api('watchlist', 1, db) == {'codes': ['600000']}


def test_timeouts_keep_fetch_capacity_bounded_until_worker_finishes(monkeypatch):
    import threading
    from app.portfolio import live_quotes
    entered = threading.Semaphore(0)
    release = threading.Event()
    def slow(codes):
        entered.release()
        release.wait(2)
        return {}, 'sina', None, None
    monkeypatch.setattr(dg, 'get_spot_quotes', slow)
    monkeypatch.setattr(live_quotes, 'WAIT_SECONDS', 0.01)
    try:
        assert live_quotes.fetch(['600000'])[1] == ['行情请求超时']
        assert entered.acquire(timeout=1)
        assert live_quotes.fetch(['000001'])[1] == ['行情请求超时']
        assert entered.acquire(timeout=1)
        assert live_quotes.fetch(['600001'])[1] == ['行情请求繁忙']
    finally:
        release.set()
        from app.shared import quote_fetch
        for _ in range(2):
            assert quote_fetch._SLOTS.acquire(timeout=2)
        for _ in range(2):
            quote_fetch._SLOTS.release()


def test_holding_does_not_present_pre_purchase_close_as_current(db, monkeypatch):
    db.add(Holding(user_id=1, code='600000', name='test', quantity=100,
                   buy_price=10, buy_date=date(2026, 11, 6), breakeven_price=10.01,
                   initial_stop_price=9, stop_price=9, highest_price=10, risk_r=1))
    db.add(DailyQuote(code='600000', trade_date=date(2026, 11, 5), close=13))
    db.commit()
    monkeypatch.setattr(dg, 'completed_trade_day', lambda: date(2026, 11, 5))
    row = holding_service.holdings_view(db, 1)[0]
    assert row['close'] is None
    assert row['unrealized_pct'] is None


def test_alert_batches_and_preserves_success_when_one_batch_fails(db, monkeypatch):
    from app.portfolio import live_quotes
    watches = [Watchlist(user_id=1, code=f'{n:06d}', name='test', take_profit=12)
               for n in range(1, 122)]
    db.add_all(watches)
    db.commit()
    calls = []
    def batch(codes, priority=False):
        calls.append((list(codes), priority))
        if len(calls) == 2:
            return {}, ['行情请求超时']
        return {code: {'price': 13, 'pct_chg': 5, 'realtime': True} for code in codes}, []
    monkeypatch.setattr(live_quotes, 'fetch', batch)
    monkeypatch.setattr(live_quotes, 'is_realtime', lambda quote: quote['realtime'])
    result = task_runner.task_watch_alert(db)
    assert [len(codes) for codes, _ in calls] == [50, 50, 21]
    assert all(priority for _, priority in calls)
    assert result['alerts'] == 71
    assert result['errors']


def test_alert_priority_has_separate_bounded_capacity(monkeypatch):
    import threading
    from app.shared import quote_fetch
    entered = threading.Semaphore(0)
    release = threading.Event()
    def slow(codes):
        entered.release()
        release.wait(2)
        return {}, 'sina', None, None
    monkeypatch.setattr(dg, 'get_spot_quotes', slow)
    try:
        assert quote_fetch.bounded_get_spot_quotes(['1'], timeout=0.01)[1] == 'quote_timeout'
        assert quote_fetch.bounded_get_spot_quotes(['2'], timeout=0.01)[1] == 'quote_timeout'
        assert quote_fetch.bounded_get_spot_quotes(['3'], timeout=0.01)[1] == 'quote_busy'
        assert quote_fetch.bounded_get_spot_quotes(['4'], timeout=0.01, priority=True)[1] == 'quote_timeout'
        assert quote_fetch.bounded_get_spot_quotes(['5'], timeout=0.01, priority=True)[1] == 'quote_busy'
    finally:
        release.set()
        for _ in range(2):
            assert quote_fetch._SLOTS.acquire(timeout=2)
        assert quote_fetch._ALERT_SLOTS.acquire(timeout=2)
        for _ in range(2):
            quote_fetch._SLOTS.release()
        quote_fetch._ALERT_SLOTS.release()


def test_alert_rechecks_quote_freshness_when_later_batches_take_time(db, monkeypatch):
    from app.portfolio import live_quotes
    db.add(Watchlist(user_id=1, code='600000', name='fresh', take_profit=12))
    db.commit()
    monkeypatch.setattr(live_quotes, 'now', lambda: NOW)
    monkeypatch.setattr(live_quotes, 'fetch', lambda codes, priority=False: ({
        '600000': {'price': 13, 'pct_chg': 5, 'realtime': True,
                   'quote_at': '2026-11-06T09:57:00+08:00'}}, []))
    result = task_runner.task_watch_alert(db)
    assert result['alerts'] == 0
    assert result['errors']


def test_stream_quote_carries_windows_and_expires_during_other_fetch(monkeypatch):
    from app.portfolio import live_quotes

    class Hub:
        def snapshot(self, codes):
            return {'max_age_seconds': 3.0, 'delayed_max_age_seconds': 15.0,
                    'items': {'600000': {'price': 10, 'quality': 'realtime',
                                         'provider': 'licensed', 'provider_time': NOW.isoformat(),
                                         'received_at': NOW.isoformat(), 'age_seconds': 0}}}

    monkeypatch.setattr(live_quotes.settings, 'marketdata_stream_enabled', True)
    monkeypatch.setattr(live_quotes, 'get_hub', lambda: Hub())
    monkeypatch.setattr(live_quotes, 'now', lambda: NOW.replace(second=5))
    monkeypatch.setattr(live_quotes, 'bounded_get_spot_quotes',
                        lambda *args, **kwargs: (None, 'quote_timeout'))
    quotes, errors = live_quotes.fetch(['600000', '000001'])
    quote = quotes['600000']
    assert quote['realtime_max_age_seconds'] == 3.0
    assert quote['delayed_max_age_seconds'] == 15.0
    assert quote['quote_quality'] == 'delayed'
    assert quote['realtime'] is False
    assert errors == ['行情请求超时']


def test_alert_rechecks_stream_specific_window(monkeypatch):
    from app.portfolio import live_quotes

    monkeypatch.setattr(live_quotes, 'now', lambda: NOW.replace(second=4))
    monkeypatch.setattr(live_quotes, '_trading', lambda _: True)
    quote = {'quote_kind': 'stream', 'quote_at': NOW.isoformat(), 'realtime': True,
             'realtime_max_age_seconds': 3.0}
    assert live_quotes.is_realtime(quote) is False
    assert live_quotes.is_realtime({**quote, 'realtime_max_age_seconds': 5.0}) is True
    assert live_quotes.is_realtime({**quote, 'realtime_max_age_seconds': None}) is False


def test_alert_fails_closed_when_calendar_is_unverified(db, monkeypatch):
    from app.portfolio import live_quotes
    db.add(Watchlist(user_id=1, code='600000', name='unknown-calendar', take_profit=12))
    db.commit()
    monkeypatch.setattr(live_quotes, 'now', lambda: NOW)
    monkeypatch.setattr(live_quotes, 'bounded_get_spot_quotes', lambda *args, **kwargs: (
        ({'600000': {'price': 13, 'pct_chg': 5,
                     'quote_at': '2026-11-06T09:59:30+08:00'}},
         'sina', '2026-11-06T09:59:30+08:00', 30), None))
    from app.screener import cockpit
    monkeypatch.setattr(cockpit, '_session', lambda _: {
        'state': 'trading', 'label': '交易中', 'calendar_verified': False})

    result = task_runner.task_watch_alert(db)

    assert result['alerts'] == 0
    assert result['errors']


def test_alert_commits_each_batch_and_rolls_back_failed_batch(db, monkeypatch):
    from app.portfolio import live_quotes
    db.add_all([Watchlist(user_id=1, code=f'{n:06d}', name='test', take_profit=12)
                for n in range(1, 52)])
    db.commit()
    commit = db.commit
    attempts = []
    def fail_first_commit():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('commit failed')
        return commit()
    monkeypatch.setattr(db, 'commit', fail_first_commit)
    monkeypatch.setattr(live_quotes, 'fetch', lambda codes, priority=False: (
        {code: {'price': 13, 'pct_chg': 5, 'realtime': True} for code in codes}, []))
    monkeypatch.setattr(live_quotes, 'is_realtime', lambda quote: quote['realtime'])
    result = task_runner.task_watch_alert(db)
    assert len(attempts) == 2
    assert result['alerts'] == 1
    assert result['errors']
    assert [a.code for a in db.execute(select(WatchAlertLog)).scalars()] == ['000051']


def test_alert_releases_sqlite_write_lock_before_next_fetch(tmp_path, monkeypatch):
    import sqlite3
    from app.portfolio import live_quotes
    path = tmp_path / 'locks.db'
    engine = create_engine(f'sqlite:///{path}')
    PortfolioBase.metadata.create_all(engine)
    ScreenerBase.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE lock_probe (value INTEGER)')
    with Session(engine, expire_on_commit=False) as session:
        session.add(User(id=1, username='lock_user', password_hash='x', nickname='q'))
        session.add_all([Watchlist(user_id=1, code=f'{n:06d}', name='test', take_profit=12)
                         for n in range(1, 52)])
        session.commit()
        calls = []
        def fetch(codes, priority=False):
            calls.append(1)
            if len(calls) == 2:
                with sqlite3.connect(path, timeout=0.1) as other:
                    other.execute('INSERT INTO lock_probe (value) VALUES (1)')
            return {code: {'price': 13, 'realtime': True} for code in codes}, []
        monkeypatch.setattr(live_quotes, 'fetch', fetch)
        monkeypatch.setattr(live_quotes, 'is_realtime', lambda quote: quote['realtime'])
        assert task_runner.task_watch_alert(session)['alerts'] == 51
        assert len(calls) == 2
    engine.dispose()


def test_alert_retries_busy_batch_after_a_timed_out_fetch(db, monkeypatch):
    from app.portfolio import live_quotes
    db.add_all([Watchlist(user_id=1, code=f'{n:06d}', name='test', take_profit=12)
                for n in range(1, 52)])
    db.commit()
    calls = []
    def fetch(codes, priority=False):
        calls.append(list(codes))
        if len(calls) == 1:
            return {}, ['行情请求超时']
        if len(calls) < 4:
            return {}, ['行情请求繁忙']
        return {codes[0]: {'price': 13, 'realtime': True}}, []
    monkeypatch.setattr(live_quotes, 'fetch', fetch)
    monkeypatch.setattr(live_quotes, 'is_realtime', lambda quote: quote['realtime'])
    monkeypatch.setattr(task_runner, 'WATCH_ALERT_BUSY_WAIT_SECONDS', 0.2, raising=False)
    monkeypatch.setattr(task_runner, 'WATCH_ALERT_BUSY_RETRY_SECONDS', 0.001, raising=False)
    result = task_runner.task_watch_alert(db)
    assert len(calls) == 4
    assert result['alerts'] == 1
    assert [a.code for a in db.execute(select(WatchAlertLog)).scalars()] == ['000051']
    assert any('超时' in message for message in result['errors'])


def test_alert_busy_retry_respects_wait_budget_and_reports_failure(db, monkeypatch):
    from app.portfolio import live_quotes
    db.add(Watchlist(user_id=1, code='600000', name='test', take_profit=12))
    db.commit()
    monkeypatch.setattr(live_quotes, 'fetch', lambda codes, priority=False: ({}, ['行情请求繁忙']))
    monkeypatch.setattr(task_runner, 'WATCH_ALERT_BUSY_WAIT_SECONDS', 0.01, raising=False)
    monkeypatch.setattr(task_runner, 'WATCH_ALERT_BUSY_RETRY_SECONDS', 0.002, raising=False)
    result = task_runner.task_watch_alert(db)
    assert result['alerts'] == 0
    assert any('繁忙' in message for message in result['errors'])
