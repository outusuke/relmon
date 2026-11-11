# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, sqlite3, tempfile, time
from types import SimpleNamespace as NS
import collector
from core import *

C = classify
# kernel messages -> specific categories
assert C("Out of memory: Killed process 4242 (firefox) total-vm:1kB", "kernel") == "oom"
assert C("oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null)", "kernel") == "oom"
assert C("Killed /user.slice/app.slice/foo.scope due to memory pressure for ...", None) == "oom"
assert C("amdgpu 0000:03:00.0: amdgpu: ring gfx_0.0.0 timeout, signaled seq=1", "kernel") == "gpu"
assert C("i915 0000:00:02.0: [drm] GPU HANG: ecode 9:1:85dffffb", "kernel") == "gpu"
assert C("NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus.", "kernel") == "gpu"
assert C("[drm:amdgpu_job_timedout [amdgpu]] *ERROR* ring sdma0 timeout", "kernel") == "gpu"
assert C("blk_update_request: I/O error, dev sda, sector 1234", "kernel") == "disk"
assert C("EXT4-fs error (device nvme0n1p2): ext4_find_entry:1455: inode #2", "kernel") == "disk"
assert C("EXT4-fs (sda1): Remounting filesystem read-only", "kernel") == "disk"
assert C("ata1.00: exception Emask 0x0 SAct 0x0 SErr 0x0 action 0x0", "kernel") == "disk"
assert C("nvme nvme0: I/O 12 QID 3 timeout, aborting", "kernel") == "disk"
assert C("BTRFS error (device sda2): bdev /dev/sda2 errs: wr 1", "kernel") == "disk"
assert C("CPU0: Package temperature above threshold, cpu clock throttled (total events = 5)", "kernel") == "thermal"
assert C("thermal thermal_zone0: critical temperature reached, shutting down", "kernel") == "thermal"
# fallbacks and precedence
assert C("usb 1-1: device descriptor read error", "kernel") == "kernel"
assert C("Kernel panic - not syncing: Fatal exception", "kernel") == "panic"
assert C("Failed with result 'exit-code'.", None, True) == "service"
assert C("something broke", None) == "error"
assert C("Out of memory", None) == "error" and C("room temperature is fine", "kernel") == "kernel"   # no false positives
assert C(None) == "error"
for c in ("oom", "disk", "gpu", "thermal"): assert c in WEIGHTS

# parse_failed
j = json.dumps([{"unit": "foo.service", "load": "loaded", "active": "failed", "sub": "failed", "description": "Foo"},
                {"unit": "bar.mount", "active": "failed"}, {"unit": "ok.service", "active": "active"}, {"x": 1}])
assert parse_failed(j) == [("foo.service", "Foo"), ("bar.mount", "")]
assert parse_failed("") == [] and parse_failed("not json") == [] and parse_failed('{"a":1}') == []

# migration v3 re-files old events and is idempotent
p = os.path.join(tempfile.mkdtemp(), "t.db")
raw = sqlite3.connect(p); raw.executescript(SCHEMA)
now = time.time()
for i, (cat, msg) in enumerate([("kernel", "blk_update_request: I/O error, dev sda"), ("kernel", "usb reset"),
                                ("error", "Out of memory: Killed process 1 (x)"), ("crash", "I/O error in app")]):
    raw.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')", (now - i, cat, "s", msg, str(i)))
raw.commit(); raw.close()
db = connect(p)
q = lambda: [r[0] for r in db.execute("SELECT category FROM events ORDER BY id")]
assert q() == ["disk", "kernel", "oom", "crash"], q()
db.close(); db = connect(p); assert q() == ["disk", "kernel", "oom", "crash"]

# collect_failed: one event per unit per boot; skipped if the journal already has it
def fake(cmd, **kw):
    return NS(stdout=j if "--user" not in cmd else "[]", returncode=0)
collector.collect_failed(db, fake, boot="B1"); collector.collect_failed(db, fake, boot="B1"); db.commit()
rows = db.execute("SELECT source, category, message FROM events WHERE boot_id='B1'").fetchall()
assert sorted(rows) == [("bar.mount", "service", "Unit bar.mount is in failed state"),
                        ("foo.service", "service", "Unit foo.service is in failed state")], rows
collector.collect_failed(db, fake, boot="B2"); db.commit()                      # new boot -> new events
assert db.execute("SELECT COUNT(*) FROM events WHERE boot_id='B2'").fetchone()[0] == 2
db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id,raw_json) VALUES(?,?,?,?,?,?,?)",
           (now, "service", "systemd", "Failed with result", "jf", "B3", json.dumps({"UNIT": "foo.service"})))
collector.collect_failed(db, fake, boot="B3"); db.commit()                      # journal already has foo.service
assert [r[0] for r in db.execute("SELECT source FROM events WHERE boot_id='B3' ORDER BY id")] == ["systemd", "bar.mount"]
def missing(c, **k): raise FileNotFoundError("systemctl")
collector.collect_failed(db, missing, boot="B4")                                # no systemctl -> no-op
assert db.execute("SELECT COUNT(*) FROM events WHERE boot_id='B4'").fetchone()[0] == 0
print("categories ok")
