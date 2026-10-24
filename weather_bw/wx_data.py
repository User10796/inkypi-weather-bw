"""Fetch, cache and parse weather data.

Open-Meteo supplies the forecast; the NWS supplies alerts (US only). Both go
through ``fetch_json``, which retries with backoff because the Pi Zero 2 W's
Wi-Fi drops packets. The last good responses are cached in a JSON file, so a
network failure draws from the cache with a stale stamp instead of an error.

HTTP, sleep and the clock are all passed in, so tests run offline.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ALERTS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = "inkypi-weather-bw/1.0 (+https://github.com/User10796/inkypi-weather-bw)"

TIMEOUT = 15  # seconds per attempt
RETRIES = 2  # extra attempts after the first
BACKOFF = (2, 5)  # seconds to wait before retry 1 and retry 2
MIN_FETCH_INTERVAL = timedelta(minutes=10)  # reuse a fresh cache instead of refetching
CACHE_VERSION = 1

CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,"
    "wind_speed_10m,wind_direction_10m,is_day"
)
HOURLY_FIELDS = "temperature_2m,precipitation_probability,weather_code,is_day"
DAILY_FIELDS = (
    "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
    "sunrise,sunset,uv_index_max"
)

SEVERITY_RANK = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1}


class FetchError(Exception):
    """A request failed after all retries (or with a non-retryable status)."""

    def __init__(self, message, status=None, friendly=None):
        super().__init__(message)
        self.status = status
        self.friendly = friendly or message


def friendly_error(exc: Exception) -> str:
    """A short message fit for the panel, instead of a Python exception."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return "Weather service is rate-limiting requests"
        if exc.code >= 500:
            return f"Weather service error (HTTP {exc.code})"
        return f"Weather request rejected (HTTP {exc.code})"
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timed out" in text:
        return "Network timed out"
    if isinstance(exc, ValueError):
        return "Weather service sent a bad response"
    return "Can't reach the weather service"


