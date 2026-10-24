"""Record API fixtures from the live Open-Meteo and NWS APIs.

    python scripts/record_fixtures.py

Writes tests/fixtures/<scenario>.json. Each file holds the exact responses
the plugin would receive (same request URLs, built by weather_bw.wx_data),
plus the moment of recording, which tests use as their fake clock.

Scenarios and the real places used (chosen on 2026-10-24 by surveying ~50
US cities for matching conditions; edit LOCATIONS to re-record elsewhere):
  sunny        Tifton, GA       clear
  rain         Anchorage, AK    rain overnight (also exercises night icons)
  storm_alert  San Juan, PR     thunderstorm in the next 24 h + an active NWS alert
  missing      derived from "sunny" with fields deleted/nulled (see MISSING_EDITS)
"""

import copy
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from weather_bw import wx_data  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")

LOCATIONS = {
    "sunny": ("Tifton", 31.4505, -83.5085, "America/New_York"),
    "rain": ("Anchorage", 61.2181, -149.9003, "America/Anchorage"),
    "storm_alert": ("San Juan", 18.4655, -66.1057, "America/Puerto_Rico"),
}

# (block, key) to delete, or (block, key, index) to null out.
MISSING_EDITS = [
    ("current", "apparent_temperature"),
    ("current", "wind_direction_10m"),
    ("daily", "uv_index_max"),
    ("daily", "sunrise"),
    ("hourly", "precipitation_probability", 2),
    ("hourly", "precipitation_probability", 3),
    ("hourly", "temperature_2m", 5),
    ("daily", "temperature_2m_min", 1),
    ("daily", "weather_code", 2),
]


def record(scenario, label, lat, lon, tz, units="imperial"):
    f_url = wx_data.forecast_url(lat, lon, units, tz)
    a_url = wx_data.alerts_url(lat, lon)
    forecast = wx_data.fetch_json(f_url)
    alerts = wx_data.fetch_json(a_url, {"Accept": "application/geo+json"})
    recorded_at = datetime.now(timezone.utc).astimezone(ZoneInfo(tz)).replace(microsecond=0)
    return {
        "scenario": scenario,
        "recorded_at": recorded_at.isoformat(),
        "location": {"label": label, "latitude": lat, "longitude": lon},
        "timezone": tz,
        "units": units,
        "forecast_url": f_url,
        "forecast": forecast,
        "alerts_url": a_url,
        "alerts": alerts,
    }


def make_missing(sunny):
    data = copy.deepcopy(sunny)
    data["scenario"] = "missing"
    data["derived_from"] = "sunny"
    data["edits"] = [list(edit) for edit in MISSING_EDITS]
    forecast = data["forecast"]
    for edit in MISSING_EDITS:
        block = forecast[edit[0]]
        if len(edit) == 2:
            block.pop(edit[1], None)
        else:
            block[edit[1]][edit[2]] = None
    return data


def main():
    os.makedirs(FIXTURES, exist_ok=True)
    recorded = {}
    for scenario, (label, lat, lon, tz) in LOCATIONS.items():
        recorded[scenario] = record(scenario, label, lat, lon, tz)
        current = recorded[scenario]["forecast"]["current"]
        hourly_codes = recorded[scenario]["forecast"]["hourly"]["weather_code"][:24]
        events = [f["properties"]["event"] for f in recorded[scenario]["alerts"].get("features", [])]
        print(f"{scenario:12s} {label:10s} now code={current['weather_code']} "
              f"max 24h code={max(hourly_codes)} alerts={events}")
    recorded["missing"] = make_missing(recorded["sunny"])
    for scenario, data in recorded.items():
        with open(os.path.join(FIXTURES, f"{scenario}.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
    print(f"Wrote {len(recorded)} fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
