# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, tempfile
import collector
from core import connect, fingerprint

def entry(src, msg, rt, **extra):
    return json.dumps(dict(MESSAGE=msg, SYSLOG_IDENTIFIER=src, PRIORITY="3", _BOOT_ID="b1",
                           __REALTIME_TIMESTAMP=str(rt), **extra))
def add(db, src, msg, rt):
    collector._add(db, rt / 1e6, "error", 3, src, msg, "b1", {})
def names(db): return sorted(r[0] + ":" + r[1] for r in db.execute("SELECT source, message FROM events"))

def fresh():
    db = connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    add(db, "strange_sammet", "boom 1", 1_700_000_001_000_001)   # container row, still in the journal -> removed
    add(db, "sudo", "pam failure", 1_700_000_002_000_002)         # normal row -> kept
    add(db, "strange_sammet", "boom 2", 1_600_000_000_000_000)   # container row older than the journal -> kept
    return db

journal = "\n".join([entry("strange_sammet", "boom 1", 1_700_000_001_000_001, CONTAINER_NAME="strange_sammet"),
                     entry("sudo", "pam failure", 1_700_000_002_000_002)])
# 1. exact rows from containers are removed, everything else stays, and it is done only once
db = fresh(); calls = []
collector._journal = lambda args: calls.append(args) or journal
collector.purge_containers(db)
assert names(db) == ["strange_sammet:boom 2", "sudo:pam failure"], names(db)
assert db.execute("SELECT v FROM meta WHERE k='purged_containers'").fetchone()
collector.purge_containers(db); assert len(calls) == 1
# 2. an unreadable journal changes nothing and is retried next run
db = fresh(); collector._journal = lambda args: None
collector.purge_containers(db)
assert len(names(db)) == 3 and not db.execute("SELECT 1 FROM meta WHERE k='purged_containers'").fetchone()
# 3. collect_journal runs the purge before collecting
db = fresh(); collector._journal = lambda args: journal
collector.collect_journal(db)
assert "strange_sammet:boom 1" not in names(db) and db.execute("SELECT v FROM meta WHERE k='purged_containers'").fetchone()
print("ok")
