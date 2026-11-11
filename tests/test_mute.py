# SPDX-License-Identifier: GPL-3.0-or-later
import os, tempfile, time
from datetime import date, datetime, timedelta
from core import *

p = os.path.join(tempfile.mkdtemp(), "t.db")
db = connect(p)
today = date(2026, 11, 11)
noon = lambda d: datetime.combine(d, datetime.min.time()).timestamp() + 43200
t = noon(today)

def add(ts, cat, src, msg):
    db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,?)",
               (ts, cat, src, msg, fingerprint(src, msg), "b"))

for i in range(5):
    add(t + i, "error", "noisy", f"spam {i}")      # same fingerprint (digits normalised)
add(t + 10, "crash", "firefox", "firefox crashed (signal 11)")
db.execute("INSERT INTO boots VALUES('b',?,?,?,?)", (t - 3600, t + 3600, "6.1", 1)); db.commit()

def score():
    ev, bo = score_inputs(db)
    return daily_scores(ev, bo, today)[today], len(ev)

s0, n0 = score()
fp = fingerprint("noisy", "spam 0")
assert n0 == 6

# 1. mute removes events from score input and raises the score
mute(db, fp, "noisy", "spam 0")
s1, n1 = score()
assert n1 == 1 and s1 > s0, (s0, s1, n1)
# 2. muting twice is harmless
mute(db, fp, "noisy", "spam 0"); assert len(muted_rows(db)) == 1
# 3. persistence across a new connection
db.close(); db = connect(p)
assert [r[0] for r in muted_rows(db)] == [fp] and score()[1] == 1
# 4. events remain in the DB (only hidden)
assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 6
# 5. new events with a muted fingerprint (collector inserts later) stay hidden
add(t + 99, "error", "noisy", "spam 777"); db.commit(); assert score()[1] == 1
# 6. unmute restores the original score exactly
unmute(db, fp)
s2, n2 = score()
assert (s2, n2) == (s0, 7) and muted_rows(db) == []
# 7. muting everything on a day with a boot -> score 10, not a gap
mute(db, fp, "noisy", "x"); mute(db, fingerprint("firefox", "firefox crashed (signal 11)"), "firefox", "x")
assert score()[0] == 10.0
# 8. no boots + all events muted -> day becomes a gap (None), as with an off machine
ev, _ = score_inputs(db)
assert daily_scores(ev, [(t - 86400 * 3, t - 86400 * 3 + 10)], today)[today] is None
print("all ok", s0, s1)
