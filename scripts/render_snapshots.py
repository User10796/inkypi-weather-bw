"""Render every fixture × layout × orientation to snapshots/*.png.

    python scripts/render_snapshots.py [output_dir]

These PNGs are committed so layout changes can be reviewed as images, and
tests/test_snapshots.py compares fresh renders against them.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from snapshot_cases import CASES, render_case  # noqa: E402


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "snapshots")
    os.makedirs(out, exist_ok=True)
    for case in CASES:
        render_case(case).save(os.path.join(out, case.filename))
    print(f"Wrote {len(CASES)} snapshots to {out}")


if __name__ == "__main__":
    main()
