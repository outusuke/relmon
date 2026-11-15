# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, tempfile, time
from datetime import date, datetime
import collector
from core import connect, daily_scores, CATEGORY_CAP

today = date(2026, 11, 15)
noon = datetime(2026, 11, 15, 12).timestamp()

# hundreds of different one-off error sources must not sink the day on their own
many = [(noon + i, "error", f"container_{i}") for i in range(300)]
s = daily_scores(many, today=today)[today]
assert s == round(1 + 9 * 2.718281828 ** (-CATEGORY_CAP["error"] / 6.0), 1), s
assert s > 7, s                                   # about 7.4: a noisy day, not a broken one
# the cap is per category: a crash still counts in full on top
crash = daily_scores(many + [(noon, "crash", "firefox")], today=today)[today]
assert crash < s - 1, (crash, s)
# panics are never capped
assert daily_scores([(noon, "panic", "kernel")], today=today)[today] < 4
# below the cap nothing changes
assert daily_scores([(noon, "error", "a"), (noon, "error", "b")], today=today)[today] == round(1 + 9 * 2.718281828 ** (-0.6 / 6.0), 1)

# collector: container output is skipped, the cursor still advances, normal entries are kept
def entry(src, msg, cursor, **extra):
    return json.dumps(dict(MESSAGE=msg, SYSLOG_IDENTIFIER=src, PRIORITY="3", _BOOT_ID="b1", __CURSOR=cursor,
                           __REALTIME_TIMESTAMP=str(int(time.time() * 1e6)), **extra))
assert collector.from_container({"CONTAINER_NAME": "x"}) and collector.from_container({"CONTAINER_ID": "abc"})
assert not collector.from_container({"SYSLOG_IDENTIFIER": "sudo"}) and not collector.from_container({"CONTAINER_NAME": ""})
db = connect(os.path.join(tempfile.mkdtemp(), "t.db"))
lines = [entry("sudo", "pam failure", "c1"), entry("strange_sammet", "boom 1", "c2", CONTAINER_NAME="strange_sammet"),
         entry("podman_app", "boom 2", "c3", CONTAINER_ID="0123456789ab")]
collector._journal = lambda args: "\n".join(lines)
collector.collect_journal(db)
assert [r[0] for r in db.execute("SELECT source FROM events")] == ["sudo"]
assert db.execute("SELECT v FROM meta WHERE k='cursor'").fetchone()[0] == "c3"
print("ok")
