"""End-to-end through the plugin class, replaying recorded fixtures."""

import copy
import json
import os
from datetime import timedelta

import pytest

from snapshot_cases import FakeDeviceConfig, ReplayPlugin, load_fixture, settings_for
from weather_bw import weather_bw as wb
from weather_bw import wx_data


def header_black_ratio(image):
    header = image.convert("L").crop((0, 0, image.width, 36))
    return header.histogram()[0] / (header.width * header.height)


def device(tmp_path, fixture, **kw):
    return FakeDeviceConfig(str(tmp_path / "config"), fixture["timezone"], **kw)


def test_fetches_forecast_and_alerts_with_spec_headers(tmp_path):
    fixture = load_fixture("sunny")
    plugin = ReplayPlugin(fixture)
    image = plugin.generate_image(settings_for(fixture), device(tmp_path, fixture))
    assert image.size == (800, 480)
    urls = [u for u, _, _ in plugin.requests]
    assert urls == [fixture["forecast_url"], fixture["alerts_url"]]
    for _, headers, timeout in plugin.requests:
        assert "User-Agent" in headers and timeout == 15


def test_cache_lives_next_to_device_json(tmp_path):
    fixture = load_fixture("sunny")
    ReplayPlugin(fixture).generate_image(settings_for(fixture), device(tmp_path, fixture))
    files = os.listdir(tmp_path / "config" / wb.CACHE_DIR_NAME)
    assert len(files) == 1 and files[0].endswith(".json")


@pytest.mark.parametrize("scenario, banner", [("sunny", False), ("rain", False), ("storm_alert", True), ("missing", False)])
def test_alert_banner_only_when_nws_has_an_active_alert(tmp_path, scenario, banner):
    fixture = load_fixture(scenario)
    image = ReplayPlugin(fixture).generate_image(settings_for(fixture), device(tmp_path, fixture))
    assert (header_black_ratio(image) > 0.5) is banner


def test_expired_alert_shows_no_banner(tmp_path):
    fixture = load_fixture("storm_alert")
    plugin = ReplayPlugin(fixture)
    plugin.fake_now += timedelta(days=3)  # past the alert's end, Oct 26 06:00
    image = plugin.generate_image(settings_for(fixture), device(tmp_path, fixture))
    assert header_black_ratio(image) < 0.2


def test_alerts_unchecked_skips_nws_and_banner(tmp_path):
    fixture = load_fixture("storm_alert")
    plugin = ReplayPlugin(fixture)
    settings = settings_for(fixture)
    del settings["show_alerts"]  # unchecked boxes aren't posted
    image = plugin.generate_image(settings, device(tmp_path, fixture))
    assert header_black_ratio(image) < 0.2
    assert all(not u.startswith(wx_data.ALERTS_URL) for u, _, _ in plugin.requests)


def test_alerts_default_on_before_the_form_is_saved(tmp_path):
    fixture = load_fixture("storm_alert")
    settings = settings_for(fixture)
    del settings["show_alerts"], settings["form_version"]
    image = ReplayPlugin(fixture).generate_image(settings, device(tmp_path, fixture))
    assert header_black_ratio(image) > 0.5


def test_nws_failure_does_not_break_the_board(tmp_path):
    fixture = load_fixture("storm_alert")

    class NwsDown(ReplayPlugin):
        def http_get(self, url, headers, timeout):
            if url.startswith(wx_data.ALERTS_URL):
                raise OSError("nws down")
            return super().http_get(url, headers, timeout)

    plugin = NwsDown(fixture)
    image = plugin.generate_image(settings_for(fixture), device(tmp_path, fixture))
    assert image.size == (800, 480) and header_black_ratio(image) < 0.2
    assert plugin.sleeps == [2, 5]  # the NWS call was retried with backoff


def test_network_failure_uses_cache_with_stale_stamp(tmp_path):
    fixture = load_fixture("sunny")
    dev = device(tmp_path, fixture)
    fresh = ReplayPlugin(fixture).generate_image(settings_for(fixture), dev)

    offline = ReplayPlugin(fixture, fail=True)
    offline.fake_now += timedelta(hours=2)
    stale = offline.generate_image(settings_for(fixture), dev)
    assert stale.size == (800, 480)
    assert offline.sleeps == [2, 5]  # forecast retried twice; NWS skipped while offline
    # Same data, but the header now carries the stale stamp instead of the date.
    assert fresh.crop((0, 0, 800, 36)).tobytes() != stale.crop((0, 0, 800, 36)).tobytes()
    assert fresh.crop((0, 36, 800, 210)).tobytes() == stale.crop((0, 36, 800, 210)).tobytes()


