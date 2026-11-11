# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import date, datetime
from core import kernel_report

T = lambda d, h=12: datetime(2026, 11, d, h).timestamp()
# A: days 1-4 (score ~10); switch on day 5 (A until 09:00, B from 15:00); B: days 5-8 (scores ~6)
boots = [(T(1, 1), T(5, 9), "A"), (T(5, 15), T(8, 23), "B"), (T(9, 1), T(9, 23), "C"), (T(10, 1), T(10, 23), "")]
sc = {date(2026, 11, d): s for d, s in zip(range(1, 11), (10, 10, None, 9.0, 8.0, 6, 7, 5, 4.0, 10))}
r = kernel_report(sc, boots)
assert [x["kernel"] for x in r] == ["A", "B", "C"]
a, b, c = r
assert (a["days"], a["avg"], a["delta"], a["worse"]) == (3, 9.7, None, False)   # day 3 off, day 5 ambiguous -> skipped
assert (b["days"], b["avg"], b["delta"], b["worse"]) == (3, 6.0, -3.7, True)    # days 6-8
assert (c["days"], c["delta"], c["worse"]) == (1, None, False)                  # 1 day: no verdict
assert a["first"] == date(2026, 11, 1)
# unknown kernel ignored; empty input
assert kernel_report(sc, [(T(1), T(2), None)]) == [] and kernel_report({}, []) == []
# rollback: A, B, A again -> A is one entry (grouped), ordered by first use
boots2 = [(T(1, 1), T(2, 23), "A"), (T(3, 1), T(4, 23), "B"), (T(5, 1), T(6, 23), "A")]
sc2 = {date(2026, 11, d): 10.0 for d in range(1, 7)}
r2 = kernel_report(sc2, boots2)
assert [(x["kernel"], x["days"]) for x in r2] == [("A", 4), ("B", 2)] and r2[1]["delta"] == 0.0
print("kernel ok")
