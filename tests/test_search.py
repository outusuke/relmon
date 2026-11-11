# SPDX-License-Identifier: GPL-3.0-or-later
import os, tempfile, time
from datetime import datetime
from core import *

db = connect(os.path.join(tempfile.mkdtemp(), "t.db"))
t = datetime(2026, 11, 10, 12).timestamp()
def add(ts, cat, src, msg):
    db.execute("INSERT INTO events(ts,category,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,'b')",
               (ts, cat, src, msg, fingerprint(src, msg)))
add(t, "crash", "firefox", "/usr/bin/firefox crashed (signal 11)")
add(t + 1, "error", "NetworkManager", "device wlan0: 100% failure_rate")
add(t + 2, "error", "gdm", "user_name lookup failed")
add(t + 3, "error", "gdmXlookup", "other")
add(t + 86400, "kernel", "kernel", "usb 1-1: reset")
for i in range(5): add(t + 10 + i, "error", f"svc{i}", f"boom {i}")   # 5 distinct sources
db.commit()
L = lambda **k: list_events(db, **k)
r, more = L(); assert len(r) == 10 and not more and r[0][2] == "kernel"            # newest first
assert [x[2] for x in L(text="FIREFOX")[0]] == ["firefox"]                          # case-insensitive, message or source
assert [x[2] for x in L(text="crashed signal")[0]] == ["firefox"]                  # all words must match
assert [x[2] for x in L(text="networkmanager 100%")[0]] == ["NetworkManager"]      # % is literal
assert [x[2] for x in L(text="user_name")[0]] == ["gdm"]                            # _ is literal (not 'userXname')
assert [x[2] for x in L(text="gdm_")[0]] == []                                      # no wildcard match on gdmX...
assert L(text="nomatch") == ([], False) and len(L(text="  ")[0]) == 10              # blank text = no filter
assert [x[2] for x in L(cat="kernel")[0]] == ["kernel"]
assert len(L(day_start=datetime(2026, 11, 10).timestamp())[0]) == 9                 # one day only
r, more = L(limit=4); assert len(r) == 4 and more                                   # paging: more rows exist
r, more = L(limit=10); assert len(r) == 10 and not more                             # exact fit: no 'more'
r, more = L(limit=4, text="boom"); assert len(r) == 4 and more
mute(db, fingerprint("firefox", "/usr/bin/firefox crashed (signal 11)"), "firefox", "x")
assert L(text="firefox") == ([], False)                                             # muted stay hidden
print("search ok")
