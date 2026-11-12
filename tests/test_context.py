# SPDX-License-Identifier: GPL-3.0-or-later
from collector import window_slice

items = [(float(i), 6, f"l{i}", False) for i in range(1000)]
assert window_slice(items[:50], 25.0) == items[:50]                       # small input untouched
r = window_slice(items, 500.0)
assert len(r) == 300 and 500.0 in [x[0] for x in r] and r[0][0] == 350.0  # centred on the event
r = window_slice(items, 3.0);   assert len(r) == 300 and r[0][0] == 0.0 and 3.0 in [x[0] for x in r]    # start edge
r = window_slice(items, 998.0); assert len(r) == 300 and r[-1][0] == 999.0 and 998.0 in [x[0] for x in r]  # end edge
r = window_slice(items, 500.3); assert 500.0 in [x[0] for x in r]          # inexact ts -> nearest line

from collector import format_entry, LogLine, filter_lines, collapse, display_line
r = format_entry({"__REALTIME_TIMESTAMP": "1700000000000000", "MESSAGE": "hi", "SYSLOG_IDENTIFIER": "x", "_PID": "7",
                  "PRIORITY": "3", "_SYSTEMD_UNIT": "a.service"})
assert r[3] == "a.service" and r[4] == "7" and r[2][13:] == "x[7]: hi"
L = lambda prio, body, hit=False, unit=None, pid=None: LogLine(prio, "00:00:00.000 " + body, hit, 0.0, unit, pid, body, 1)
ls = [L(6, "a"), L(3, "b", unit="u.service"), L(6, "c", pid="7"), L(4, "d"), L(3, "EV", hit=True), L(6, "e")]
names = lambda x: [l.body for l in x]
assert names(filter_lines(ls)) == ["a", "b", "c", "d", "EV", "e"]
assert names(filter_lines(ls, errors_only=True)) == ["b", "d", "EV"]
assert names(filter_lines(ls, unit="u.service")) == ["b", "EV"] and names(filter_lines(ls, pid="7")) == ["c", "EV"]
assert names(filter_lines(ls, unit="u.service", pid="7")) == ["b", "c", "EV"]
c = collapse([L(6, "x"), L(6, "x"), L(6, "x"), L(6, "y"), L(3, "EV", hit=True), L(3, "EV", hit=True)])
assert [(l.body, l.n) for l in c] == [("x", 3), ("y", 1), ("EV", 1), ("EV", 1)]
assert display_line(c[0]).endswith("x  \u00d73") and "ERR" in display_line(L(3, "b")) and "event" in display_line(c[2])
print("context ok")
