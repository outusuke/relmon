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
assert names(filter_lines(ls, terms={"u"})) == ["b", "EV"] and names(filter_lines(ls, pid="7")) == ["c", "EV"]   # unit "u.service"
assert names(filter_lines(ls, terms={"u"}, pid="7")) == ["b", "c", "EV"]
from collector import related_terms
d = dict(unit="init.scope", pid="1", source="systemd", message="Failed to start zswap-cli.service - Command-line tool.")
assert related_terms(d) == ({"zswap-cli"}, None)          # systemd/PID 1 would match every line
assert related_terms(dict(unit="session-2.scope", pid="4242", source="firefox", message="crashed")) == ({"session-2", "firefox"}, "4242")
assert related_terms(dict(unit=None, pid=None, source="kernel", message="Out of memory: Killed process 5 (x)")) == (set(), None)
zs = [L(6, "zswap-cli[890]: set", pid="890"), L(6, "systemd[1]: Started x.service", pid="1"),
      L(6, "audit[1]: unit=zswap-cli res=failed", pid="1"), L(3, "EV", hit=True)]
assert names(filter_lines(zs, terms={"zswap-cli"})) == ["zswap-cli[890]: set", "audit[1]: unit=zswap-cli res=failed", "EV"]
assert "-12ms" in display_line(LogLine(6, "t", False, -0.012, None, None, "m", 1)) and "+2.5s" in display_line(LogLine(6, "t", False, 2.5, None, None, "m", 1))
c = collapse([L(6, "x"), L(6, "x"), L(6, "x"), L(6, "y"), L(3, "EV", hit=True), L(3, "EV", hit=True)])
assert [(l.body, l.n) for l in c] == [("x", 3), ("y", 1), ("EV", 1), ("EV", 1)]
assert display_line(c[0]).endswith("x  \u00d73") and "ERR" in display_line(L(3, "b")) and "event" in display_line(c[2])

import shlex
from datetime import datetime
from collector import journalctl_command
from core import event_markdown
t0 = datetime(2026, 11, 11, 12, 0, 0).timestamp()
p = shlex.split(journalctl_command(t0, "abc123", 120, True))
assert p[p.index("--since") + 1] == "2026-11-11 11:58:00" and p[p.index("--until") + 1] == "2026-11-11 12:02:00"
assert p[p.index("-b") + 1] == "abc123" and p[p.index("-p") + 1] == "4"
q = shlex.split(journalctl_command(t0)); assert "-b" not in q and "-p" not in q and q[0] == "journalctl"
d = dict(category="oom", source="kernel", message="m", unit=None, pid=None, boot_id=None, boot_start=None, kernel=None)
assert "\u00b1120 s" in event_markdown(d, t0, [L(6, "a"), L(3, "EV", hit=True)], 120)
assert "\u00b130 s" in event_markdown(d, t0, [L(6, "a")])

r = format_entry({"__REALTIME_TIMESTAMP": "1700000000000000", "MESSAGE": "\x1b[2m2026\x1b[0m \x1b[32m INFO\x1b[0m hi\n", "SYSLOG_IDENTIFIER": "x"})
assert r[2].endswith("x: 2026  INFO hi")                    # colour codes and trailing newline gone
M = lambda body, hit=False: LogLine(6, "t", hit, 0.0, None, None, body, 1)
rows = display_line(M("\n".join(f"r{i}" for i in range(10)))).split("\n")
assert len(rows) == 7 and rows[1] == " " * 13 + "r1" and rows[-1] == " " * 13 + "\u2026 4 more lines"   # folded, indented
assert len(display_line(M("\n".join(f"r{i}" for i in range(10)), hit=True)).split("\n")) == 10   # the event is shown whole
assert len(display_line(M("x" * 1000))) < 400 and len(display_line(M("x" * 1000, hit=True))) > 1000
print("context ok")
