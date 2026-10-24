"""Fresh renders must match the committed PNGs in snapshots/.

After an intended layout change, regenerate with
    python scripts/render_snapshots.py
and review the PNG diffs in the PR. On a mismatch, the fresh render is
written to snapshots/_actual/ (git-ignored) for comparison.
"""

import os

import pytest
from PIL import Image, ImageChops

from snapshot_cases import CASES, render_case

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS = os.path.join(ROOT, "snapshots")
# Allows for tiny FreeType differences between machines; real layout changes
# move thousands of pixels.
MAX_DIFF_FRACTION = 0.002


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.filename)
def test_matches_committed_snapshot(case):
    expected_path = os.path.join(SNAPSHOTS, case.filename)
    assert os.path.exists(expected_path), "run: python scripts/render_snapshots.py"
    actual = render_case(case).convert("L")
    expected = Image.open(expected_path).convert("L")
    assert actual.size == expected.size
    diff = ImageChops.difference(actual, expected)
    changed = sum(diff.histogram()[1:]) / (actual.width * actual.height)
    if changed > MAX_DIFF_FRACTION:
        os.makedirs(os.path.join(SNAPSHOTS, "_actual"), exist_ok=True)
        actual.save(os.path.join(SNAPSHOTS, "_actual", case.filename))
    assert changed <= MAX_DIFF_FRACTION, f"{changed:.2%} of pixels differ from {case.filename}"
