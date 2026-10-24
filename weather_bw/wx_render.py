"""Pillow drawing for weather_bw: pure black and white, no gray anywhere.

Everything is drawn on a mode "L" canvas using only 0 (black) and 255
(white); anti-aliased text edges are thresholded at 128 at the end. Shading
is done with hatching, outlines or solid black, never gray.

Regions follow the spec's 800×480 dashboard; portrait (480×800) stacks the
same regions vertically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import wx_icons
from .wx_data import Alert, Day, Forecast, Hour

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
ICON_FONT = "weathericons-regular-webfont.ttf"
FONT_STYLES = {
    # style: (regular file, regular weight, bold file, bold weight)
    "sans": ("AtkinsonHyperlegible-Regular.ttf", None, "AtkinsonHyperlegible-Bold.ttf", None),
    "serif": ("Lora[wght].ttf", 450, "Lora[wght].ttf", 700),
}
DEFAULT_FONT_STYLE = "sans"
LAYOUTS = ("dashboard", "hourly", "week")

MIN_TEXT = 18  # px; nothing smaller is ever drawn
KEY_TEXT = 40  # px; key numbers (temperatures, rain chance) are at least this
BLACK, WHITE = 0, 255
THRESHOLD = 128
HATCH_SPACING = 7
HEADER_H = 36
MIN_SPAN_F, MIN_SPAN_C = 12, 7  # a 3° wiggle shouldn't fill the chart

Rect = tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive)


# --------------------------------------------------------------------------
# Fonts and text helpers
# --------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _load(filename: str, size: int, weight: int | None) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(os.path.join(FONTS_DIR, filename), size)
    if weight is not None:
        try:
            font.set_variation_by_axes([weight])
        except (OSError, AttributeError):
            pass
    return font


class Fonts:
    def __init__(self, style: str):
        self.regular_file, self.regular_weight, self.bold_file, self.bold_weight = FONT_STYLES.get(
            style, FONT_STYLES[DEFAULT_FONT_STYLE]
        )

    def text(self, size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
        size = max(MIN_TEXT, int(round(size)))
        if bold:
            return _load(self.bold_file, size, self.bold_weight)
        return _load(self.regular_file, size, self.regular_weight)

    @staticmethod
    def icon(size: float) -> ImageFont.FreeTypeFont:
        return _load(ICON_FONT, max(MIN_TEXT, int(round(size))), None)


def text_width(font, text: str) -> float:
    return font.getlength(text)


def fit_font(fonts: Fonts, text: str, max_width: float, start: float, floor: float = MIN_TEXT,
             bold: bool = False):
    """Largest size ≤ start (and ≥ floor) at which text fits max_width."""
    size = int(start)
    while size > floor and fonts.text(size, bold).getlength(text) > max_width:
        size -= 1
    return fonts.text(size, bold)


def ellipsize(font, text: str, max_width: float) -> str:
    if font.getlength(text) <= max_width:
        return text
    while text and font.getlength(text + "…") > max_width:
        text = text[:-1]
    return text.rstrip() + "…"


class Pen:
    """Drawing helpers on one canvas."""

    def __init__(self, image: Image.Image, fonts: Fonts):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.fonts = fonts

    def text(self, xy, text, font, fill=BLACK, anchor="la"):
        self.draw.text(xy, text, font=font, fill=fill, anchor=anchor)

    def glyph(self, center, glyph: str, size: float, fill=BLACK, max_width: float | None = None):
        """Draw an icon glyph centered on its ink, not its em box. The ink is
        shrunk to fit ``max_width`` (default 1.1 × size) and ``size`` tall;
        Weather Icons' cloud glyphs are much wider than they are tall."""
        font = Fonts.icon(size)
        left, top, right, bottom = self.draw.textbbox((0, 0), glyph, font=font)
        max_width = max_width or size * 1.1
        scale = min(1.0, max_width / max(1, right - left), size / max(1, bottom - top))
        if scale < 1.0:
            font = Fonts.icon(size * scale)
            left, top, right, bottom = self.draw.textbbox((0, 0), glyph, font=font)
        x = center[0] - (left + right) / 2
        y = center[1] - (top + bottom) / 2
        self.draw.text((x, y), glyph, font=font, fill=fill)

    def hline(self, x0, x1, y, width=1, fill=BLACK):
        self.draw.line([(x0, y), (x1, y)], fill=fill, width=width)

    def dotted_vline(self, x, y0, y1, dash=3, gap=4, width=2):
        y = y0
        while y < y1:
            self.draw.line([(x, y), (x, min(y + dash, y1))], fill=BLACK, width=width)
            y += dash + gap

    def hatched_rect(self, rect: Rect, outline=2):
        """Outlined box with 45° diagonal hatching inside."""
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return
        layer = Image.new("L", (w, h), WHITE)
        ld = ImageDraw.Draw(layer)
        for k in range(-h, w, HATCH_SPACING):
            ld.line([(k, h), (k + h, 0)], fill=BLACK, width=2)
        region = self.image.crop(rect)
        self.image.paste(ImageChops.darker(region, layer), (x0, y0))
        self.draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=BLACK, width=outline)

    def arrow(self, center, size, degrees_to):
        """A filled arrow pointing toward ``degrees_to`` (0 = up/north, clockwise)."""
        import math

        cx, cy = center
        r = size / 2
        pts = [(0, -r), (r * 0.62, r * 0.75), (0, r * 0.35), (-r * 0.62, r * 0.75)]
        a = math.radians(degrees_to)
        rot = [
            (cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a))
            for x, y in pts
        ]
        self.draw.polygon(rot, fill=BLACK)


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def fmt_temp(value) -> str:
    return "—" if value is None else f"{round(value)}°"


