import json
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from snapshot_cases import load_fixture
from weather_bw import wx_data

NOW = datetime(2026, 10, 24, 9, 44, tzinfo=timezone(timedelta(hours=-4)))


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


class FakeHTTP:
    """Returns or raises scripted results per URL prefix, recording calls."""

    def __init__(self, forecast=None, alerts=None):
        self.forecast = list(forecast or [])
        self.alerts = list(alerts or [])
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers, timeout))
        queue = self.forecast if url.startswith(wx_data.FORECAST_URL) else self.alerts
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, Exception):
            raise result
        return result

    def count(self, prefix):
        return sum(1 for url, _, _ in self.calls if url.startswith(prefix))


# --------------------------------------------------------------------------
# fetch_json: timeout, retries, backoff
# --------------------------------------------------------------------------

def test_retries_twice_with_backoff_then_succeeds():
    sleeps = []
    http = FakeHTTP(forecast=[TimeoutError("t1"), OSError("reset"), {"ok": 1}])
    assert wx_data.fetch_json(wx_data.FORECAST_URL, get=http, sleep=sleeps.append) == {"ok": 1}
    assert len(http.calls) == 3
    assert sleeps == [2, 5]
    assert all(timeout == 15 for _, _, timeout in http.calls)
    assert all("inkypi-weather-bw" in headers["User-Agent"] for _, headers, _ in http.calls)


def test_gives_up_after_two_retries():
    sleeps = []
    http = FakeHTTP(forecast=[TimeoutError("down")])
    with pytest.raises(wx_data.FetchError):
        wx_data.fetch_json(wx_data.FORECAST_URL, get=http, sleep=sleeps.append)
    assert len(http.calls) == 3 and sleeps == [2, 5]


@pytest.mark.parametrize("code, attempts", [(400, 1), (404, 1), (429, 3), (500, 3), (503, 3)])
def test_4xx_fails_fast_but_429_and_5xx_retry(code, attempts):
    http = FakeHTTP(forecast=[http_error(code)])
    with pytest.raises(wx_data.FetchError) as info:
        wx_data.fetch_json(wx_data.FORECAST_URL, get=http, sleep=lambda s: None)
    assert len(http.calls) == attempts
    assert info.value.status == code


def test_truncated_json_is_retried():
    http = FakeHTTP(forecast=[json.JSONDecodeError("bad", "", 0), {"ok": 1}])
    assert wx_data.fetch_json(wx_data.FORECAST_URL, get=http, sleep=lambda s: None) == {"ok": 1}


# --------------------------------------------------------------------------
# Request URLs
# --------------------------------------------------------------------------

def test_forecast_url_requests_the_spec_fields():
    query = urllib.parse.parse_qs(urllib.parse.urlparse(
        wx_data.forecast_url(31.4505, -83.5085, "imperial", "America/New_York")).query)
    assert query["current"][0].split(",") == [
        "temperature_2m", "apparent_temperature", "relative_humidity_2m", "weather_code",
        "wind_speed_10m", "wind_direction_10m", "is_day"]
    assert query["hourly"][0].split(",") == ["temperature_2m", "precipitation_probability", "weather_code", "is_day"]
    assert query["daily"][0].split(",") == [
        "weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max",
        "sunrise", "sunset", "uv_index_max"]
    assert query["temperature_unit"] == ["fahrenheit"] and query["wind_speed_unit"] == ["mph"]
    assert query["timezone"] == ["America/New_York"] and query["forecast_days"] == ["7"]


def test_metric_units():
    query = urllib.parse.parse_qs(urllib.parse.urlparse(
        wx_data.forecast_url(1, 2, "metric", "UTC")).query)
    assert query["temperature_unit"] == ["celsius"] and query["wind_speed_unit"] == ["kmh"]


def test_alerts_url_uses_four_decimals():
    assert wx_data.alerts_url(31.450512, -83.50851) == (
        "https://api.weather.gov/alerts/active?point=31.4505,-83.5085")


# --------------------------------------------------------------------------
# get_weather: cache and stale fallback
# --------------------------------------------------------------------------

def call(tmp_path, http, now=NOW, alerts=True):
    return wx_data.get_weather(31.4505, -83.5085, "imperial", "America/New_York", now,
                               str(tmp_path / "cache.json"), want_alerts=alerts, get=http,
                               sleep=lambda s: None)


def test_fresh_fetch_writes_cache(tmp_path):
    fixture = load_fixture("sunny")
    http = FakeHTTP([fixture["forecast"]], [fixture["alerts"]])
    result = call(tmp_path, http)
    assert not result.stale and result.fetched_at == NOW and result.alerts == []
    cache = json.loads((tmp_path / "cache.json").read_text())
    assert cache["forecast"] == fixture["forecast"]
    # The alerts request asks for GeoJSON.
    alerts_call = [c for c in http.calls if c[0].startswith(wx_data.ALERTS_URL)][0]
    assert alerts_call[1]["Accept"] == "application/geo+json"


