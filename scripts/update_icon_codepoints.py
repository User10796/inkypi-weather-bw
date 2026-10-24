"""Print the CODEPOINTS dict for every icon name used in wx_icons.py.

    python scripts/update_icon_codepoints.py path/to/weather-icons/css/weather-icons.css

Paste the output over CODEPOINTS in weather_bw/wx_icons.py.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weather_bw import wx_icons  # noqa: E402


def main(css_path):
    css = open(css_path, encoding="utf-8").read()
    found = dict(re.findall(r'\.wi-([a-z0-9-]+):before\s*\{\s*content:\s*"\\(f[0-9a-f]+)"', css))
    names = {n for _, day, night in wx_icons.WMO.values() for n in (day, night)}
    names |= set(wx_icons.EXTRA_ICONS)
    missing = sorted(n for n in names if n not in found)
    if missing:
        sys.exit(f"Not in CSS: {missing}")
    print("CODEPOINTS = {")
    for name in sorted(names):
        print(f'    "{name}": "\\u{found[name]}",')
    print("}")


if __name__ == "__main__":
    main(sys.argv[1])