def fmt_pct(value) -> str:
    return "—" if value is None else f"{round(value)}%"


def fmt_time(value: datetime | None, units: str) -> str:
    if value is None:
        return "—"
    if units == "metric":
        return value.strftime("%H:%M")
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour} {suffix}" if value.minute == 0 else f"{hour}:{value.minute:02d} {suffix}"


def fmt_hour(value: datetime, units: str) -> str:
    if units == "metric":
        return value.strftime("%H")
    hour = value.hour % 12 or 12
    return f"{hour}{'a' if value.hour < 12 else 'p'}"


def cardinal(degrees) -> str:
    if degrees is None:
        return ""
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[int((degrees % 360) / 45 + 0.5) % 8]


def uv_category(uv) -> str:
    if uv is None:
        return "—"
    uv = round(uv)
    level = "Low" if uv <= 2 else "Moderate" if uv <= 5 else "High" if uv <= 7 else "Very high" if uv <= 10 else "Extreme"
    return f"{uv} {level}"


def speed_unit(units: str) -> str:
    return "km/h" if units == "metric" else "mph"


# --------------------------------------------------------------------------
# Regions
# --------------------------------------------------------------------------

def draw_header(pen: Pen, rect: Rect, label: str, date_text: str, alert: Alert | None,
                stale_text: str | None, units: str, now_local: datetime):
    x0, y0, x1, y1 = rect
    fonts = pen.fonts
    pad = 10
    mid = (y0 + y1) / 2
    font = fonts.text(22, bold=True)
    if alert is not None:
        pen.draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=BLACK)
        fill = WHITE
        until = ""
        if alert.ends is not None:
            until = " until " + fmt_time(alert.ends.replace(tzinfo=None), units)
            if alert.ends.date() != now_local.date():
                until += alert.ends.strftime(" %a")
        left = alert.event.upper() + until
    else:
        fill = BLACK
        left = label or ""
        pen.hline(x0, x1, y1 - 2, width=2)

    right = stale_text or date_text
    right_font = fonts.text(MIN_TEXT, bold=bool(stale_text))
    if alert is not None and not stale_text:
        # The alert matters more than the date: drop the date if it would
        # force the alert text below 20 px or into an ellipsis.
        room = (x1 - x0) - 3 * pad - text_width(right_font, right)
        if fonts.text(20, bold=True).getlength(left) > room:
            right = ""
    right_w = text_width(right_font, right) if right else 0
    avail = (x1 - x0) - (3 if right else 2) * pad - right_w
    left_font = fit_font(fonts, left, avail, 22, bold=True) if left else font
    left = ellipsize(left_font, left, avail)
    if left:
        pen.text((x0 + pad, mid), left, left_font, fill=fill, anchor="lm")
    if right:
        pen.text((x1 - pad, mid), right, right_font, fill=fill, anchor="rm")


