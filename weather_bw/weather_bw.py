"""Weather (B&W): a crisp black-and-white weather board for 1-bit e-ink."""

import dataclasses
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugins.base_plugin.base_plugin import BasePlugin

from . import wx_data
from . import wx_render as render

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "America/New_York"
CACHE_DIR_NAME = "weather_bw_cache"


def _is_on(value) -> bool:
    return str(value).strip().lower() in ("true", "on", "1", "yes")


def _choice(value, options, default):
    return value if value in options else default


def _coordinate(value, limit):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if -limit <= number <= limit else None


def cache_dir(device_config) -> str:
    """Next to InkyPi's device.json (src/config/), which survives InkyPi
    updates and plugin reinstalls."""
    config_file = getattr(device_config, "config_file", None)
    if isinstance(config_file, str) and config_file:
        return os.path.join(os.path.dirname(config_file), CACHE_DIR_NAME)
    return os.path.join(os.path.expanduser("~"), ".local", "state", "inkypi", CACHE_DIR_NAME)


def stale_stamp(fetched_at: datetime, now: datetime, units: str) -> str:
    minutes = max(0, int((now - fetched_at).total_seconds() // 60))
    if minutes < 60:
        ago = f"{minutes} min ago"
    elif minutes < 48 * 60:
        ago = f"{minutes // 60} h ago"
    else:
        ago = f"{minutes // (24 * 60)} days ago"
    local = fetched_at.astimezone(now.tzinfo)
    when = render.fmt_time(local.replace(tzinfo=None), units)
    if local.date() != now.date():
        when = local.strftime("%a ") + when
    return f"Updated {when} ({ago})"


def _local_alert(alert, tz):
    """The NWS reports times in the issuing office's offset; show them in ours."""
    if alert.ends is None:
        return alert
    return dataclasses.replace(alert, ends=alert.ends.astimezone(tz))


class WeatherBW(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params["layouts"] = list(render.LAYOUTS)
        return template_params

    # Overridable seams (tests inject fakes).
    def now(self, tz):
        return datetime.now(tz)

    def http_get(self, url, headers, timeout):
        return wx_data.http_get_json(url, headers, timeout)

    def sleep(self, seconds):
        time.sleep(seconds)

    def generate_image(self, settings, device_config):
        latitude = _coordinate(settings.get("latitude"), 90)
        longitude = _coordinate(settings.get("longitude"), 180)
        if latitude is None or longitude is None:
            raise RuntimeError("Enter a latitude and longitude in the Weather (B&W) settings.")

        width, height = device_config.get_resolution()
        size = (width, height)
        if device_config.get_config("orientation") == "vertical":
            size = (height, width)

        tz_name = device_config.get_config("timezone", default=DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            tz_name, tz = DEFAULT_TIMEZONE, ZoneInfo(DEFAULT_TIMEZONE)
        now = self.now(tz)

        units = _choice(settings.get("units"), ("imperial", "metric"), "imperial")
        layout = _choice(settings.get("layout"), render.LAYOUTS, "dashboard")
        hours = int(_choice(settings.get("hours"), ("12", "24"), "24"))
        days = int(_choice(settings.get("days"), ("5", "7"), "7"))
        font_style = _choice(settings.get("font_style"), tuple(render.FONT_STYLES), render.DEFAULT_FONT_STYLE)
        label = (settings.get("location_label") or "").strip()
        # An unchecked checkbox isn't posted at all, so "missing" only means
        # "off" once the form has been saved (form_version is always posted).
        show_alerts = _is_on(settings.get("show_alerts")) if "form_version" in settings else True

        key = wx_data.cache_key(latitude, longitude, units, tz_name)
        cache_path = os.path.join(cache_dir(device_config), f"{key}.json")
        try:
            result = wx_data.get_weather(
                latitude, longitude, units, tz_name, now, cache_path,
                want_alerts=show_alerts, get=self.http_get, sleep=self.sleep,
            )
        except wx_data.NoWeatherData as exc:
            return render.render_error(size, "Weather unavailable", str(exc), font_style, label)

        now_local = now.replace(tzinfo=None)
        forecast = wx_data.parse_forecast(result.forecast, now_local, hours=hours, days=days)
        page = render.Page(
            size=size,
            layout=layout,
            font_style=font_style,
            units=units,
            label=label,
            date_text=f"{now:%a %b} {now.day}",
            stale_text=stale_stamp(result.fetched_at, now, units) if result.stale else None,
            alert=_local_alert(result.alerts[0], tz) if show_alerts and result.alerts else None,
            now_local=now_local,
        )
        return render.render(page, forecast)