class NoWeatherData(Exception):
    """The fetch failed and there is no cached forecast to fall back on."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http_get_json(url: str, headers: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code == 429 or exc.code >= 500
    return True  # timeouts, DNS, connection resets, truncated JSON


def fetch_json(url, headers=None, *, get=http_get_json, sleep=time.sleep, retries=RETRIES,
               backoff=BACKOFF, timeout=TIMEOUT) -> dict:
    """GET JSON with ``retries`` extra attempts; 4xx (except 429) fails at once."""
    headers = {"User-Agent": USER_AGENT, **(headers or {})}
    last = None
    for attempt in range(retries + 1):
        try:
            return get(url, headers, timeout)
        except Exception as exc:  # noqa: BLE001 - every failure is handled the same way
            last = exc
            if not _retryable(exc):
                break
            if attempt < retries:
                delay = backoff[min(attempt, len(backoff) - 1)]
                logger.info("weather_bw: %s failed (%s); retrying in %ss", url, exc, delay)
                sleep(delay)
    status = getattr(last, "code", None)
    raise FetchError(f"{type(last).__name__}: {last}", status=status, friendly=friendly_error(last)) from last


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------

def forecast_params(latitude: float, longitude: float, units: str, timezone: str) -> dict:
    metric = units == "metric"
    return {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "current": CURRENT_FIELDS,
        "hourly": HOURLY_FIELDS,
        "daily": DAILY_FIELDS,
        "temperature_unit": "celsius" if metric else "fahrenheit",
        "wind_speed_unit": "kmh" if metric else "mph",
        "timezone": timezone,
        "forecast_days": 7,
    }


def forecast_url(latitude, longitude, units, timezone) -> str:
    return FORECAST_URL + "?" + urllib.parse.urlencode(forecast_params(latitude, longitude, units, timezone))


def alerts_url(latitude, longitude) -> str:
    # The NWS rejects points with more than 4 decimal places.
    return f"{ALERTS_URL}?point={latitude:.4f},{longitude:.4f}"


def cache_key(latitude, longitude, units, timezone) -> str:
    safe_tz = timezone.replace("/", "_")
    return f"{latitude:.4f}_{longitude:.4f}_{units}_{safe_tz}".replace("-", "m")


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("version") == CACHE_VERSION:
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.warning("weather_bw: unreadable cache %s; ignoring it", path)
    return {"version": CACHE_VERSION}


def save_cache(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Alert:
    event: str
    severity: str
    headline: str
    ends: datetime | None  # aware


def parse_alerts(payload: dict | None, now: datetime) -> list[Alert]:
    """Active alerts, most severe first. Expired or non-"Actual" ones are dropped."""
    alerts = []
    for feature in (payload or {}).get("features") or []:
        props = (feature or {}).get("properties") or {}
        if props.get("status", "Actual") != "Actual":
            continue
        ends = _parse_time(props.get("ends")) or _parse_time(props.get("expires"))
        if ends is not None and ends.tzinfo is None:
            ends = None  # no UTC offset: can't compare it safely, so treat as open-ended
        if ends is not None and ends <= now:
            continue
        event = (props.get("event") or "").strip()
        if not event:
            continue
        alerts.append(Alert(event, props.get("severity") or "Unknown", props.get("headline") or "", ends))
    alerts.sort(key=lambda a: (-SEVERITY_RANK.get(a.severity, 0), a.ends or datetime.max.replace(tzinfo=now.tzinfo)))
    return alerts


# --------------------------------------------------------------------------
# Fetch with cache fallback
# --------------------------------------------------------------------------

@dataclass
class WeatherResult:
    forecast: dict
    fetched_at: datetime
    stale: bool
    alerts: list[Alert] = field(default_factory=list)
    error: str | None = None


def get_weather(latitude, longitude, units, timezone, now, cache_path, *, want_alerts=True,
                get=http_get_json, sleep=time.sleep) -> WeatherResult:
    """Fresh data if possible, else the cache (stale), else NoWeatherData."""
    cache = load_cache(cache_path)
    changed = False
    error = None
    network_down = False

    fetched_at = _parse_time(cache.get("forecast_fetched_at"))
    if fetched_at is not None and fetched_at > now:
        fetched_at = None  # clock went backwards; don't trust the cache's age
    fresh = fetched_at is not None and now - fetched_at < MIN_FETCH_INTERVAL and cache.get("forecast")

    if not fresh:
        try:
            cache["forecast"] = fetch_json(
                forecast_url(latitude, longitude, units, timezone), get=get, sleep=sleep
            )
            cache["forecast_fetched_at"] = now.isoformat()
            fetched_at = now
            changed = True
        except FetchError as exc:
            error = exc.friendly
            network_down = exc.status is None
            logger.warning("weather_bw: forecast fetch failed: %s", exc)
            if not cache.get("forecast"):
                raise NoWeatherData(error) from exc
            fetched_at = _parse_time(cache.get("forecast_fetched_at")) or now

    alerts: list[Alert] = []
    if want_alerts:
        alerts_at = _parse_time(cache.get("alerts_fetched_at"))
        alerts_fresh = alerts_at is not None and alerts_at <= now and now - alerts_at < MIN_FETCH_INTERVAL
        # If the forecast just failed on a network error, the NWS is almost
        # certainly unreachable too: use cached alerts rather than spend
        # another ~40 s retrying.
        if not alerts_fresh and not (error and network_down):
            try:
                cache["alerts"] = fetch_json(
                    alerts_url(latitude, longitude),
                    {"Accept": "application/geo+json"},
                    get=get,
                    sleep=sleep,
                )
                cache["alerts_fetched_at"] = now.isoformat()
                changed = True
            except FetchError as exc:
                if exc.status is not None and 400 <= exc.status < 500:
                    # Outside the US, or a point the NWS doesn't cover: no alerts.
                    cache["alerts"] = None
                    cache["alerts_fetched_at"] = now.isoformat()
                    changed = True
                else:
                    # Keep any cached alerts that haven't expired yet.
                    logger.warning("weather_bw: alerts fetch failed: %s", exc)
        alerts = parse_alerts(cache.get("alerts"), now)

    if changed:
        try:
            save_cache(cache_path, cache)
        except OSError as exc:
            logger.error("weather_bw: could not write cache %s: %s", cache_path, exc)

    stale = error is not None
    return WeatherResult(cache["forecast"], fetched_at, stale, alerts, error)


# --------------------------------------------------------------------------
# Parsing (tolerant of missing fields: anything absent becomes None)
# --------------------------------------------------------------------------

@dataclass
class Current:
    temperature: float | None = None
    feels_like: float | None = None
    humidity: float | None = None
    code: int | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None
    is_day: bool | None = None


@dataclass
class Hour:
    time: datetime  # naive, local
    temperature: float | None = None
    precip_probability: float | None = None
    code: int | None = None
    is_day: bool | None = None


@dataclass
class Day:
    date: date
    code: int | None = None
    high: float | None = None
    low: float | None = None
    precip_probability: float | None = None
    sunrise: datetime | None = None
    sunset: datetime | None = None
    uv_index: float | None = None


@dataclass
class Forecast:
    current: Current
    hours: list[Hour]
    days: list[Day]


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _code(value):
    number = _num(value)
    return int(number) if number is not None else None


def _flag(value):
    number = _num(value)
    return bool(number) if number is not None else None


def _column(block: dict, key: str, length: int) -> list:
    values = block.get(key)
    if not isinstance(values, list):
        return [None] * length
    return (values + [None] * length)[:length]


def parse_forecast(payload: dict, now_local: datetime, hours: int = 24, days: int = 7) -> Forecast:
    """``now_local`` is naive local time (Open-Meteo returns local times)."""
    payload = payload or {}
    cur = payload.get("current") or {}
    current = Current(
        temperature=_num(cur.get("temperature_2m")),
        feels_like=_num(cur.get("apparent_temperature")),
        humidity=_num(cur.get("relative_humidity_2m")),
        code=_code(cur.get("weather_code")),
        wind_speed=_num(cur.get("wind_speed_10m")),
        wind_direction=_num(cur.get("wind_direction_10m")),
        is_day=_flag(cur.get("is_day")),
    )

    hourly = payload.get("hourly") or {}
    times = [_parse_time(t) for t in hourly.get("time") or []]
    n = len(times)
    temps = _column(hourly, "temperature_2m", n)
    pops = _column(hourly, "precipitation_probability", n)
    codes = _column(hourly, "weather_code", n)
    flags = _column(hourly, "is_day", n)
    this_hour = now_local.replace(minute=0, second=0, microsecond=0)
    hour_list = [
        Hour(t, _num(temps[i]), _num(pops[i]), _code(codes[i]), _flag(flags[i]))
        for i, t in enumerate(times)
        if t is not None and t >= this_hour
    ][:hours]

    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    m = len(dates)
    columns = {
        key: _column(daily, key, m)
        for key in (
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_probability_max", "sunrise", "sunset", "uv_index_max",
        )
    }
    day_list = []
    for i, raw in enumerate(dates):
        try:
            day = date.fromisoformat(raw)
        except (TypeError, ValueError):
            continue
        if day < now_local.date():
            continue
        day_list.append(Day(
            date=day,
            code=_code(columns["weather_code"][i]),
            high=_num(columns["temperature_2m_max"][i]),
            low=_num(columns["temperature_2m_min"][i]),
            precip_probability=_num(columns["precipitation_probability_max"][i]),
            sunrise=_parse_time(columns["sunrise"][i]),
            sunset=_parse_time(columns["sunset"][i]),
            uv_index=_num(columns["uv_index_max"][i]),
        ))
    return Forecast(current, hour_list, day_list[:days])