def test_stale_stamp_text():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    fetched = datetime(2026, 10, 24, 7, 15, tzinfo=tz)
    assert wb.stale_stamp(fetched, fetched + timedelta(hours=2, minutes=5), "imperial") == "Updated 7:15 AM (2 h ago)"
    assert wb.stale_stamp(fetched, fetched + timedelta(minutes=25), "metric") == "Updated 07:15 (25 min ago)"
    assert wb.stale_stamp(fetched, fetched + timedelta(days=3), "imperial") == "Updated Sat 7:15 AM (3 days ago)"


def test_network_failure_without_cache_shows_error_screen(tmp_path):
    fixture = load_fixture("sunny")
    image = ReplayPlugin(fixture, fail=True).generate_image(settings_for(fixture), device(tmp_path, fixture))
    assert image.size == (800, 480)
    assert sum(image.convert("L").histogram()[1:255]) == 0


def test_cache_survives_a_settings_save(tmp_path):
    """InkyPi replaces the settings dict on every save; the cache is keyed by
    location, so a new dict for the same place reuses it."""
    fixture = load_fixture("sunny")
    dev = device(tmp_path, fixture)
    ReplayPlugin(fixture).generate_image(settings_for(fixture), dev)
    resaved = copy.deepcopy(settings_for(fixture, layout="week", font_style="serif"))
    plugin = ReplayPlugin(fixture, fail=True)
    plugin.fake_now += timedelta(minutes=5)
    plugin.generate_image(resaved, dev)
    assert plugin.requests == []  # served from the fresh cache, no network at all


def test_missing_coordinates_raise_for_the_web_ui(tmp_path):
    fixture = load_fixture("sunny")
    for bad in ({"latitude": ""}, {"longitude": "abc"}, {"latitude": "95"}):
        with pytest.raises(RuntimeError, match="latitude and longitude"):
            ReplayPlugin(fixture).generate_image(settings_for(fixture, **bad), device(tmp_path, fixture))


def test_portrait_orientation(tmp_path):
    fixture = load_fixture("sunny")
    image = ReplayPlugin(fixture).generate_image(settings_for(fixture), device(tmp_path, fixture, orientation="vertical"))
    assert image.size == (480, 800)


def test_bad_timezone_falls_back(tmp_path):
    fixture = copy.deepcopy(load_fixture("sunny"))
    fixture["timezone"] = "Mars/Olympus"
    assert ReplayPlugin(fixture).generate_image(settings_for(fixture), device(tmp_path, fixture)).size == (800, 480)


def test_unknown_options_fall_back_to_defaults(tmp_path):
    fixture = load_fixture("sunny")
    settings = settings_for(fixture, layout="radar", units="kelvin", hours="99", days="0", font_style="comic")
    assert ReplayPlugin(fixture).generate_image(settings, device(tmp_path, fixture)).size == (800, 480)


def test_settings_template_params():
    params = wb.WeatherBW({"id": "weather_bw"}).generate_settings_template()
    assert params["layouts"] == ["dashboard", "hourly", "week"]


def test_plugin_info_matches_class():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    info = json.load(open(os.path.join(root, "weather_bw", "plugin-info.json")))
    assert info["id"] == "weather_bw" and hasattr(wb, info["class"])


def test_alert_end_time_shown_in_device_timezone(tmp_path):
    """San Juan's alert ends 06:00 at -04:00. A board set to Central time
    must say 5 AM, not 6 AM."""
    from weather_bw import wx_render

    fixture = load_fixture("storm_alert")
    seen = []
    real = wx_render.draw_header

    def spy(pen, rect, label, date_text, alert, *args):
        seen.append(alert)
        return real(pen, rect, label, date_text, alert, *args)

    wx_render.draw_header = spy
    try:
        central = copy.deepcopy(fixture)
        central["timezone"] = "America/Chicago"
        ReplayPlugin(central).generate_image(settings_for(central), device(tmp_path, central))
    finally:
        wx_render.draw_header = real
    assert wx_render.fmt_time(seen[0].ends.replace(tzinfo=None), "imperial") == "5 AM"