def draw_now(pen: Pen, rect: Rect, forecast: Forecast, units: str):
    x0, y0, x1, y1 = rect
    fonts = pen.fonts
    cur = forecast.current
    w, h = x1 - x0, y1 - y0
    icon_size = min(110, h * 0.62)
    pad = 12
    top_h = icon_size + 6
    pen.glyph((x0 + pad + icon_size / 2, y0 + 8 + top_h / 2), wx_icons.icon_for(cur.code, cur.is_day),
              icon_size, max_width=icon_size)

    temp = fmt_temp(cur.temperature)
    temp_left = x0 + pad + icon_size + 14
    temp_font = fit_font(fonts, temp, x1 - pad - temp_left, min(100, top_h), floor=KEY_TEXT, bold=True)
    pen.text((temp_left, y0 + 8 + top_h / 2), temp, temp_font, anchor="lm")

    line_font = fonts.text(22)
    desc = wx_icons.describe(cur.code)
    feels = f"Feels {fmt_temp(cur.feels_like)}"
    line_y = y0 + 8 + top_h + 6
    one_line = f"{desc}  ·  {feels}"
    if text_width(line_font, one_line) <= w - 2 * pad and h - (line_y - y0) < 56:
        pen.text((x0 + pad, line_y), one_line, line_font)
    else:
        pen.text((x0 + pad, line_y), ellipsize(line_font, desc, w - 2 * pad), line_font)
        pen.text((x0 + pad, line_y + 28), feels, line_font)


def draw_today(pen: Pen, rect: Rect, forecast: Forecast, units: str):
    """Big High/Low and Rain on top (readable across the room), details below."""
    x0, y0, x1, y1 = rect
    fonts = pen.fonts
    today = forecast.days[0] if forecast.days else Day(date=date.today())
    cur = forecast.current
    pad = 12
    col_w = (x1 - x0) / 2
    label_font = fonts.text(MIN_TEXT)
    big = fonts.text(KEY_TEXT + 4, bold=True)

    # Top row
    ty = y0 + 6
    pen.text((x0 + pad, ty), "High / Low", label_font)
    pen.text((x0 + col_w + pad, ty), "Rain", label_font)
    hilo = f"{fmt_temp(today.high)} / {fmt_temp(today.low)}"
    hilo_font = fit_font(fonts, hilo, col_w - 2 * pad, KEY_TEXT + 4, floor=KEY_TEXT, bold=True)
    pen.text((x0 + pad, ty + 22), hilo, hilo_font)
    pen.text((x0 + col_w + pad, ty + 22), fmt_pct(today.precip_probability), big)

    # Detail rows
    rows = [
        (("Wind", None), ("Humidity", fmt_pct(cur.humidity))),
        (("Sunrise", fmt_time(today.sunrise, units)), ("Sunset", fmt_time(today.sunset, units))),
        (("UV", uv_category(today.uv_index)), None),
    ]
    top = ty + 22 + KEY_TEXT + 14
    row_h = max(24, (y1 - top - 4) / len(rows))
    value_font = fonts.text(MIN_TEXT + 2, bold=True)
    for r, row in enumerate(rows):
        cy = top + r * row_h + row_h / 2
        for c, cell in enumerate(row):
            if cell is None:
                continue
            label, value = cell
            cx0 = x0 + c * col_w + pad
            cx1 = x0 + (c + 1) * col_w - pad
            pen.text((cx0, cy), label, label_font, anchor="lm")
            if label == "Wind":
                speed = "—" if cur.wind_speed is None else f"{round(cur.wind_speed)} {speed_unit(units)}"
                value = f"{cardinal(cur.wind_direction)} {speed}".strip()
                pen.text((cx1, cy), value, value_font, anchor="rm")
                if cur.wind_direction is not None:
                    arrow_x = cx1 - text_width(value_font, value) - 14
                    pen.arrow((arrow_x, cy), 18, (cur.wind_direction + 180) % 360)
            else:
                pen.text((cx1, cy), value, value_font, anchor="rm")


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _chart_points(hours: list[Hour], x0: float, x1: float):
    n = max(1, len(hours))
    step = (x1 - x0) / n
    return step, [x0 + step * (i + 0.5) for i in range(len(hours))]


