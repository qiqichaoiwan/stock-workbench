from datetime import datetime, timedelta, timezone

from app.marketdata import futu as futu_module


UTC = timezone.utc


def test_futu_health_exposes_message_age_and_marks_silent_connection_stale(monkeypatch):
    provider = futu_module.FutuProvider()
    monkeypatch.setattr(futu_module.settings, "marketdata_delayed_max_age_seconds", 3.0)
    now = [100.0]
    monkeypatch.setattr(futu_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(provider, "heartbeat_required", lambda: True)
    provider._state = "connected"
    provider._connected_at_monotonic = 100.0
    provider._last_message_at = datetime.now(UTC).isoformat()
    now[0] = 116.0

    health = provider.health()

    assert health["state"] == "stale"
    assert health["message_age_seconds"] == 16.0
    assert health["heartbeat_timeout_seconds"] == 15.0


def test_futu_invalid_symbols_are_excluded_from_health(monkeypatch):
    monkeypatch.setattr(futu_module.settings, "marketdata_symbols", "600000,abc")
    provider = futu_module.FutuProvider()
    assert provider.symbols == ["SH.600000"]
    assert provider.invalid_symbols == ["abc"]


def test_futu_invalid_symbol_beyond_subscription_cap_is_still_rejected(monkeypatch):
    monkeypatch.setattr(futu_module.settings, "marketdata_symbols", "600000,abc")
    monkeypatch.setattr(futu_module.settings, "futu_max_symbols", 1)
    provider = futu_module.FutuProvider()
    assert provider.symbols == ["SH.600000"]
    assert provider.invalid_symbols == ["abc"]


def test_futu_api_role_is_never_reported_as_an_enabled_provider(monkeypatch):
    provider = futu_module.FutuProvider()
    monkeypatch.setattr(futu_module.settings, "marketdata_process_role", "api")
    monkeypatch.setattr(futu_module.settings, "marketdata_stream_enabled", True)
    monkeypatch.setattr(futu_module.settings, "futu_enabled", True)
    assert provider.health()["enabled"] is False


def test_futu_symbol_parser_accepts_ascii_digits_only():
    assert futu_module._symbol("600000") == "SH.600000"
    for value in ("²²²²²²", "SH.²²²²²²", "６０００００"):
        try:
            futu_module._symbol(value)
        except ValueError:
            pass
        else:  # pragma: no cover - defensive assertion branch
            raise AssertionError(f"non-ASCII symbol unexpectedly accepted: {value}")


def test_futu_heartbeat_is_suppressed_outside_a_share_sessions():
    assert futu_module.FutuProvider.heartbeat_required(
        datetime(2026, 11, 7, 3, 0, tzinfo=UTC)
    ) is False  # Saturday
    assert futu_module.FutuProvider.heartbeat_required(
        datetime(2026, 11, 6, 1, 30, tzinfo=UTC)
    ) is True  # Friday 09:30 Beijing
    assert futu_module.FutuProvider.heartbeat_required(
        datetime(2026, 11, 6, 4, 0, tzinfo=UTC)
    ) is False  # Friday 12:00 Beijing lunch


def test_futu_received_callback_resets_heartbeat(monkeypatch):
    provider = futu_module.FutuProvider()
    now = [10.0]
    monkeypatch.setattr(futu_module.time, "monotonic", lambda: now[0])
    provider._state = "stale"
    provider._error = "old"
    provider._received(None)

    assert provider.health()["state"] == "connected"
    assert provider.health()["message_age_seconds"] == 0.0
    assert provider.health()["error"] is None


def test_futu_error_state_method_is_not_shadowed_by_error_text():
    provider = futu_module.FutuProvider()

    provider._set_error("OpenD unavailable")

    assert provider.health()["state"] == "error"
    assert provider.health()["error"] == "OpenD unavailable"


def test_futu_ticker_change_uses_accepted_same_day_previous_close(monkeypatch):
    from app.marketdata.hub import QuoteHub

    now = datetime(2026, 11, 9, 2, tzinfo=UTC)
    clock = [now]
    hub = QuoteHub(now_fn=lambda: clock[0])
    monkeypatch.setattr(futu_module, 'get_hub', lambda: hub)
    monkeypatch.setenv('MARKETDATA_AUTHORIZED_PROVIDERS', 'futu')
    provider = futu_module.FutuProvider()
    assert provider.ingest_quote({'code': 'SH.600000', 'last_price': 10.1,
                                  'prev_close_price': 10}, now.isoformat())['accepted']
    tick_at = now
    assert provider.ingest_ticker({'code': 'SH.600000', 'price': 10.2,
                                   'sequence': 6977554163425089259}, tick_at.isoformat())['accepted']
    assert provider.ingest_ticker({'code': 'SH.600000', 'price': 10.3,
                                   'sequence': 6977554163425089260}, tick_at.isoformat())['accepted']
    quote = hub.latest()['items']['600000']
    assert round(quote['pct_chg'], 4) == 3.0
    assert quote['pct_chg_source'] == 'futu_previous_close'
    assert quote['pct_chg_reference_price'] == 10
    assert quote['pct_chg_reference_at'] == now.isoformat()
    assert quote['sequence'] is None
    assert quote['sequence_contiguous'] is False
    assert quote['provider_sequence'] == '6977554163425089260'

    # A reference from the previous date may not be reused for today's tick.
    clock[0] = now + timedelta(days=1)
    assert provider.ingest_ticker({'code': 'SH.600000', 'price': 10.3,
                                   'sequence': 6977554167720056556}, clock[0].isoformat())['accepted']
    quote = hub.latest()['items']['600000']
    assert quote['pct_chg'] is None
    assert 'pct_chg_reference_at' not in quote
    assert quote['sequence'] is None
    assert quote['provider_sequence'] == '6977554167720056556'


def test_futu_missing_previous_close_never_invents_change(monkeypatch):
    from app.marketdata.hub import QuoteHub

    now = datetime(2026, 11, 9, 2, tzinfo=UTC)
    hub = QuoteHub(now_fn=lambda: now)
    monkeypatch.setattr(futu_module, 'get_hub', lambda: hub)
    provider = futu_module.FutuProvider()
    assert provider.ingest_quote({'code': 'SH.600000', 'last_price': 10,
                                  'prev_close_price': 0}, now.isoformat())['accepted']
    assert hub.latest()['items']['600000']['pct_chg'] is None
