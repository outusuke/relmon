# SPDX-License-Identifier: GPL-3.0-or-later
import os, subprocess, sys, tempfile, time
from datetime import date, datetime
from core import *

home = tempfile.mkdtemp()
db = connect(os.path.join(home, ".local/share/relmon/events.db"))
today = date(2026, 11, 11)
noon = lambda d: datetime(2026, 11, d, 12).timestamp()
def add(d, cat, src, msg, i=0):
    db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')",
               (noon(d) + i, cat, src, msg, fingerprint(src, msg)))
for i in range(4): add(10, "crash", "firefox", "firefox crashed (signal 11)", i)
add(11, "oom", "kernel", "Out of memory: Killed process 1 (x)\nsecond `line`")
add(11, "change", "kernel", "Kernel changed: a -> b")
add(1, "crash", "ancient", "outside the window")
add(9, "error", "quiet", "muted noise"); mute(db, fingerprint("quiet", "muted noise"), "quiet", "x")
db.execute("INSERT INTO boots VALUES('b',?,?,?,?)", (noon(5), noon(11), "6.1", 1)); db.commit()

t = report(db, 7, today)
assert t.startswith("Reliability report: Nov 05 - Nov 11, 2026")
assert "Average score:" in t and "7 of 7 days" in t and "Wed Nov 11" in t
assert "crash" in t and "(1 distinct)" in t and "x4" in t and "firefox" in t
assert "ancient" not in t and "quiet" not in t and "Kernel changed" not in t         # window, mute, 'change' excluded
assert "Out of memory: Killed process 1 (x) second `line`" in t   # newline collapsed to one line
assert "6.1" in t and "Kernels" in t
m = report(db, 7, today, md=True)
assert m.startswith("# Reliability report") and "| Wed Nov 11 |" in m and "## Top problems" in m
assert "second 'line'" in m and "- x4 **firefox** (crash)" in m                       # backticks can't break the span
e = connect(os.path.join(tempfile.mkdtemp(), "e.db"))
assert report(e, 3, today).count("none") == 2 and "off" in report(e, 3, today)   # empty DB works

# CLI: both entry points, no GTK, reads the DB only
env = dict(os.environ, HOME=home); here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for script in ("app.py", "collector.py"):
    r = subprocess.run([sys.executable, os.path.join(here, script), "--report", "--days", "3", "--md"],
                       capture_output=True, text=True, env=env, cwd=here)
    assert r.returncode == 0 and r.stdout.startswith("# Reliability report"), (script, r.stderr)
bad = subprocess.run([sys.executable, os.path.join(here, "collector.py"), "--report", "--days", "x"],
                     capture_output=True, text=True, env=env)
assert bad.returncode == 2
print("report ok")
