# SPDX-License-Identifier: GPL-3.0-or-later
import os, subprocess, tempfile, time
from types import SimpleNamespace as NS
import collector
from core import *

db = connect(os.path.join(tempfile.mkdtemp(), "t.db"))
now = time.time()
def add(cat, src, msg, ts=None):
    db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')",
               (ts or now - 5, cat, src, msg, fingerprint(src, msg))); db.commit()

# first call only sets the marker: the backlog is not announced
add("crash", "old", "old crashed (signal 11)")
assert new_alerts(db) is None
# nothing new -> None; ordinary errors / changes never notify
add("error", "spam", "noise"); add("change", "kernel", "Kernel changed: a -> b")
assert new_alerts(db) is None
# one crash -> singular text; same call again -> already announced
add("crash", "firefox", "firefox crashed (signal 11)")
s, b = new_alerts(db)
assert s == "1 new reliability problem" and b == "firefox: firefox crashed (signal 11)", (s, b)
assert new_alerts(db) is None
# repeats collapse (x3); more than 3 problems -> "+N more"; old events (>24h) and muted ones are skipped
for i in range(3): add("crash", "gimp", "gimp crashed (signal 6)", now - 5 - i)
add("panic", "kernel", "Kernel panic - not syncing"); add("unclean", "system", "Boot ended without a clean shutdown")
add("crash", "ancient", "ancient crashed", now - 3 * 86400)
add("crash", "quiet", "quiet crashed"); mute(db, fingerprint("quiet", "quiet crashed"), "quiet", "quiet crashed")
s, b = new_alerts(db)
assert s == "3 new reliability problems" and "gimp: gimp crashed (signal 6) x3" in b and "ancient" not in b and "quiet" not in b, (s, b)
# notify_new: send=None only moves the marker, a sender gets (summary, body)
add("crash", "a", "a crashed"); collector.notify_new(db, None)
assert new_alerts(db) is None
got = []; add("crash", "b", "b crashed"); collector.notify_new(db, lambda *m: got.append(m))
assert got == [("1 new reliability problem", "b: b crashed")]
# send_desktop: busctl argv, success/failure/missing
seen = []
def ok(cmd, **kw): seen.append(cmd); return NS(returncode=0)
assert collector.send_desktop("S", "B", ok) is True
assert seen[0][:2] == ["busctl", "--user"] and "susssasa{sv}i" in seen[0]
assert seen[0][seen[0].index("S"):] == ["S", "B", "0", "0", "-1"]
assert collector.send_desktop("S", "B", lambda c, **k: NS(returncode=1)) is False
def missing(c, **k): raise FileNotFoundError("busctl")
assert collector.send_desktop("S", "B", missing) is False
print("alerts ok")