def test_recent_cache_is_reused_without_fetching(tmp_path):
    fixture = load_fixture("sunny")
    call(tmp_path, FakeHTTP([fixture["forecast"]], [fixture["alerts"]]))
    http = FakeHTTP([AssertionError("should not fetch")], [AssertionError("nope")])
    result = call(tmp_path, http, now=NOW + timedelta(minutes=9))
    assert http.calls == [] and not result.stale


def test_failure_falls_back_to_cache_as_stale(tmp_path):
    fixture = load_fixture("sunny")
    call(tmp_path, FakeHTTP([fixture["forecast"]], [fixture["alerts"]]))
    later = NOW + timedelta(hours=2)
    result = call(tmp_path, FakeHTTP([OSError("down")], [OSError("down")]), now=later)
    assert result.stale and result.fetched_at == NOW
    assert result.forecast == fixture["forecast"]
    assert result.error == "Can't reach the weather service"


def test_nws_is_skipped_when_the_network_is_down(tmp_path):
    storm = load_fixture("storm_alert")
    call(tmp_path, FakeHTTP([storm["forecast"]], [storm["alerts"]]))
    http = FakeHTTP([OSError("down")], [AssertionError("NWS should not be tried")])
    result = call(tmp_path, http, now=NOW + timedelta(hours=1))
    assert http.count(wx_data.ALERTS_URL) == 0
    assert [a.event for a in result.alerts] == ["Rip Current Statement"]  # from the cache


def test_nws_still_tried_when_open_meteo_returns_an_http_error(tmp_path):
    storm = load_fixture("storm_alert")
    call(tmp_path, FakeHTTP([storm["forecast"]], [storm["alerts"]]))
    http = FakeHTTP([http_error(503)], [storm["alerts"]])
    call(tmp_path, http, now=NOW + timedelta(hours=1))
    assert http.count(wx_data.ALERTS_URL) == 1


@pytest.mark.parametrize("exc, text", [
    (TimeoutError("x"), "Network timed out"),
    (urllib.error.URLError("_ssl.c:1015: The handshake operation timed out"), "Network timed out"),
    (urllib.error.URLError("Name or service not known"), "Can't reach the weather service"),
    (http_error(429), "Weather service is rate-limiting requests"),
    (http_error(502), "Weather service error (HTTP 502)"),
    (json.JSONDecodeError("bad", "", 0), "Weather service sent a bad response"),
])
def test_friendly_errors(exc, text):
    assert wx_data.friendly_error(exc) == text


def test_failure_without_cache_raises_no_weather_data(tmp_path):
    with pytest.raises(wx_data.NoWeatherData):
        call(tmp_path, FakeHTTP([OSError("down")], [OSError("down")]))


def test_corrupt_cache_is_ignored(tmp_path):
    (tmp_path / "cache.json").write_text("{not json")
    fixture = load_fixture("sunny")
    assert not call(tmp_path, FakeHTTP([fixture["forecast"]], [fixture["alerts"]])).stale


def test_clock_going_backwards_forces_a_refetch(tmp_path):
    fixture = load_fixture("sunny")
    call(tmp_path, FakeHTTP([fixture["forecast"]], [fixture["alerts"]]), now=NOW + timedelta(days=30))
    http = FakeHTTP([fixture["forecast"]], [fixture["alerts"]])
    call(tmp_path, http, now=NOW)
    assert http.count(wx_data.FORECAST_URL) == 1


def test_alerts_4xx_means_no_alerts_not_an_error(tmp_path):
    fixture = load_fixture("sunny")
    result = call(tmp_path, FakeHTTP([fixture["forecast"]], [http_error(404)]))
    assert result.alerts == [] and not result.stale


def test_alerts_outage_keeps_unexpired_cached_alerts(tmp_path):
    storm = load_fixture("storm_alert")
    call(tmp_path, FakeHTTP([storm["forecast"]], [storm["alerts"]]))
    later = NOW + timedelta(hours=1)
    result = call(tmp_path, FakeHTTP([storm["forecast"]], [OSError("nws down")]), now=later)
    assert [a.event for a in result.alerts] == ["Rip Current Statement"]
    # Once it has ended, the cached alert disappears even though the NWS is still down.
    after_end = datetime(2026, 10, 26, 7, 0, tzinfo=timezone(timedelta(hours=-4)))
    result = call(tmp_path, FakeHTTP([storm["forecast"]], [OSError("nws down")]), now=after_end)
    assert result.alerts == []