def draw_chart(pen: Pen, rect: Rect, hours: list[Hour], now_local: datetime, units: str,
               icon_row: bool = False, title: str | None = None):
    """Temperature line over hatched precipitation bars, hour labels below."""
    x0, y0, x1, y1 = rect
    fonts = pen.fonts
    label_font = fonts.text(MIN_TEXT)
    pad = 12
    if not hours:
        pen.text(((x0 + x1) / 2, (y0 + y1) / 2), "No hourly forecast", fonts.text(22), anchor="mm")
        return

    cx0, cx1 = x0 + pad, x1 - pad
    step, xs = _chart_points(hours, cx0, cx1)
    every = 3 if len(hours) > 12 else 2 if step < 50 else 1

    top = y0 + 4
    if title:
        pen.text((cx0, top), title, label_font)
        top += 24
    if icon_row:
        stride = 1 if step >= 40 else 2 if step >= 22 else 3
        icon_size = min(44, step * stride * 0.85)
        for i, hour in enumerate(hours):
            if i % stride == 0:
                pen.glyph((xs[i], top + icon_size / 2 + 2), wx_icons.icon_for(hour.code, hour.is_day),
                          icon_size, max_width=step * stride * 0.9)
        top += icon_size + 10

    hour_label_h = 24
    bottom = y1 - hour_label_h - 2
    total = bottom - top
    bars_h = max(24, total * 0.32)
    temp_top = top + 24  # room for value labels above the line
    temp_bottom = bottom - bars_h - 10
    bars_top = bottom - bars_h

    # Precipitation bars: outlined + hatched, height = probability.
    bar_w = max(4, step * 0.62)
    pen.hline(cx0, cx1, bottom, width=1)
    for x, hour in zip(xs, hours):
        p = hour.precip_probability
        if p is None or p <= 0:
            continue
        bh = max(3, round(bars_h * min(p, 100) / 100))
        rect_bar = (round(x - bar_w / 2), round(bottom - bh), round(x + bar_w / 2), round(bottom) + 1)
        if bh >= 6:
            pen.hatched_rect(rect_bar)
        else:
            pen.draw.rectangle([rect_bar[0], rect_bar[1], rect_bar[2] - 1, rect_bar[3] - 1], fill=BLACK)
    peak = max(hours, key=lambda h: h.precip_probability or 0)
    if (peak.precip_probability or 0) > 0:
        i = hours.index(peak)
        bh = bars_h * min(peak.precip_probability, 100) / 100
        label = fmt_pct(peak.precip_probability)
        ly = bottom - bh - 4
        lx = min(max(xs[i], cx0 + text_width(label_font, label) / 2), cx1 - text_width(label_font, label) / 2)
        if ly - MIN_TEXT >= temp_bottom - 4:
            pen.text((lx, ly), label, label_font, anchor="ms")

    # Temperature line.
    temps = [h.temperature for h in hours if h.temperature is not None]
    if temps:
        lo, hi = min(temps), max(temps)
        span = max(hi - lo, MIN_SPAN_C if units == "metric" else MIN_SPAN_F)
        mid = (hi + lo) / 2
        lo, hi = mid - span / 2, mid + span / 2

        def y_of(t):
            return temp_bottom - (t - lo) / (hi - lo) * (temp_bottom - temp_top)

        segment = []
        for x, hour in zip(xs, hours):
            if hour.temperature is None:
                if len(segment) > 1:
                    pen.draw.line(segment, fill=BLACK, width=3, joint="curve")
                segment = []
                continue
            segment.append((x, y_of(hour.temperature)))
        if len(segment) > 1:
            pen.draw.line(segment, fill=BLACK, width=3, joint="curve")
    # Dotted markers at midnight (with the new day's name) and at "now".
    taken = []
    for i, (x, hour) in enumerate(zip(xs, hours)):
        if i % every == 0:
            pen.text((x, y1 - 2), fmt_hour(hour.time, units), label_font, anchor="md")
        if hour.time.hour == 0 and i > 0:
            mx = x - step / 2
            pen.dotted_vline(mx, top, bottom)
            name = hour.time.strftime("%a")
            pen.text((mx + 4, top), name, label_font)
            taken.append(pen.draw.textbbox((mx + 4, top), name, font=label_font))

    if temps:
        for i, (x, hour) in enumerate(zip(xs, hours)):
            if hour.temperature is None or i % every:
                continue
            y = y_of(hour.temperature)
            pen.draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=BLACK)
            label = fmt_temp(hour.temperature)
            box = pen.draw.textbbox((x, y - 8), label, font=label_font, anchor="ms")
            if not any(_overlaps(box, t) for t in taken):
                pen.text((x, y - 8), label, label_font, anchor="ms")

    first = hours[0].time
    frac = (now_local - first).total_seconds() / 3600
    if 0 <= frac <= 1:
        nx = xs[0] - step / 2 + frac * step
        pen.dotted_vline(nx, top, bottom)


