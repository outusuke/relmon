# SPDX-License-Identifier: GPL-3.0-or-later
import os, tempfile
from datetime import date, datetime, timedelta
from core import connect, daily_scores, usual_events, score_inputs, mute, fingerprint, USUAL_DAYS, USUAL_MIN

today = date(2026, 11, 15)
at = lambda n: datetime.combine(today - timedelta(days=n), datetime.min.time()).timestamp() + 43200   # n days ago, noon
fresh = lambda cat, src, n: (at(n), cat, src, fingerprint(src, "msg"))
score = lambda ev, d=0: daily_scores(ev, today=today)[today - timedelta(days=d)]

# the problem: a chronic error every day for a month leaves every day low; a new failure is invisible
chronic = [fresh("kernel", "usb", n) for n in range(30, -1, -1) for _ in range(3)]
s = daily_scores(chronic, today=today)
assert all(s[today - timedelta(days=n)] == 10.0 for n in range(26))            # after 3 days of history it is usual
assert s[today - timedelta(days=29)] < 10 and s[today - timedelta(days=28)] < 10   # not usual yet: counts in full
# ...and a new failure today now shows up as a clear drop
worse = daily_scores(chronic + [fresh("service", "backup.service", 0)], today=today)
assert worse[today] == 8.6 and worse[today - timedelta(days=1)] == 10.0, worse[today]

# the threshold: 3 of the previous 7 days is usual, 2 is not; the day itself never counts toward its own history
def hist(days_ago): return [fresh("kernel", "usb", n) for n in days_ago] + [fresh("kernel", "usb", 0)]
assert score(hist([1, 2, 3])) == 10.0
assert score(hist([1, 2])) < 10.0
assert score(hist([1, 2, 8])) < 10.0                                          # day 8 is outside the 7-day window
assert score(hist([1, 3, 7])) == 10.0                                         # day 7 is inside it
assert score([fresh("kernel", "usb", 0)] * 5) < 10.0                          # many events today, no history

# panics, unclean shutdowns and disk errors are never usual
for cat in ("panic", "unclean", "disk"):
    ev = [fresh(cat, "x", n) for n in range(10, -1, -1)]
    assert score(ev) == score([fresh(cat, "x", 0)]) < 8, cat
# crashes can become usual (only the three above are exempt)
assert score([fresh("crash", "app", n) for n in range(5, -1, -1)]) == 10.0

# "an event" is a fingerprint: same source with a different message is new, not usual
other = (at(0), "kernel", "usb", fingerprint("usb", "a different failure"))
assert score([fresh("kernel", "usb", n) for n in (1, 2, 3)] + [other]) < 10.0
# 3-tuples (no fingerprint) still work and fall back to source + category
assert daily_scores([(at(n), "error", "svc") for n in (0, 1, 2, 3)], today=today)[today] == 10.0
# usual events do not hide unusual ones on the same day
mixed = [fresh("kernel", "usb", n) for n in (0, 1, 2, 3)] + [fresh("panic", "kernel", 0)]
assert score(mixed) == score([fresh("panic", "kernel", 0)]) < 4

# the usual list
db = connect(os.path.join(tempfile.mkdtemp(), "u.db"))
def add(cat, src, msg, n, i=0):
    db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')",
               (at(n) + i, cat, src, msg, fingerprint(src, msg)))
for n in range(1, 6): add("kernel", "usb", "usb 1-1: device descriptor read error -71", n); add("kernel", "usb", "usb 1-1: device descriptor read error -71", n, 5)
for n in (1, 2, 3): add("service", "cups.service", "Failed to start cups", n)
for n in (1, 2): add("service", "rare.service", "Failed to start rare", n)        # only 2 days: not usual
for n in range(1, 6): add("disk", "kernel", "blk_update_request: I/O error", n)    # never usual
for n in range(1, 6): add("error", "quiet", "muted noise", n)
mute(db, fingerprint("quiet", "muted noise"), "quiet", "x")
add("kernel", "newthing", "only today", 0); db.commit()
u = usual_events(db, today)
assert [(x["source"], x["days"], x["count"]) for x in u] == [("usb", 5, 10), ("cups.service", 3, 3)], u
assert u[0]["category"] == "kernel" and u[0]["message"].startswith("usb 1-1")
ev, bo = score_inputs(db)
assert all(len(r) == 4 for r in ev)
assert usual_events(connect(os.path.join(tempfile.mkdtemp(), "e.db")), today) == []
assert (USUAL_DAYS, USUAL_MIN) == (7, 3)
print("usual ok")
