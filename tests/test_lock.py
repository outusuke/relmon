# SPDX-License-Identifier: GPL-3.0-or-later
import fcntl, os, subprocess, sys, tempfile
import collector

d = tempfile.mkdtemp(); lp = os.path.join(d, "c.lock")
calls = []
for name in ("collect_boots", "collect_journal", "collect_coredumps", "collect_failed", "prune_old", "notify_new"):
    setattr(collector, name, lambda db, *a, n=name: calls.append(n))
db = type("DB", (), {"commit": lambda self: None})()

# 1. free lock -> runs all six steps
assert collector.run_once(db, lp) is True and len(calls) == 6
# 2. lock held by ANOTHER PROCESS (the timer) -> skipped, nothing collected
holder = subprocess.Popen([sys.executable, "-c",
    "import fcntl,sys;f=open(sys.argv[1],'w');fcntl.flock(f,fcntl.LOCK_EX);print('ready',flush=True);sys.stdin.read()", lp],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
assert holder.stdout.readline().strip() == "ready"
assert collector.run_once(db, lp) is False and len(calls) == 6
# 3. holder exits (even if it had crashed) -> lock is free again
holder.stdin.close(); holder.wait()
assert collector.run_once(db, lp) is True and len(calls) == 12
# 4. a failing step doesn't leave the lock stuck
def boom(db): raise RuntimeError("x")
collector.collect_journal = boom
try: collector.run_once(db, lp)
except RuntimeError: pass
collector.collect_journal = lambda db: None
assert collector.run_once(db, lp) is True
print("lock ok")
