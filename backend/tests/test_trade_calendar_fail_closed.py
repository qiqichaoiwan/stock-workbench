from datetime import date

from app.scheduler import _guarded
from app.shared import data_gateway as dg


def test_is_trade_day_fails_closed_on_calendar_error(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("calendar timeout")

    monkeypatch.setattr(dg, "get_trade_calendar", broken)
    assert dg.is_trade_day(date(2026, 11, 6)) is False


def test_is_trade_day_fails_closed_on_empty_calendar(monkeypatch):
    monkeypatch.setattr(dg, "get_trade_calendar", lambda *_args, **_kwargs: [])
    assert dg.is_trade_day(date(2026, 11, 6)) is False


def test_scheduler_skips_when_calendar_is_unverified(monkeypatch):
    called = []
    monkeypatch.setattr(dg, "is_trade_day", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("app.scheduler.run_task", lambda *args, **kwargs: called.append((args, kwargs)))

    _guarded("sync_quotes")

    assert called == []