def draw_days(pen: Pen, rect: Rect, days: list[Day], units: str, today: date):
    """Equal columns: day name, 48 px icon, high bold / low regular, rain %."""
    x0, y0, x1, y1 = rect
    fonts = pen.fonts
    pen.hline(x0, x1, y0, width=2)
    if not days:
        return
    col = (x1 - x0) / len(days)
    h = y1 - y0
    name_font = fonts.text(20, bold=True)
    hi_font = fonts.text(24, bold=True)
    lo_font = fonts.text(22)
    pct_font = fonts.text(MIN_TEXT)
    stacked = col < 90  # narrow columns (portrait): high over low
    icon_size = min(48, h * 0.37)
    for i, day in enumerate(days):
        cx = x0 + col * (i + 0.5)
        y = y0 + 6
        name = "Today" if day.date == today else day.date.strftime("%a")
        pen.text((cx, y), name, name_font if col > 70 or len(name) <= 3 else fonts.text(MIN_TEXT, bold=True), anchor="ma")
        y += 24
        pen.glyph((cx, y + icon_size / 2), wx_icons.icon_for(day.code, True), icon_size, max_width=col * 0.85)
        y += icon_size + 4
        if stacked:
            pen.text((cx, y), fmt_temp(day.high), hi_font, anchor="ma")
            y += 26
            pen.text((cx, y), fmt_temp(day.low), lo_font, anchor="ma")
            y += 24
        else:
            hi, lo = fmt_temp(day.high), " / " + fmt_temp(day.low)
            total = text_width(hi_font, hi) + text_width(lo_font, lo)
            left = cx - total / 2
            pen.text((left, y), hi, hi_font, anchor="la")
            pen.text((left + text_width(hi_font, hi), y + 2), lo, lo_font, anchor="la")
            y += 28
        if y + MIN_TEXT <= y1:
            pen.text((cx, y), fmt_pct(day.precip_probability), pct_font, anchor="ma")
        if i:
            pen.draw.line([(x0 + col * i, y0 + 8), (x0 + col * i, y1 - 8)], fill=BLACK, width=1)


