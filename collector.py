# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads journal errors + coredumps into SQLite. Run: python collector.py [--follow]"""
import fcntl, hashlib, json, os, platform, shlex, subprocess, sys, time
from collections import namedtuple
from datetime import datetime
from core import report, connect, fingerprint, prune, retention_cutoff, new_alerts, classify, parse_failed, DB_PATH

LOCK_PATH = os.path.join(os.path.dirname(DB_PATH), "collect.lock")


def _add(db, ts, cat, sev, src, msg, boot, raw, fp=None):
    db.execute("INSERT OR IGNORE INTO events(ts,category,severity,source,message,fingerprint,boot_id,raw_json)"
               " VALUES(?,?,?,?,?,?,?,?)",
               (ts, cat, sev, src, msg, fp or fingerprint(src, msg), boot or "", json.dumps(raw)))   # '' not NULL: UNIQUE ignores NULLs


def _journal(args):
    try:
        r = subprocess.run(["journalctl", "-p", "3", "-o", "json", "--no-pager"] + args,
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


STAMP_W = 13   # width of "HH:MM:SS.mmm " at the start of a display line
LogLine = namedtuple("LogLine", "prio text hit rel unit pid body n")   # rel = seconds from the event, n = repeat count


def context_args(ts, boot=None, window=30, limit=5000):   # safety cap; window_slice() trims to 300
    f = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")   # journalctl reads local time
    a = ["journalctl", "--no-pager", "-o", "json", "--since", f(ts - window), "--until", f(ts + window),
         "-n", str(limit)]
    return a + ["-b", boot] if boot else a


def format_entry(e):
    """Journal JSON entry -> (timestamp, priority 0..7, display line, unit, pid)."""
    m = e.get("MESSAGE")
    if isinstance(m, list):  # journald encodes binary messages as byte arrays
        try:
            m = bytes(m).decode("utf-8", "replace")
        except (TypeError, ValueError):
            m = str(m)
    m = "" if m is None else str(m)
    ts = int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6
    src = e.get("SYSLOG_IDENTIFIER") or e.get("_COMM") or "?"
    pid = e.get("_PID")
    try:
        prio = int(e.get("PRIORITY", 6))
    except (TypeError, ValueError):
        prio = 6
    unit = e.get("_SYSTEMD_UNIT") or e.get("_SYSTEMD_USER_UNIT") or e.get("UNIT")
    return (ts, prio, f"{datetime.fromtimestamp(ts):%H:%M:%S.%f}"[:-3] + f" {src}" + (f"[{pid}]" if pid else "") + f": {m}",
            unit, pid)


def journalctl_command(ts, boot=None, window=30, warnings=False):
    """Shell command that shows the same journal window in a terminal."""
    f = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")   # journalctl reads local time
    a = ["journalctl", "--no-pager", "--since", f(ts - window), "--until", f(ts + window)]
    if warnings: a += ["-p", "4"]
    if boot: a += ["-b", boot]
    return shlex.join(a)


def window_slice(items, ts, limit=300):
    """items: [(t, ...)] in time order. At most `limit` of them, centred on the one nearest ts
    (journalctl -n would keep the LAST lines and could cut the event off)."""
    if len(items) <= limit:
        return items
    i = min(range(len(items)), key=lambda j: abs(items[j][0] - ts))
    start = max(0, min(i - limit // 2, len(items) - limit))
    return items[start:start + limit]


def context_lines(ts, boot=None, window=30):
    """Journal lines (all priorities) within +-window s of ts.
    Returns (lines, error); lines = [LogLine], one of the two is None."""
    try:
        r = subprocess.run(context_args(ts, boot, window), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"Could not run journalctl: {e}"
    if r.returncode != 0:
        return None, (r.stderr.strip() or "journalctl failed") + "\n(Are you in the systemd-journal group?)"
    items = []
    for raw in r.stdout.splitlines():
        try:
            t, prio, text, unit, pid = format_entry(json.loads(raw))
        except ValueError:
            continue
        items.append((t, prio, text, abs(t - ts) < 0.0005, unit, pid))
    lines = [LogLine(p, x, h, t - ts, u, pid and str(pid), x[STAMP_W:], 1) for t, p, x, h, u, pid in window_slice(items, ts)]
    return lines or None, (None if lines else "No journal lines in this window (the journal may have rotated).")


def filter_lines(lines, errors_only=False, unit=None, pid=None):
    """Lines for the log view. The event line is always kept; errors_only keeps warnings and worse;
    unit/pid keep only lines from that unit or process."""
    out = []
    for l in lines:
        if not l.hit:
            if errors_only and l.prio > 4:
                continue
            if (unit or pid) and not ((unit and l.unit == unit) or (pid and l.pid == str(pid))):
                continue
        out.append(l)
    return out


def collapse(lines):
    """Merge consecutive identical lines into one with a repeat count; the event line is never merged."""
    out = []
    for l in lines:
        p = out[-1] if out else None
        if p and not l.hit and not p.hit and (p.prio, p.body) == (l.prio, l.body):
            out[-1] = p._replace(n=p.n + 1)
        else:
            out.append(l)
    return out


def display_line(l):
    """One log line as shown in the UI: severity word (not just colour), time from the event, message."""
    tag = "ERR " if l.prio <= 3 else "WARN" if l.prio == 4 else "    "
    rel = "event" if l.hit else f"{l.rel:+.1f}s"
    return f"{tag} {rel:>7} {l.body}" + (f"  \u00d7{l.n}" if l.n > 1 else "")


def collect_journal(db):
    cur = db.execute("SELECT v FROM meta WHERE k='cursor'").fetchone()
    out = _journal(["--after-cursor", cur[0]]) if cur else None
    if out is None:  # first run, or saved cursor is gone (journal rotated); duplicates are ignored by UNIQUE
        out = _journal(["--since", "30 days ago"])
    if out is None:
        return
    last = None
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE")
        msg = msg if isinstance(msg, str) else str(msg)
        src = e.get("SYSLOG_IDENTIFIER") or e.get("_COMM") or e.get("_SYSTEMD_UNIT") or "unknown"
        cat = classify(msg, e.get("_TRANSPORT"), "UNIT_RESULT" in e or "JOB_RESULT" in e)
        _add(db, int(e["__REALTIME_TIMESTAMP"]) / 1e6, cat, int(e.get("PRIORITY", 3)),
             src, msg, e.get("_BOOT_ID"), {k: v for k, v in e.items() if k.startswith(("_SYSTEMD", "_PID", "UNIT"))})
        last = e.get("__CURSOR", last)
    if last:
        db.execute("INSERT OR REPLACE INTO meta VALUES('cursor',?)", (last,))


def collect_coredumps(db):
    try:
        out = subprocess.run(["coredumpctl", "list", "--json=short", "--no-pager"],
                             capture_output=True, text=True, timeout=60).stdout
        items = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return
    cut = retention_cutoff()
    for c in items:
        if c["time"] / 1e6 < cut:
            continue   # older than we keep; skipping avoids re-adding what prune() removes
        exe = c.get("exe") or "unknown"
        _add(db, c["time"] / 1e6, "crash", 2, os.path.basename(exe),
             f"{exe} crashed (signal {c.get('sig')})", None, c)


def collect_failed(db, run=subprocess.run, boot=None):
    """`systemctl --failed` (system + user): one 'service' event per failed unit per boot, stamped when first seen.
    Skipped when the journal already recorded a failure of that unit in this boot."""
    if boot is None:
        try:
            boot = open("/proc/sys/kernel/random/boot_id").read().strip().replace("-", "")
        except OSError:
            return
    for scope in ([], ["--user"]):
        try:
            out = run(["systemctl", *scope, "list-units", "--failed", "--output=json", "--no-pager"],
                      capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        for unit, desc in parse_failed(out):
            msg = f"Unit {unit} is in failed state"
            fp = fingerprint(unit, msg)
            if db.execute("SELECT 1 FROM events WHERE category='service' AND boot_id=? "
                          "AND (fingerprint=? OR instr(raw_json, ?)>0) LIMIT 1", (boot, fp, f'"{unit}"')).fetchone():
                continue
            _add(db, time.time(), "service", 3, unit, msg, boot, {"UNIT": unit, "description": desc}, fp=fp)


def _kernel(bid):
    try:
        p = subprocess.Popen(["journalctl", "-b", bid, "-k", "-o", "cat", "--no-pager"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    try:
        for line in p.stdout:
            if line.startswith("Linux version"):
                return line.split()[2]
    finally:
        p.kill(); p.wait()


def _clean(bid):
    """Heuristic: journald logs 'Journal stopped' during a normal shutdown."""
    try:
        out = subprocess.run(["journalctl", "-b", bid, "-n", "100", "-o", "cat", "--no-pager"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int("Journal stopped" in out or "Shutting down" in out)


def collect_boots(db):
    try:
        out = subprocess.run(["journalctl", "--list-boots", "-o", "json", "--no-pager"],
                             capture_output=True, text=True, timeout=60).stdout
        boots = sorted(json.loads(out or "[]"), key=lambda b: b["first_entry"])
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return
    prev = None
    for i, b in enumerate(boots):
        bid, first, last = b["boot_id"], b["first_entry"] / 1e6, b["last_entry"] / 1e6
        current = i == len(boots) - 1
        if last < retention_cutoff():
            continue
        row = db.execute("SELECT kernel, clean FROM boots WHERE boot_id=?", (bid,)).fetchone()
        kernel = platform.release() if current else ((row[0] if row and row[0] else None) or _kernel(bid))
        clean = row[1] if row and row[1] is not None else (None if current else _clean(bid))
        db.execute("INSERT OR REPLACE INTO boots VALUES(?,?,?,?,?)", (bid, first, last, kernel, clean))
        if clean == 0:
            _add(db, last, "unclean", 3, "system", "Boot ended without a clean shutdown", bid, {})
        if kernel and prev and kernel != prev:
            msg = f"Kernel changed: {prev} -> {kernel}"
            _add(db, first, "change", 6, "kernel", msg, bid, {}, fp=hashlib.sha1(msg.encode()).hexdigest()[:16])
        prev = kernel or prev


def prune_old(db):
    prune(db)


def send_desktop(summary, body, run=subprocess.run):
    """Desktop notification over D-Bus with busctl (ships with systemd). True if it was delivered."""
    try:
        r = run(["busctl", "--user", "call", "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications", "Notify", "susssasa{sv}i", "Reliability Monitor", "0",
                 "dialog-warning", summary, body, "0", "0", "-1"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def notify_new(db, send=None):
    """Announce new panics/crashes through send(summary, body). send=None only moves the marker
    (the app window collects too, and you are already looking at it)."""
    msg = new_alerts(db)
    if msg and send:
        send(*msg)


def run_once(db, lock_path=LOCK_PATH, notify=False):
    """Collect once, unless another collector (the app or the systemd timer) holds the lock.
    Returns True if it ran. flock is released automatically on close/crash, so it can't go stale."""
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        for step in (collect_boots, collect_journal, collect_coredumps, collect_failed, prune_old,
                     lambda d: notify_new(d, send_desktop if notify else None)):
            step(db)
            db.commit()   # keep write transactions short
    return True


def debug(db):
    print("persistent journal:", os.path.isdir("/var/log/journal"))
    print("groups:", subprocess.run(["id", "-nG"], capture_output=True, text=True).stdout.strip())
    for r in db.execute("SELECT boot_id, datetime(first_ts,'unixepoch','localtime'), kernel, clean "
                        "FROM boots ORDER BY first_ts"):
        print("boot", r)
    print("events by category:", db.execute("SELECT category, COUNT(*) FROM events GROUP BY category").fetchall())


def main_report(argv):
    """--report [--md] [--days N]: print the summary from the database (no collection, no GTK)."""
    days = 7
    if "--days" in argv:
        try:
            days = max(1, min(90, int(argv[argv.index("--days") + 1])))
        except (IndexError, ValueError):
            print("--days needs a number (1-90)", file=sys.stderr); return 2
    print(report(connect(), days, md="--md" in argv), end="")
    return 0


if __name__ == "__main__":
    if "--report" in sys.argv:
        sys.exit(main_report(sys.argv))
    db = connect()
    run_once(db, notify=True)   # timer / command line: notify; the app window does not
    if "--debug" in sys.argv:
        debug(db)
    while "--follow" in sys.argv:
        time.sleep(60)
        run_once(db, notify=True)
