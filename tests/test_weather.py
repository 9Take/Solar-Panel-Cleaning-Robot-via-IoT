"""Open-Meteo weather for the dashboard: request, parsing, errors, config, page."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app import weather
from app.config import Settings
from app.dashboard import data

TZ = ZoneInfo("Asia/Bangkok")
REPLY = {
    "current": {"time": "2026-10-01T13:15", "interval": 900,
                "temperature_2m": 33.4, "cloud_cover": 40, "relative_humidity_2m": 61},
    "hourly": {"time": ["2026-10-01T11:00", "2026-10-01T12:00", "2026-10-01T13:00", "2026-10-01T14:00"],
               "temperature_2m": [31.0, 32.5, 33.4, None],
               "cloud_cover": [20, 35, 40, 70],
               "relative_humidity_2m": [70, 65, 61, 66]},
}


def fake_transport(body):
    seen = []

    def send(url):
        seen.append(url)
        return body if isinstance(body, bytes) else json.dumps(body).encode()
    return send, seen


def test_fetch_asks_for_the_three_variables_and_parses_local_times():
    send, seen = fake_transport(REPLY)
    w = weather.fetch(14.062, 100.3334, "Asia/Bangkok", past_days=7, transport=send)
    assert "latitude=14.062" in seen[0] and "past_days=7" in seen[0]
    for v in ("temperature_2m", "cloud_cover", "relative_humidity_2m"):
        assert v in seen[0]
    assert w.at == datetime(2026, 10, 1, 13, 15, tzinfo=TZ)
    assert w.current == {"temperature_2m": 33.4, "cloud_cover": 40, "relative_humidity_2m": 61}
    assert w.hourly[0] == {"time": datetime(2026, 10, 1, 11, tzinfo=TZ),
                           "temperature_2m": 31.0, "cloud_cover": 20, "relative_humidity_2m": 70}
    assert w.hourly[3]["temperature_2m"] is None          # gaps stay gaps, never filled in


@pytest.mark.parametrize("body, message", [
    ({"error": True, "reason": "Latitude must be in range of -90 to 90°."}, "Latitude must be"),
    (b"<html>bad gateway</html>", "non-JSON"),
    ({"current": {}}, "unexpected reply format"),
])
def test_errors_become_weather_error(body, message):
    send, _ = fake_transport(body)
    with pytest.raises(weather.WeatherError, match=message):
        weather.fetch(14, 100, "Asia/Bangkok", transport=send)


def test_weather_history_keeps_only_the_range_without_forecast():
    w = weather.parse(json.dumps(REPLY).encode(), TZ)
    rows = data.weather_history(w, datetime(2026, 10, 1, 12, tzinfo=TZ), datetime(2026, 10, 1, 13, 15, tzinfo=TZ))
    assert [r["time"].hour for r in rows] == [12, 13]


def test_weather_label():
    assert data.weather_label(None, "%") == "–"
    assert data.weather_label(40, "%") == "40 %"
    assert data.weather_label(33.44, "°C", 1) == "33.4 °C"


def test_weather_settings(monkeypatch):
    monkeypatch.setenv("PLC_HOST", "10.0.0.5")
    monkeypatch.setenv("WEATHER_LAT", "")
    monkeypatch.setenv("WEATHER_LON", "")
    s = Settings(_env_file=None)
    assert s.weather_lat is None and s.weather_lon is None      # empty = weather off
    monkeypatch.setenv("WEATHER_LAT", "14.0620")
    monkeypatch.setenv("WEATHER_LON", "100.3334")
    assert Settings(_env_file=None).weather_lat == pytest.approx(14.062)
    monkeypatch.setenv("WEATHER_LAT", "91")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --- on the page -------------------------------------------------------------------------

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
from tests.test_dashboard import PAGE, page_env  # noqa: E402,F401  (fixture reuse)


def _run(monkeypatch, fetch):
    import streamlit as st
    st.cache_data.clear()
    monkeypatch.setenv("WEATHER_LAT", "14.0620")
    monkeypatch.setenv("WEATHER_LON", "100.3334")
    monkeypatch.setenv("WEATHER_PLACE", "Test site")
    monkeypatch.setattr(weather, "fetch", fetch)
    at = AppTest.from_file(PAGE, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_page_shows_current_weather(page_env, monkeypatch):
    at = _run(monkeypatch, lambda lat, lon, tz: weather.parse(json.dumps(REPLY).encode(), ZoneInfo(tz)))
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Cloud cover"] == "40 %"
    assert metrics["Temperature"] == "33.4 °C"
    assert metrics["Humidity"] == "61 %"
    assert any("Test site" in m.value for m in at.markdown)


def test_page_survives_open_meteo_failure(page_env, monkeypatch):
    def fail(lat, lon, tz):
        raise weather.WeatherError("cannot reach Open-Meteo: timed out")
    at = _run(monkeypatch, fail)
    assert any("Weather unavailable" in w.value for w in at.warning)
    assert "Cloud cover" not in {m.label for m in at.metric}


def test_page_without_location_turns_weather_off(page_env):
    at = AppTest.from_file(PAGE, default_timeout=60)
    at.run()
    assert not at.exception
    assert any("Weather off" in c.value for c in at.caption)
