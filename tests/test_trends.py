# SPDX-License-Identifier: GPL-3.0-or-later
import os, tempfile, time
from core import *

db = connect(os.path.join(tempfile.mkdtemp(), "t.db"))
now = 1_800_000_000.0
D = 86400
def add(days_ago, cat, src, msg="x crashed (signal 11)", i=0):
    db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')",
               (now - days_ago * D - i, cat, src, msg, fingerprint(src, msg)))
for i in range(6): add(5, "crash", "firefox", i=i)
for i in range(2): add(40, "crash", "firefox", i=i)        # previous window
add(3, "crash", "gimp"); add(45, "crash", "gimp"); add(46, "crash", "gimp"); add(47, "crash", "gimp")
add(2, "crash", "newapp")
add(50, "crash", "gone")                                   # only crashed before: not listed
add(1, "error", "spam")                                    # not a crash
add(100, "crash", "ancient")                               # outside both windows
add(4, "crash", "quiet"); mute(db, fingerprint("quiet", "x crashed (signal 11)"), "quiet", "x")
add(65, "error", "old")                                    # history reaches back >30 days
db.commit()

r = app_trends(db, 30, now)
assert [(t["source"], t["n"], t["prev"], t["delta"]) for t in r] == \
       [("firefox", 6, 2, 4), ("newapp", 1, 0, 1), ("gimp", 1, 3, -2)], r   # ties: rising first
assert trend_line(r[0]) == ("6 crashes in the last 30 days (previous 30: 2)", "+4", True)
assert trend_line(r[2])[1:] == ("-2", False) and trend_line(r[1])[0].startswith("1 crash in")
assert app_trends(db, 7, now)[0]["source"] == "firefox"
# young database: nothing to compare with
y = connect(os.path.join(tempfile.mkdtemp(), "y.db"))
y.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')",
          (now - 5 * D, "crash", "app", "m", "f")); y.commit()
t = app_trends(y, 30, now)[0]
assert (t["prev"], t["delta"]) == (None, None) and trend_line(t) == ("1 crash in the last 30 days", "", False)
assert app_trends(connect(os.path.join(tempfile.mkdtemp(), "e.db")), 30, now) == []
print("trends ok")