def draw_day_cards(pen: Pen, rect: Rect, days: list[Day], units: str, today: date, vertical: bool):
    """Large cards for the week layout; today's card is inverted (solid black)."""
    x0, y0, x1, y1 = rect
    fonts = pen.fonts
    if not days:
        pen.text(((x0 + x1) / 2, (y0 + y1) / 2), "No daily forecast", fonts.text(22), anchor="mm")
        return
    n = len(days)
    gap = 8
    for i, day in enumerate(days):
        if vertical:
            ch = (y1 - y0 - gap * (n + 1)) / n
            card = (x0 + gap, y0 + gap + i * (ch + gap), x1 - gap, y0 + gap + i * (ch + gap) + ch)
        else:
            cw = (x1 - x0 - gap * (n + 1)) / n
            card = (x0 + gap + i * (cw + gap), y0 + gap, x0 + gap + i * (cw + gap) + cw, y1 - gap)
        cx0, cy0, cx1, cy1 = (round(v) for v in card)
        inverted = day.date == today
        fill = WHITE if inverted else BLACK
        if inverted:
            pen.draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=10, fill=BLACK)
        else:
            pen.draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=10, outline=BLACK, width=2)
        name = "Today" if inverted else day.date.strftime("%a")
        date_text = day.date.strftime("%b ") + str(day.date.day)
        icon = wx_icons.icon_for(day.code, True)
        hi, lo = fmt_temp(day.high), fmt_temp(day.low)
        pct = fmt_pct(day.precip_probability)
        w, h = cx1 - cx0, cy1 - cy0
        if vertical:
            mid = (cy0 + cy1) / 2
            pen.text((cx0 + 14, mid - 2), name, fonts.text(26, bold=True), fill=fill, anchor="ls")
            pen.text((cx0 + 14, mid + 4), date_text, fonts.text(MIN_TEXT), fill=fill, anchor="la")
            icon_size = min(h * 0.8, 70)
            pen.glyph((cx0 + w * 0.40, mid), icon, icon_size, fill=fill, max_width=w * 0.2)
            hi_font = fonts.text(KEY_TEXT, bold=True)
            pen.text((cx0 + w * 0.56, mid), hi, hi_font, fill=fill, anchor="lm")
            lo_x = cx0 + w * 0.56 + text_width(hi_font, hi) + 8
            pen.text((lo_x, mid + 4), lo, fonts.text(26), fill=fill, anchor="lm")
            pen.text((cx1 - 14, mid), pct, fonts.text(22, bold=True), fill=fill, anchor="rm")
        else:
            cxm = (cx0 + cx1) / 2
            y = cy0 + 10
            pen.text((cxm, y), name, fit_font(fonts, name, w - 8, 26, bold=True), fill=fill, anchor="ma")
            y += 30
            pen.text((cxm, y), date_text, fonts.text(MIN_TEXT), fill=fill, anchor="ma")
            y += 26
            icon_size = min(w * 0.75, h * 0.3, 90)
            pen.glyph((cxm, y + icon_size / 2), icon, icon_size, fill=fill, max_width=w - 12)
            y += icon_size + 12
            pen.text((cxm, y), hi, fit_font(fonts, hi, w - 8, KEY_TEXT, floor=KEY_TEXT - 6, bold=True), fill=fill, anchor="ma")
            y += KEY_TEXT + 6
            pen.text((cxm, y), lo, fonts.text(26), fill=fill, anchor="ma")
            y += 34
            if y + 24 <= cy1:
                total = 26 + text_width(fonts.text(22, bold=True), pct)
                left = cxm - total / 2
                pen.glyph((left + 11, y + 12), wx_icons.glyph("umbrella"), 22, fill=fill)
                pen.text((left + 26, y), pct, fonts.text(22, bold=True), fill=fill, anchor="la")


# --------------------------------------------------------------------------
# Page layouts
# --------------------------------------------------------------------------

@dataclass
class Page:
    size: tuple[int, int]
    layout: str
    font_style: str
    units: str
    label: str
    date_text: str
    stale_text: str | None
    alert: Alert | None
    now_local: datetime


def _scale_rect(rect, sx, sy) -> Rect:
    x0, y0, x1, y1 = rect
    return (round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy))


