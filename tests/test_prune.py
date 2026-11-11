# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, sqlite3, tempfile, time
from types import SimpleNamespace as NS
import collector
from core import *

p = os.path.join(tempfile.mkdtemp(), "t.db")
now = time.time()

# 1. migration: old DB with NULL-boot duplicates -> one row each, boot_id ''
raw = sqlite3.connect(p); raw.executescript(SCHEMA)
for ts, fp in ((now - 10, "a"), (now - 10, "a"), (now - 10, "a"), (now - 20, "b")):
    raw.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,NULL)", (ts, "crash", "x", "m", fp))
raw.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,?)", (now, "error", "y", "m", "c", "boot1"))
raw.commit(); raw.close()
db = connect(p)
assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
assert db.execute("SELECT COUNT(*) FROM events WHERE boot_id IS NULL").fetchone()[0] == 0
db.close(); db = connect(p)   # running the migration again changes nothing
assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3

# 2. collect_coredumps is idempotent (fake coredumpctl output)
dump = json.dumps([{"time": int((now - 100) * 1e6), "exe": "/usr/bin/foo", "sig": 11}])
collector.subprocess.run = lambda *a, **k: NS(stdout=dump, returncode=0)
before = db.execute("SELECT COUNT(*) FROM events WHERE category='crash'").fetchone()[0]
for _ in range(3): collector.collect_coredumps(db)
db.commit()
assert db.execute("SELECT COUNT(*) FROM events WHERE category='crash'").fetchone()[0] == before + 1

# 3. coredumps older than retention are not re-added
old = json.dumps([{"time": int((now - 200 * 86400) * 1e6), "exe": "/usr/bin/bar", "sig": 6}])
collector.subprocess.run = lambda *a, **k: NS(stdout=old, returncode=0)
collector.collect_coredumps(db); db.commit()
assert db.execute("SELECT COUNT(*) FROM events WHERE source='bar'").fetchone()[0] == 0

# 4. prune removes old events/boots, keeps recent events and mutes
db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,?)", (now - 100 * 86400, "error", "z", "m", "old", "b0"))
db.execute("INSERT INTO boots VALUES('b0',?,?,?,?)", (now - 101 * 86400, now - 100 * 86400, "6.1", 1))
db.execute("INSERT INTO boots VALUES('b1',?,?,?,?)", (now - 3600, now, "6.1", None))
mute(db, "old", "z", "m"); db.commit()
n_before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
assert prune(db) == 1
assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == n_before - 1
assert [r[0] for r in db.execute("SELECT boot_id FROM boots")] == ["b1"]
assert len(muted_rows(db)) == 1
print("prune ok")