def test_alerts_skipped_when_disabled(tmp_path):
    fixture = load_fixture("storm_alert")
    http = FakeHTTP([fixture["forecast"]], [fixture["alerts"]])
    assert call(tmp_path, http, alerts=False).alerts == []
    assert http.count(wx_data.ALERTS_URL) == 0


# --------------------------------------------------------------------------
# Alerts parsing
# --------------------------------------------------------------------------

def feature(event, severity="Moderate", ends="2026-10-26T06:00:00-04:00", expires=None, status="Actual"):
    return {"properties": {"event": event, "severity": severity, "ends": ends, "expires": expires,
                           "status": status, "headline": event}}


def test_alerts_sorted_most_severe_first_and_expired_dropped():
    payload = {"features": [
        feature("Small Craft Advisory", "Minor"),
        feature("Tornado Warning", "Extreme", ends="2026-10-24T11:00:00-04:00"),
        feature("Flood Warning", "Severe"),
        feature("Old Warning", "Extreme", ends="2026-10-24T08:00:00-04:00"),
        feature("Test Message", "Extreme", status="Test"),
    ]}
    assert [a.event for a in wx_data.parse_alerts(payload, NOW)] == [
        "Tornado Warning", "Flood Warning", "Small Craft Advisory"]


def test_alert_uses_ends_before_expires():
    # The recorded San Juan alert: the message expired at 12:15 but the event runs to Monday.
    payload = {"features": [feature("Rip Current Statement", ends="2026-10-26T06:00:00-04:00",
                                    expires="2026-10-24T09:00:00-04:00")]}
    assert len(wx_data.parse_alerts(payload, NOW)) == 1
    payload = {"features": [feature("Gale Warning", ends=None, expires="2026-10-24T09:00:00-04:00")]}
    assert wx_data.parse_alerts(payload, NOW) == []


@pytest.mark.parametrize("payload", [None, {}, {"features": None}, {"features": [None, {}, {"properties": {}}]}])
def test_alerts_tolerate_junk(payload):
    assert wx_data.parse_alerts(payload, NOW) == []


# --------------------------------------------------------------------------
# Forecast parsing
# --------------------------------------------------------------------------

def local_now(fixture):
    return datetime.fromisoformat(fixture["recorded_at"]).replace(tzinfo=None)


@pytest.mark.parametrize("scenario", ["sunny", "rain", "storm_alert", "missing"])
def test_parse_recorded_fixtures(scenario):
    fixture = load_fixture(scenario)
    now = local_now(fixture)
    forecast = wx_data.parse_forecast(fixture["forecast"], now, hours=24, days=7)
    assert len(forecast.hours) == 24 and len(forecast.days) == 7
    assert forecast.hours[0].time == now.replace(minute=0, second=0)
    assert forecast.days[0].date == now.date()
    assert forecast.current.temperature is not None


def test_hours_and_days_options():
    fixture = load_fixture("sunny")
    forecast = wx_data.parse_forecast(fixture["forecast"], local_now(fixture), hours=12, days=5)
    assert len(forecast.hours) == 12 and len(forecast.days) == 5


def test_missing_fields_become_none():
    fixture = load_fixture("missing")
    forecast = wx_data.parse_forecast(fixture["forecast"], local_now(fixture))
    assert forecast.current.feels_like is None
    assert forecast.current.wind_direction is None
    assert all(d.uv_index is None and d.sunrise is None for d in forecast.days)
    assert forecast.days[1].low is None and forecast.days[2].code is None
    assert forecast.days[0].sunset is not None


def test_empty_payload():
    forecast = wx_data.parse_forecast({}, datetime(2026, 10, 24, 9))
    assert forecast.hours == [] and forecast.days == [] and forecast.current.temperature is None


def test_bad_values_are_ignored():
    payload = {"current": {"temperature_2m": "warm", "weather_code": 2.0, "is_day": True},
               "hourly": {"time": ["2026-10-24T09:00", "garbage"], "temperature_2m": [70]},
               "daily": {"time": ["2026-10-24", "nope"], "temperature_2m_max": [80, 81]}}
    forecast = wx_data.parse_forecast(payload, datetime(2026, 10, 24, 9, 30))
    assert forecast.current.temperature is None and forecast.current.code == 2
    assert forecast.current.is_day is None  # a JSON bool is not 0/1
    assert len(forecast.hours) == 1 and forecast.hours[0].temperature == 70
    assert len(forecast.days) == 1 and forecast.days[0].high == 80


def test_alert_without_utc_offset_does_not_crash():
    payload = {"features": [feature("Flood Warning", "Severe", ends="2026-10-26T06:00:00"),
                            feature("Wind Advisory", "Moderate")]}
    alerts = wx_data.parse_alerts(payload, NOW)
    assert [a.event for a in alerts] == ["Flood Warning", "Wind Advisory"]
    assert alerts[0].ends is None
