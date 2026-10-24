"""WMO weather codes → condition text and Weather Icons glyphs.

The one table to edit is ``WMO``. Codepoints come from Weather Icons' own
``css/weather-icons.css`` (v2.0.10); ``scripts/update_icon_codepoints.py``
regenerates ``CODEPOINTS`` from that file.
"""

# code: (description, day icon, night icon)
WMO = {
    0: ("Clear", "day-sunny", "night-clear"),
    1: ("Mostly clear", "day-sunny-overcast", "night-alt-partly-cloudy"),
    2: ("Partly cloudy", "day-cloudy", "night-alt-cloudy"),
    3: ("Overcast", "cloudy", "cloudy"),
    45: ("Fog", "day-fog", "night-fog"),
    48: ("Freezing fog", "fog", "fog"),
    51: ("Light drizzle", "day-sprinkle", "night-alt-sprinkle"),
    53: ("Drizzle", "sprinkle", "sprinkle"),
    55: ("Heavy drizzle", "showers", "showers"),
    56: ("Freezing drizzle", "day-sleet", "night-alt-sleet"),
    57: ("Heavy freezing drizzle", "sleet", "sleet"),
    61: ("Light rain", "day-rain", "night-alt-rain"),
    63: ("Rain", "rain", "rain"),
    65: ("Heavy rain", "rain-wind", "rain-wind"),
    66: ("Freezing rain", "day-rain-mix", "night-alt-rain-mix"),
    67: ("Heavy freezing rain", "rain-mix", "rain-mix"),
    71: ("Light snow", "day-snow", "night-alt-snow"),
    73: ("Snow", "snow", "snow"),
    75: ("Heavy snow", "snow", "snow"),
    77: ("Snow grains", "snowflake-cold", "snowflake-cold"),
    80: ("Light showers", "day-showers", "night-alt-showers"),
    81: ("Showers", "showers", "showers"),
    82: ("Violent showers", "rain-wind", "rain-wind"),
    85: ("Snow showers", "day-snow", "night-alt-snow"),
    86: ("Heavy snow showers", "snow", "snow"),
    95: ("Thunderstorm", "day-thunderstorm", "night-alt-thunderstorm"),
    96: ("Thunderstorm, hail", "day-sleet-storm", "night-alt-sleet-storm"),
    99: ("Severe thunderstorm, hail", "hail", "hail"),
}
UNKNOWN = ("—", "na", "na")

# Other glyphs used by the layouts.
EXTRA_ICONS = ("na", "umbrella", "sunrise", "sunset", "strong-wind", "humidity")

CODEPOINTS = {
    "cloudy": "",
    "day-cloudy": "",
    "day-fog": "",
    "day-rain": "",
    "day-rain-mix": "",
    "day-showers": "",
    "day-sleet": "",
    "day-sleet-storm": "",
    "day-snow": "",
    "day-sprinkle": "",
    "day-sunny": "",
    "day-sunny-overcast": "",
    "day-thunderstorm": "",
    "fog": "",
    "hail": "",
    "humidity": "",
    "na": "",
    "night-alt-cloudy": "",
    "night-alt-partly-cloudy": "",
    "night-alt-rain": "",
    "night-alt-rain-mix": "",
    "night-alt-showers": "",
    "night-alt-sleet": "",
    "night-alt-sleet-storm": "",
    "night-alt-snow": "",
    "night-alt-sprinkle": "",
    "night-alt-thunderstorm": "",
    "night-clear": "",
    "night-fog": "",
    "rain": "",
    "rain-mix": "",
    "rain-wind": "",
    "showers": "",
    "sleet": "",
    "snow": "",
    "snowflake-cold": "",
    "sprinkle": "",
    "strong-wind": "",
    "sunrise": "",
    "sunset": "",
    "umbrella": "",
}


def describe(code) -> str:
    return WMO.get(code, UNKNOWN)[0]


def icon_name(code, is_day=True) -> str:
    _, day, night = WMO.get(code, UNKNOWN)
    return day if is_day or is_day is None else night


def glyph(name: str) -> str:
    return CODEPOINTS.get(name, CODEPOINTS["na"])


def icon_for(code, is_day=True) -> str:
    return glyph(icon_name(code, is_day))
