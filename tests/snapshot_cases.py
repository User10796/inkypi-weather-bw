"""Shared by tests and scripts/render_snapshots.py: load a recorded fixture
and render it through the real plugin class with a fake clock and fake HTTP
that replays the recording."""

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime

import conftest  # noqa: F401  (registers the BasePlugin stub)
from weather_bw import weather_bw as wb

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SCENARIOS = ("sunny", "rain", "storm_alert", "missing")
LAYOUTS = ("dashboard", "hourly", "week")
ORIENTATIONS = ("landscape", "portrait")


def load_fixture(name):
    with open(os.path.join(FIXTURES, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


class FakeDeviceConfig:
    def __init__(self, config_dir, timezone, orientation="horizontal", resolution=(800, 480)):
        self.config_file = os.path.join(config_dir, "device.json")
        self._config = {"timezone": timezone, "orientation": orientation}
        self._resolution = list(resolution)

    def get_resolution(self):
        return self._resolution

    def get_config(self, key=None, default=None):
        return self._config.get(key, default)


class ReplayPlugin(wb.WeatherBW):
    """The real plugin, with the clock, HTTP and sleep replaced."""

    def __init__(self, fixture=None, fail=False):
        super().__init__({"id": "weather_bw"})
        self.fixture = fixture
        self.fail = fail
        self.fake_now = datetime.fromisoformat(fixture["recorded_at"]) if fixture else None
        self.requests = []
        self.sleeps = []

    def now(self, tz):
        return self.fake_now.astimezone(tz)

    def http_get(self, url, headers, timeout):
        self.requests.append((url, dict(headers), timeout))
        if self.fail:
            raise OSError("network is unreachable")
        if url.startswith(wb.wx_data.FORECAST_URL):
            return self.fixture["forecast"]
        if url.startswith(wb.wx_data.ALERTS_URL):
            return self.fixture["alerts"]
        raise AssertionError(f"unexpected URL {url}")

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def settings_for(fixture, **extra):
    loc = fixture["location"]
    base = {
        "latitude": str(loc["latitude"]),
        "longitude": str(loc["longitude"]),
        "location_label": loc["label"],
        "units": fixture.get("units", "imperial"),
        "layout": "dashboard",
        "hours": "24",
        "days": "7",
        "show_alerts": "true",
        "font_style": "sans",
        "form_version": "1",
    }
    base.update(extra)
    return base


@dataclass(frozen=True)
class Case:
    scenario: str
    layout: str
    orientation: str

    @property
    def filename(self):
        return f"{self.scenario}-{self.layout}-{self.orientation}.png"


CASES = [Case(s, l, o) for s in SCENARIOS for l in LAYOUTS for o in ORIENTATIONS]


def render_case(case, config_dir=None, **extra):
    fixture = load_fixture(case.scenario)
    with tempfile.TemporaryDirectory() as tmp:
        device = FakeDeviceConfig(
            config_dir or tmp,
            fixture["timezone"],
            orientation="vertical" if case.orientation == "portrait" else "horizontal",
        )
        return ReplayPlugin(fixture).generate_image(settings_for(fixture, **{"layout": case.layout, **extra}), device)
