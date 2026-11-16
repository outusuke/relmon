# SPDX-License-Identifier: GPL-3.0-or-later
import math, os, tempfile
from datetime import date, datetime, timedelta
import core
from core import (connect, daily_scores, usual_events, explain_text, fingerprint, _usual_keys, SCORE_K,
                  SPIKE_FACTOR, SPIKE_MIN)

today = date(2026, 11, 15)
at = lambda n: datetime.combine(today - timedelta(days=n), datetime.min.time()).timestamp() + 43200
ev = lambda cat, src, n, k=1: [(at(n) + i, cat, src, fingerprint(src, "m")) for i in range(k)]
score = lambda events: daily_scores(events, today=today)[today]
def chronic(per_day, days=(1, 2, 3, 4, 5)):
    return [e for n in days for e in ev("kernel", "usb", n, per_day)]
assert (SPIKE_FACTOR, SPIKE_MIN) == (5, 10)

# a steady usual event stays usual; one that jumps to 5x its busiest day (and >= 10 events) counts again
assert score(chronic(20) + ev("kernel", "usb", 0, 20)) == 10.0
assert score(chronic(20) + ev("kernel", "usb", 0, 99)) == 10.0
assert score(chronic(20) + ev("kernel", "usb", 0, 100)) < 10.0
# small numbers never count as a spike: 1 a day becoming 9 is still usual, 10 is not
assert score(chronic(1) + ev("kernel", "usb", 0, 9)) == 10.0
assert score(chronic(1) + ev("kernel", "usb", 0, 10)) < 10.0
# it is judged against the busiest recent day, so a bursty event needs a much bigger jump
bursty = [e for n, k in ((1, 2), (2, 2), (3, 200)) for e in ev("kernel", "usb", n, k)]
assert score(bursty + ev("kernel", "usb", 0, 900)) == 10.0
assert score(bursty + ev("kernel", "usb", 0, 1000)) < 10.0
# a problem that stays at the higher level becomes usual again the next day
lvl = chronic(20, (4, 5, 6)) + ev("kernel", "usb", 1, 100) + ev("kernel", "usb", 0, 100)
s = daily_scores(lvl, today=today)
assert s[today - timedelta(days=1)] < 10.0 and s[today] == 10.0
# an event that is not usual yet is unaffected (counts in full either way)
assert score(ev("kernel", "usb", 1, 20) + ev("kernel", "usb", 0, 20)) < 10.0
# a spike on one event does not bring back the other usual events that day
other = [e for n in (1, 2, 3, 4, 5) for e in ev("service", "cups", n)]
spiked = chronic(20) + other + ev("kernel", "usb", 0, 100) + ev("service", "cups", 0)
assert score(spiked) == score(chronic(20) + ev("kernel", "usb", 0, 100))
# plain sets per day (no counts) still work and have no spike check, as in the older helper
assert ("kernel", "x") in _usual_keys({today - timedelta(days=n): {("kernel", "x")} for n in (1, 2, 3)}, today)

# usual_events leaves out an event that is spiking today, and its count covers the history days only
db = connect(os.path.join(tempfile.mkdtemp(), "s.db"))
def add(n, k, off=0):
    for i in range(off, off + k):
        db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')",
                   (at(n) + i, "kernel", "usb", "m", fingerprint("usb", "m")))
for n in (1, 2, 3, 4, 5): add(n, 20)
add(0, 20); db.commit()
u = usual_events(db, today)
assert [(x["source"], x["days"], x["count"]) for x in u] == [("usb", 5, 100)], u
add(0, 80, 20); db.commit()   # 100 today: spike
assert usual_events(db, today) == []

# the explanation: the pieces add up to the score, caps and usual sources are named
events = (chronic(20) + [e for n in (1, 2, 3) for e in ev("service", "cups", n)]
          + [e for src in "abcdefgh" for e in ev("error", src, 0)]   # 8 different errors today: over the error cap
          + ev("crash", "firefox", 0, 3) + ev("service", "cups", 0) + ev("kernel", "usb", 0, 20))
detail = {}
s = daily_scores(events, today=today, detail=detail)[today]
d = detail[today]
assert abs(1 + 9 * math.exp(-sum(c for _, c in d["penalty"].values()) / SCORE_K) - s) < 0.06
assert d["penalty"]["error"][0] > d["penalty"]["error"][1] and ("crash", "firefox") in d["counted"]
assert ("kernel", "usb") in d["usual"] and ("service", "cups") in d["usual"] and ("kernel", "usb") not in d["counted"]
text = explain_text(s, d)
assert text.startswith(f"score {s:.1f}: crash") and "(capped, was" in text and text.endswith("2 usual sources not counted"), text
assert explain_text(None, None) == "machine was off"
assert explain_text(10.0, None) == "score 10.0: nothing counted"
quiet = {}; q = daily_scores(chronic(20) + ev("kernel", "usb", 0, 20), today=today, detail=quiet)[today]
assert explain_text(q, quiet[today]) == "score 10.0: nothing counted. 1 usual source not counted"
print("spike ok")
