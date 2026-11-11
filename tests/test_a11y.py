# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date
from core import score_rgb, step_index, chart_summary

# step_index: start at the last bar, clamp at the ends, no bars -> None
assert step_index(None, -1, 30) == 29 and step_index(None, 0, 30) == 29
assert step_index(5, -1, 30) == 4 and step_index(0, -1, 30) == 0 and step_index(29, 1, 30) == 29
assert step_index(3, 1, 0) is None

# chart_summary: average, worst day, gaps skipped, empty
d = [date(2026, 11, n) for n in (9, 10, 11)]
s = {d[0]: 9.0, d[1]: None, d[2]: 4.0}
assert chart_summary(d, s) == "Reliability score, last 3 days: average 6.5 of 10, worst day Wed Nov 11 with 4.0."
assert "no data" in chart_summary(d, {x: None for x in d}) and "no data" in chart_summary([], {})

# colours: 3 bands, both themes, >= 3:1 contrast (WCAG non-text) on white / libadwaita dark (#242424)
lin = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
lum = lambda c: 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2])
ratio = lambda a, b: (max(lum(a), lum(b)) + 0.05) / (min(lum(a), lum(b)) + 0.05)
for dark, bg in ((False, (1, 1, 1)), (True, (0x24 / 255,) * 3)):
    cols = [score_rgb(x, dark) for x in (1, 5, 9)]
    assert len(set(cols)) == 3 and score_rgb(3.9, dark) == cols[0] and score_rgb(4, dark) == cols[1] and score_rgb(7, dark) == cols[2]
    for c in cols: assert ratio(c, bg) >= 3.0, (dark, c, ratio(c, bg))
assert score_rgb(1, True) != score_rgb(1, False)
print("a11y ok")
