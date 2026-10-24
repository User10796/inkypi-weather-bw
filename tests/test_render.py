import math
from datetime import date, datetime, timedelta, timezone

import pytest
from PIL import Image, ImageOps

from snapshot_cases import CASES, load_fixture, render_case
from weather_bw import wx_data, wx_render as r
from weather_bw.wx_data import Alert, Current, Day, Forecast, Hour


def strictly_black_and_white(image):
    histogram = image.convert("L").histogram()
    # Also check every RGB channel: no tinted pixels sneak in.
    channels_ok = all(sum(image.getchannel(c).histogram()[1:255]) == 0 for c in "RGB")
    return sum(histogram[1:255]) == 0 and channels_ok


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.filename)
def test_every_layout_renders_strictly_black_and_white(case):
    image = render_case(case)
    expected = (800, 480) if case.orientation == "landscape" else (480, 800)
    assert image.size == expected and image.mode == "RGB"
    assert strictly_black_and_white(image)
    # And it isn't blank.
    assert ImageOps.invert(image.convert("L")).getbbox() is not None


@pytest.mark.parametrize("extra", [
    {"units": "metric"}, {"font_style": "serif"}, {"hours": "12"}, {"days": "5"},
    {"hours": "12", "days": "5", "layout": "week"}, {"layout": "hourly", "hours": "12"},
])
def test_option_combinations_are_black_and_white(extra):
    for case in CASES[:6]:
        assert strictly_black_and_white(render_case(case, **extra))


def test_error_screen_is_black_and_white():
    for size in ((800, 480), (480, 800)):
        image = r.render_error(size, "Weather unavailable", "OSError: network is unreachable", label="Tifton")
        assert strictly_black_and_white(image)


def test_hatched_bars_are_hatched_not_filled():
    image = Image.new("L", (100, 100), 255)
    pen = r.Pen(image, r.Fonts("sans"))
    pen.hatched_rect((10, 10, 70, 90))
    inside = image.crop((14, 14, 66, 86))
    black = inside.histogram()[0] / (inside.width * inside.height)
    assert 0.15 < black < 0.6  # stripes, not solid and not empty
    assert image.getpixel((10, 50)) == 0 and image.getpixel((69, 50)) == 0  # outline
    assert image.getpixel((5, 5)) == 255  # nothing outside
    # Stripes run diagonally: shifting one pixel right and one up lines them up.
    same = sum(inside.getpixel((x, y)) == inside.getpixel((x + 1, y - 1))
               for x in range(0, 50) for y in range(1, 70))
    assert same / (50 * 69) > 0.95


def test_rain_chart_has_hatched_bars():
    # Precipitation bars in the rain fixture's chart area contain stripes.
    image = render_case(next(c for c in CASES if c.filename == "rain-hourly-landscape.png")).convert("L")
    bars = image.crop((560, 380, 790, 440))
    black = bars.histogram()[0] / (bars.width * bars.height)
    assert 0.1 < black < 0.7


def test_arrow_points_downwind():
    for degrees, (dx, dy) in [(0, (0, -1)), (90, (1, 0)), (180, (0, 1)), (225, (-1, 1))]:
        image = Image.new("L", (60, 60), 255)
        r.Pen(image, r.Fonts("sans")).arrow((30, 30), 40, degrees)
        pts = [(x, y) for x in range(60) for y in range(60) if image.getpixel((x, y)) == 0]
        # The tip is the inked point farthest along the requested direction.
        tip = max(pts, key=lambda p: (p[0] - 30) * dx + (p[1] - 30) * dy)
        angle = math.degrees(math.atan2(tip[0] - 30, -(tip[1] - 30))) % 360
        assert abs((angle - degrees + 180) % 360 - 180) < 12


def test_text_is_never_below_minimum_size():
    fonts = r.Fonts("sans")
    assert fonts.text(8).size == r.MIN_TEXT
    assert r.Fonts.icon(4).size == r.MIN_TEXT
    assert r.fit_font(fonts, "x" * 200, 50, 40).size == r.MIN_TEXT


def test_key_numbers_are_at_least_40px():
    """Current temp, high/low and rain chance are drawn at ≥ KEY_TEXT."""
    assert r.KEY_TEXT >= 40
    fonts = r.Fonts("sans")
    # Even a very long high/low string never shrinks below KEY_TEXT.
    assert r.fit_font(fonts, "-100° / -100°", 100, r.KEY_TEXT + 4, floor=r.KEY_TEXT, bold=True).size >= 40


@pytest.mark.parametrize("value, units, expected", [
    (datetime(2026, 10, 24, 6, 0), "imperial", "6 AM"),
    (datetime(2026, 10, 24, 18, 52), "imperial", "6:52 PM"),
    (datetime(2026, 10, 24, 0, 5), "imperial", "12:05 AM"),
    (datetime(2026, 10, 24, 18, 52), "metric", "18:52"),
    (None, "imperial", "—"),
])
def test_fmt_time(value, units, expected):
    assert r.fmt_time(value, units) == expected


@pytest.mark.parametrize("deg, name", [(0, "N"), (22, "N"), (23, "NE"), (180, "S"), (225, "SW"), (350, "N"), (None, "")])
def test_cardinal(deg, name):
    assert r.cardinal(deg) == name


@pytest.mark.parametrize("uv, text", [(0.4, "0 Low"), (5.5, "6 High"), (8.2, "8 Very high"), (11, "11 Extreme"), (None, "—")])
def test_uv_category(uv, text):
    assert r.uv_category(uv) == text


def test_regions_cover_the_spec_grid():
    regs = r.regions("dashboard", (800, 480))
    assert regs == {"header": (0, 0, 800, 36), "now": (0, 36, 320, 210), "today": (320, 36, 800, 210),
                    "chart": (0, 210, 800, 350), "days": (0, 350, 800, 480)}
    portrait = r.regions("dashboard", (480, 800))
    tops = [portrait[k][1] for k in ("header", "now", "today", "chart", "days")]
    assert tops == sorted(tops) and portrait["days"][3] == 800  # stacked vertically


def test_empty_forecast_still_renders():
    page = r.Page((800, 480), "dashboard", "sans", "imperial", "", "Sat Oct 24", None, None,
                  datetime(2026, 10, 24, 9, 0))
    for layout in r.LAYOUTS:
        page.layout = layout
        assert strictly_black_and_white(r.render(page, Forecast(Current(), [], [])))


def test_long_alert_and_stale_stamp_fit_the_header():
    alert = Alert("Extreme Heat Warning For The Entire Coastal Region", "Extreme", "",
                  datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc))
    fixture = load_fixture("sunny")
    forecast = wx_data.parse_forecast(fixture["forecast"], datetime(2026, 10, 24, 9, 44))
    for size in ((800, 480), (480, 800)):
        page = r.Page(size, "dashboard", "sans", "imperial", "Tifton", "Sat Oct 24",
                      "Updated 7:44 AM (2 h ago)", alert, datetime(2026, 10, 24, 9, 44))
        image = r.render(page, forecast)
        header = image.convert("L").crop((0, 0, size[0], 36))
        assert header.histogram()[0] / (size[0] * 36) > 0.5  # still a black banner
        assert strictly_black_and_white(image)
