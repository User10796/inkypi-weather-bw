import pytest
from PIL import Image, ImageDraw, ImageFont

from weather_bw import wx_icons
from weather_bw.wx_render import FONTS_DIR, ICON_FONT

# Every code Open-Meteo documents for weather_code (WMO 4677 subset).
ALL_WMO_CODES = [0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77,
                 80, 81, 82, 85, 86, 95, 96, 99]

FONT = ImageFont.truetype(f"{FONTS_DIR}/{ICON_FONT}", 64)


def ink(char):
    image = Image.new("L", (128, 128), 255)
    ImageDraw.Draw(image).text((16, 16), char, font=FONT, fill=0)
    return image.point(lambda p: 255 - p).getbbox()


def test_table_covers_every_wmo_code_exactly():
    assert sorted(wx_icons.WMO) == ALL_WMO_CODES


@pytest.mark.parametrize("code", ALL_WMO_CODES)
@pytest.mark.parametrize("is_day", [True, False])
def test_every_code_has_a_glyph_that_draws(code, is_day):
    name = wx_icons.icon_name(code, is_day)
    assert name in wx_icons.CODEPOINTS, name
    assert name != "na"
    assert ink(wx_icons.icon_for(code, is_day)) is not None
    assert wx_icons.describe(code) not in ("", "—")


@pytest.mark.parametrize("name", sorted(wx_icons.CODEPOINTS))
def test_every_codepoint_is_in_the_font(name):
    # Missing glyphs in this font draw nothing at all.
    assert ink(wx_icons.glyph(name)) is not None, name


def test_day_and_night_differ_where_it_matters():
    assert wx_icons.icon_name(0, True) == "day-sunny"
    assert wx_icons.icon_name(0, False) == "night-clear"
    assert wx_icons.icon_name(95, False) == "night-alt-thunderstorm"


def test_unknown_code_and_missing_is_day():
    assert wx_icons.icon_name(42, True) == "na"
    assert wx_icons.icon_name(None, None) == "na"
    assert wx_icons.describe(None) == "—"
    assert wx_icons.icon_name(2, None) == "day-cloudy"  # unknown day/night → day icon