def regions(layout: str, size: tuple[int, int]) -> dict[str, Rect]:
    """Region rectangles for a layout. Landscape follows the spec's 800×480
    grid; portrait stacks the same regions in 480×800. Other panel sizes are
    scaled from the nearest of the two."""
    width, height = size
    portrait = height > width
    if portrait:
        base_w, base_h = 480, 800
        grids = {
            "dashboard": {"header": (0, 0, 480, 36), "now": (0, 36, 480, 206), "today": (0, 206, 480, 380),
                          "chart": (0, 380, 480, 600), "days": (0, 600, 480, 800)},
            "hourly": {"header": (0, 0, 480, 36), "chart": (0, 36, 480, 800)},
            "week": {"header": (0, 0, 480, 36), "cards": (0, 36, 480, 800)},
        }
    else:
        base_w, base_h = 800, 480
        grids = {
            "dashboard": {"header": (0, 0, 800, 36), "now": (0, 36, 320, 210), "today": (320, 36, 800, 210),
                          "chart": (0, 210, 800, 350), "days": (0, 350, 800, 480)},
            "hourly": {"header": (0, 0, 800, 36), "chart": (0, 36, 800, 480)},
            "week": {"header": (0, 0, 800, 36), "cards": (0, 36, 800, 480)},
        }
    sx, sy = width / base_w, height / base_h
    return {name: _scale_rect(r, sx, sy) for name, r in grids.get(layout, grids["dashboard"]).items()}


def _finish(canvas: Image.Image) -> Image.Image:
    bw = canvas.point(lambda p: WHITE if p >= THRESHOLD else BLACK, mode="1")
    return bw.convert("RGB")


def render(page: Page, forecast: Forecast) -> Image.Image:
    canvas = Image.new("L", page.size, WHITE)
    pen = Pen(canvas, Fonts(page.font_style))
    layout = page.layout if page.layout in LAYOUTS else "dashboard"
    r = regions(layout, page.size)
    portrait = page.size[1] > page.size[0]
    today = page.now_local.date()

    draw_header(pen, r["header"], page.label, page.date_text, page.alert, page.stale_text, page.units,
                page.now_local)
    if layout == "dashboard":
        draw_now(pen, r["now"], forecast, page.units)
        if portrait:
            pen.hline(12, page.size[0] - 12, r["today"][1], width=1)
        else:
            x = r["today"][0]
            pen.draw.line([(x, r["today"][1] + 8), (x, r["today"][3] - 8)], fill=BLACK, width=1)
        draw_today(pen, r["today"], forecast, page.units)
        pen.hline(0, page.size[0], r["chart"][1], width=2)
        draw_chart(pen, r["chart"], forecast.hours, page.now_local, page.units)
        draw_days(pen, r["days"], forecast.days, page.units, today)
    elif layout == "hourly":
        title = f"Now {fmt_temp(forecast.current.temperature)} · {wx_icons.describe(forecast.current.code)}"
        draw_chart(pen, r["chart"], forecast.hours, page.now_local, page.units, icon_row=True, title=title)
    else:
        draw_day_cards(pen, r["cards"], forecast.days, page.units, today, vertical=portrait)
    return _finish(canvas)


def render_error(size: tuple[int, int], message: str, detail: str, font_style: str = DEFAULT_FONT_STYLE,
                 label: str = "") -> Image.Image:
    canvas = Image.new("L", size, WHITE)
    pen = Pen(canvas, Fonts(font_style))
    w, h = size
    pen.glyph((w / 2, h * 0.32), wx_icons.glyph("na"), min(w, h) * 0.3)
    title_font = fit_font(pen.fonts, message, w - 40, KEY_TEXT, bold=True)
    pen.text((w / 2, h * 0.55), message, title_font, anchor="mm")
    small = pen.fonts.text(MIN_TEXT + 2)
    for i, line in enumerate(l for l in (label, ellipsize(small, detail, w - 40), "Will retry at the next refresh.") if l):
        pen.text((w / 2, h * 0.55 + 44 + i * 28), line, small, anchor="mm")
    return _finish(canvas)
