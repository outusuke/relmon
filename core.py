# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure logic: schema, fingerprinting, scoring. No GTK imports (easy to port to Rust)."""
import hashlib, json, math, os, re, sqlite3, time
from datetime import date, datetime, timedelta

DB_PATH = os.path.expanduser("~/.local/share/relmon/events.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, category TEXT NOT NULL,
  severity INTEGER, source TEXT, message TEXT, fingerprint TEXT,
  boot_id TEXT, raw_json TEXT,
  UNIQUE(ts, fingerprint, boot_id));
CREATE INDEX IF NOT EXISTS idx_ts ON events(ts);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS boots(
  boot_id TEXT PRIMARY KEY, first_ts REAL, last_ts REAL, kernel TEXT, clean INTEGER);
CREATE TABLE IF NOT EXISTS muted(
  fingerprint TEXT PRIMARY KEY, source TEXT, message TEXT, muted_at REAL);
"""

# SQL fragment: events that are not muted (events stay in the DB; muting only hides them)
NOT_MUTED = "NOT EXISTS (SELECT 1 FROM muted m WHERE m.fingerprint = events.fingerprint)"

# category -> penalty per event
WEIGHTS = {"panic": 8.0, "crash": 1.5, "oom": 2.0, "disk": 2.0, "gpu": 1.5, "thermal": 1.0,
           "kernel": 1.0, "service": 1.0, "unclean": 4.0, "error": 0.3, "change": 0.0}

# message patterns (matched on the lower-cased text) for the specific kernel/hardware categories
PATTERNS = [
    ("oom", r"out of memory: (killed|kill process)|oom-kill|oom_kill|invoked oom-killer|oom_reaper|killed process \d+|due to memory pressure"),
    ("gpu", r"gpu hang|gpu reset|gpu fault|ring \S+ timeout|\bnvrm: xid|\[drm[^\]]*\].*\*error\*|"
            r"amdgpu.*(timeout|hang|reset|fault)|i915.*(hang|reset)|nouveau.*(fault|timeout|error)"),
    ("disk", r"i/o error|buffer i/o|blk_update_request|medium error|remounting filesystem read-only|"
             r"\bata\d+(\.\d+)?: (failed command|exception emask)|nvme\d*.*(timeout|controller is down)|"
             r"\b(ext4|f2fs|fat|exfat|ntfs3?)-fs\b.*(error|warning)|\bxfs\b.*(corrupt|metadata)|\bbtrfs\b.*(error|corrupt)"),
    ("thermal", r"temperature above threshold|core temperature|package temperature|critical temperature|"
                r"thermal throttl|thermal.*(critical|trip)|cpu clock throttled|overheat"),
]
_PATTERNS = [(k, re.compile(p)) for k, p in PATTERNS]
REPEAT_CAP = 2.0   # n repeats from one source count 1+log10(n) times, at most this many
SCORE_K = 6.0      # softness of the curve: bigger = more forgiving
RETENTION_DAYS = 90   # older events/boots are pruned


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")   # readers never block on the collector's writes
    db.executescript(SCHEMA)
    _migrate(db)
    return db


def _migrate(db):
    """v2: a NULL boot_id defeated UNIQUE (NULLs are distinct), so coredumps were re-inserted every run.
    Drop those duplicates once, then store '' instead of NULL.
    v3: re-file old 'kernel'/'error' events into the specific categories (oom, gpu, disk, thermal)."""
    done = {r[0] for r in db.execute("SELECT k FROM meta WHERE k IN ('v2','v3')")}
    if "v2" not in done:
        db.execute("DELETE FROM events WHERE boot_id IS NULL AND id NOT IN "
                   "(SELECT MIN(id) FROM events WHERE boot_id IS NULL GROUP BY ts, fingerprint)")
        db.execute("UPDATE events SET boot_id='' WHERE boot_id IS NULL")
        db.execute("INSERT INTO meta VALUES('v2','1')")
    if "v3" not in done:
        for i, msg in db.execute("SELECT id, message FROM events WHERE category IN ('kernel','error')").fetchall():
            if cat := specific_category(msg):
                db.execute("UPDATE events SET category=? WHERE id=?", (cat, i))
        db.execute("INSERT INTO meta VALUES('v3','1')")
    db.commit()


def specific_category(message):
    """'oom' | 'gpu' | 'disk' | 'thermal' if the message matches, else None."""
    m = (message or "").lower()
    return next((k for k, rx in _PATTERNS if rx.search(m)), None)


def classify(message, transport=None, service_result=False):
    """Event category from a journal entry's message, _TRANSPORT and whether it carries UNIT_RESULT/JOB_RESULT."""
    if "Kernel panic" in (message or ""):
        return "panic"
    if cat := specific_category(message):
        return cat
    if transport == "kernel":
        return "kernel"
    return "service" if service_result else "error"


def parse_failed(text):
    """Unit names from `systemctl list-units --failed --output=json`: [(unit, description)]. Bad input -> []."""
    try:
        items = json.loads(text or "[]")
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [(u["unit"], u.get("description") or "") for u in items
            if isinstance(u, dict) and u.get("unit") and u.get("active", "failed") == "failed"]


def retention_cutoff(days=RETENTION_DAYS, now=None):
    return (now or time.time()) - days * 86400


def prune(db, days=RETENTION_DAYS, now=None):
    """Delete events and boots older than `days`. Mutes are kept. Returns the number of events removed."""
    cut = retention_cutoff(days, now)
    n = db.execute("DELETE FROM events WHERE ts<?", (cut,)).rowcount
    db.execute("DELETE FROM boots WHERE last_ts<?", (cut,))
    db.commit()
    return n


def fingerprint(source, message):
    norm = re.sub(r"0x[0-9a-f]+|\d+", "#", (message or "").lower())
    return hashlib.sha1(f"{source}|{norm}".encode()).hexdigest()[:16]


def mute(db, fp, source, message):
    db.execute("INSERT OR IGNORE INTO muted VALUES(?,?,?,?)", (fp, source, (message or "")[:200], time.time()))
    db.commit()


def unmute(db, fp):
    db.execute("DELETE FROM muted WHERE fingerprint=?", (fp,))
    db.commit()


def muted_rows(db):
    """[(fingerprint, source, message, muted_at)], newest first."""
    return db.execute("SELECT fingerprint, source, message, muted_at FROM muted ORDER BY muted_at DESC").fetchall()


def score_inputs(db):
    """(events, boots) for daily_scores(), with muted fingerprints excluded."""
    rows = db.execute(f"SELECT ts,category,source FROM events WHERE {NOT_MUTED}").fetchall()
    return rows, db.execute("SELECT first_ts, last_ts FROM boots").fetchall()


def new_alerts(db, now=None):
    """(summary, body) for panics/crashes/unclean shutdowns collected since the last call, else None.
    Always advances a marker; the very first call only sets it, so the old backlog is not announced."""
    top = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
    last = db.execute("SELECT v FROM meta WHERE k='notified'").fetchone()
    db.execute("INSERT OR REPLACE INTO meta VALUES('notified',?)", (str(top),)); db.commit()
    if not last:
        return None
    rows = db.execute("SELECT source, message, COUNT(*) FROM events WHERE id>? AND id<=? AND ts>? "
                      f"AND category IN ('panic','crash','unclean') AND {NOT_MUTED} "
                      "GROUP BY fingerprint ORDER BY MAX(ts) DESC",
                      (int(last[0]), top, (now or time.time()) - 86400)).fetchall()
    if not rows:
        return None
    n = len(rows)
    lines = [f"{s}: {(m or '')[:80]}" + (f" x{k}" if k > 1 else "") for s, m, k in rows[:3]]
    return (f"{n} new reliability problem{'s' if n > 1 else ''}", "\n".join(lines) + (f"\n+{n - 3} more" if n > 3 else ""))


def app_trends(db, days=30, now=None):
    """Crashes per app over the last `days` days vs the `days` before: [dict(source, n, prev, delta)], worst first.
    Only apps that crashed in the recent window. prev/delta are None when the history is shorter than `days`
    (nothing to compare with). Muted events are excluded."""
    now = now or time.time()
    cut1, cut0 = now - days * 86400, now - 2 * days * 86400
    start = db.execute("SELECT MIN(a) FROM (SELECT MIN(ts) a FROM events UNION ALL SELECT MIN(first_ts) FROM boots)").fetchone()[0]
    known = start is not None and start <= cut1
    rows = db.execute(f"SELECT source, SUM(ts>=?), SUM(ts<?) FROM events WHERE category='crash' AND ts>=? AND ts<=? "
                      f"AND {NOT_MUTED} GROUP BY source", (cut1, cut1, cut0, now)).fetchall()
    out = [dict(source=s or "?", n=n, prev=p if known else None, delta=n - p if known else None)
           for s, n, p in rows if n]
    return sorted(out, key=lambda t: (-t["n"], -(t["delta"] or 0), t["source"]))


def trend_line(t, days=30):
    """(subtitle, suffix, worse) for an app_trends() entry."""
    n = t["n"]
    sub = f"{n} crash{'es' if n != 1 else ''} in the last {days} days"
    if t["delta"] is None:
        return sub, "", False
    return sub + f" (previous {days}: {t['prev']})", f"{t['delta']:+d}", t["delta"] > 0


PAGE = 300   # rows per page in the event list


def _like(s):
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_events(db, cat="all", day_start=None, text="", limit=PAGE):
    """Grouped event list, newest first: ([(last_ts, category, source, message, count, fingerprint)], more).
    cat 'all' = every category; day_start = local midnight timestamp of one day; text = words that must
    each appear (case-insensitive) in the source or the message; more = rows beyond `limit` exist."""
    q, args = f"SELECT MAX(ts),category,source,message,COUNT(*),fingerprint FROM events WHERE {NOT_MUTED}", []
    if cat != "all":
        q += " AND category=?"; args.append(cat)
    if day_start is not None:
        q += " AND ts>=? AND ts<?"; args += [day_start, day_start + 86400]
    for w in (text or "").split():
        q += " AND (source LIKE ? ESCAPE '\\' OR message LIKE ? ESCAPE '\\')"; args += [_like(w)] * 2
    rows = db.execute(q + " GROUP BY fingerprint ORDER BY MAX(ts) DESC LIMIT ?", args + [limit + 1]).fetchall()
    return rows[:limit], len(rows) > limit


def kernel_boots(db):
    """[(first_ts, last_ts, kernel)] for kernel_report()."""
    return db.execute("SELECT first_ts, last_ts, kernel FROM boots").fetchall()


def kernel_report(scores, boots, min_days=2, drop=1.0):
    """Average daily score per kernel, oldest first: [dict(kernel, first, days, avg, delta, worse)].
    scores: daily_scores() output; boots: [(first_ts, last_ts, kernel)].
    Days with no score (machine off) or with two kernels (the switch day) are skipped.
    delta = avg minus the previous kernel's avg, None unless both have >= min_days days; worse = delta <= -drop."""
    day_k = {}
    for f, l, k in sorted(boots):
        if not k:
            continue
        d, e = datetime.fromtimestamp(f).date(), datetime.fromtimestamp(l).date()
        while d <= e:
            day_k[d] = k if day_k.get(d, k) == k else None   # None = ambiguous day
            d += timedelta(days=1)
    per = {}
    for d in sorted(day_k):
        if day_k[d] and scores.get(d) is not None:
            per.setdefault(day_k[d], []).append((d, scores[d]))
    out, prev = [], None   # prev = (avg, days) of the previous kernel
    for k, items in sorted(per.items(), key=lambda kv: kv[1][0][0]):
        avg = sum(s for _, s in items) / len(items)
        delta = avg - prev[0] if prev and len(items) >= min_days and prev[1] >= min_days else None
        out.append(dict(kernel=k, first=items[0][0], days=len(items), avg=round(avg, 1),
                        delta=None if delta is None else round(delta, 1),
                        worse=delta is not None and delta <= -drop))
        prev = (avg, len(items))
    return out


def event_detail(db, fp, ts):
    """Details of the event shown in the list (fingerprint + its timestamp): unit, pid, boot. None if missing."""
    r = db.execute("SELECT category,source,message,boot_id,raw_json FROM events WHERE fingerprint=? AND ts=? LIMIT 1",
                   (fp, ts)).fetchone()
    if not r:
        return None
    cat, src, msg, boot, raw = r
    try:
        raw = json.loads(raw or "{}")
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    unit = raw.get("_SYSTEMD_UNIT") or raw.get("_SYSTEMD_USER_UNIT") or raw.get("UNIT") or raw.get("unit")
    pid = raw.get("_PID") or raw.get("pid")
    if boot:
        b = db.execute("SELECT boot_id, first_ts, kernel FROM boots WHERE boot_id=?", (boot,)).fetchone()
    else:  # coredump events carry no boot id: find the boot whose time range contains the event
        b = db.execute("SELECT boot_id, first_ts, kernel FROM boots WHERE first_ts<=? AND last_ts>=? "
                       "ORDER BY first_ts DESC LIMIT 1", (ts + 60, ts - 60)).fetchone()
    return dict(category=cat, source=src, message=msg, unit=unit, pid=str(pid) if pid else None,
                boot_id=b[0] if b else boot, boot_start=b[1] if b else None, kernel=b[2] if b else None)


def boot_label(d):
    """Short boot description from an event_detail() dict."""
    b = d["boot_id"]
    if not b:
        return "unknown"
    if not d["boot_start"]:
        return b[:8]
    return f"{b[:8]} (started {datetime.fromtimestamp(d['boot_start']):%b %d %H:%M}, kernel {d['kernel'] or '?'})"


def _fence(text):
    f = "```"
    while f in text:
        f += "`"
    return f


def event_markdown(d, ts, lines=None):
    """Bug-report Markdown for an event_detail() dict. lines: [(prio, text, is_event)] or None."""
    msg = d["message"] or ""
    f = _fence(msg)
    out = [f"### {d['source']}",
           f"- **Time:** {datetime.fromtimestamp(ts).astimezone():%Y-%m-%d %H:%M:%S %z}",
           f"- **Category:** {d['category']}",
           f"- **Unit:** {d['unit'] or '-'}",
           f"- **PID:** {d['pid'] or '-'}",
           f"- **Boot:** {boot_label(d)}",
           f"- **Kernel:** {d['kernel'] or '?'}", "",
           "**Message:**", f, msg, f]
    if lines:
        body = "\n".join((">> " if hit else "   ") + text for _, text, hit in lines)
        f = _fence(body)
        out += ["", "**Journal (±30 s, `>>` marks the event):**", f, body, f]
    else:
        out += ["", "_Journal lines unavailable._"]
    return "\n".join(out) + "\n"


def bar_index(x, width, n):
    """Index of the chart bar under pixel x (n equal-width bars across width), or None if outside."""
    if n <= 0 or width <= 0 or x < 0 or x >= width:
        return None
    return min(int(x / (width / n)), n - 1)


def label_step(bar_w, min_px=34):
    """Label every Nth bar (counted back from the last) so labels are at least min_px apart."""
    return max(1, math.ceil(min_px / bar_w)) if bar_w > 0 else 1


def day_label(d):
    return f"{d:%b} {d.day}" if d.day == 1 else str(d.day)


def score_rgb(s, dark=False):
    """Bar colour for a score. Separate palettes keep every colour >= 3:1 against the view background."""
    i = 0 if s < 4 else 1 if s < 7 else 2
    return (((0.95, 0.35, 0.30), (0.96, 0.72, 0.20), (0.40, 0.80, 0.45)) if dark else
            ((0.75, 0.11, 0.11), (0.78, 0.47, 0.00), (0.15, 0.55, 0.25)))[i]


def step_index(i, delta, n):
    """Keyboard move over n chart bars: start at the last bar, clamp at both ends; None if there are no bars."""
    if n <= 0:
        return None
    return n - 1 if i is None else max(0, min(n - 1, i + delta))


def chart_summary(days, scores):
    """Screen-reader text for the whole chart."""
    got = [(scores[d], d) for d in days if scores.get(d) is not None]
    if not got:
        return "Reliability score chart: no data."
    worst = min(got, key=lambda x: x[0])
    return (f"Reliability score, last {len(days)} days: average {sum(s for s, _ in got) / len(got):.1f} of 10, "
            f"worst day {worst[1]:%a %b} {worst[1].day} with {worst[0]:.1f}.")


def score_tooltip(d, s):
    return f"{d:%a %b} {d.day}: " + ("machine was off" if s is None else f"score {s:.1f}/10")


def daily_scores(events, boots=None, today=None):
    """events: iterable of (ts, category, source). Returns {date: score 1..10}.
    boots: optional [(first_ts, last_ts)]; days with no boot and no events become None (gap).
    Other days without events count as 10."""
    today = today or date.today()
    per = {}  # (day, source, category) -> number of events
    first = today
    active = None
    if boots:
        active = set()
        for f, l in boots:
            d, e = datetime.fromtimestamp(f).date(), datetime.fromtimestamp(l).date()
            first = min(first, d)
            while d <= e:
                active.add(d); d += timedelta(days=1)
    for ts, cat, src in events:
        d = datetime.fromtimestamp(ts).date()
        first = min(first, d)
        if active is not None:
            active.add(d)
        per[(d, src, cat)] = per.get((d, src, cat), 0) + 1
    totals = {}
    for (d, _, cat), n in per.items():
        totals[d] = totals.get(d, 0) + WEIGHTS.get(cat, 0.3) * min(REPEAT_CAP, 1 + math.log10(n))
    out, d = {}, first
    while d <= today:
        gap = active is not None and d not in active
        out[d] = None if gap else round(1 + 9 * math.exp(-totals.get(d, 0) / SCORE_K), 1)
        d += timedelta(days=1)
    return out


def report(db, days=7, today=None, md=False):
    """Plain-text or Markdown summary of the last `days` days (muted events excluded). No GTK; reference for ports."""
    today = today or date.today()
    rows, boots = score_inputs(db)
    scores = daily_scores(rows, boots, today)
    ds = [today - timedelta(n) for n in range(days - 1, -1, -1)]
    t0 = datetime.combine(ds[0], datetime.min.time()).timestamp()
    vals = [scores.get(d) for d in ds]
    got = [v for v in vals if v is not None]
    where = f"ts>=? AND category!='change' AND {NOT_MUTED}"
    cats = db.execute(f"SELECT category,COUNT(*),COUNT(DISTINCT fingerprint) FROM events WHERE {where} "
                      "GROUP BY category ORDER BY COUNT(*) DESC", (t0,)).fetchall()
    top = db.execute(f"SELECT category,source,message,COUNT(*) n FROM events WHERE {where} "
                     "GROUP BY fingerprint ORDER BY n DESC, MAX(ts) DESC LIMIT 10", (t0,)).fetchall()
    kern = kernel_report(scores, kernel_boots(db))[-3:]
    one = lambda s: " ".join((s or "").split())[:100]
    h = (lambda t: f"\n## {t}\n") if md else (lambda t: f"\n{t}:")
    out = [(f"# Reliability report: {ds[0]:%b %d} - {ds[-1]:%b %d, %Y}" if md else
            f"Reliability report: {ds[0]:%b %d} - {ds[-1]:%b %d, %Y}"),
           "", f"Average score: {sum(got) / len(got):.1f}/10 ({len(got)} of {days} days with data)" if got
           else "Average score: no data (machine off or nothing recorded)", h("Daily scores")]
    if md:
        out += ["| Day | Score |", "|---|---|"] + [f"| {d:%a %b %d} | {'off' if v is None else f'{v:.1f}'} |" for d, v in zip(ds, vals)]
    else:
        out += [f"  {d:%a %b %d}  " + ("off" if v is None else f"{v:4.1f}  " + "#" * round(v) + "." * (10 - round(v)))
                for d, v in zip(ds, vals)]
    out.append(h("Events by category"))
    out += [(f"- {c}: {n} ({k} distinct)" if md else f"  {c:<8} {n:>5}  ({k} distinct)") for c, n, k in cats] or ["  none"]
    out.append(h("Top problems"))
    out += [(f"- x{n} **{s}** ({c}): `{one(m).replace(chr(96), chr(39))}`" if md else f"  x{n:<4} {s} [{c}] {one(m)}")
            for c, s, m, n in top] or ["  none"]
    if kern:
        out.append(h("Kernels (average daily score)"))
        out += [(f"- {k['kernel']}: {k['avg']:.1f}/10 over {k['days']} day(s)" if md else f"  {k['kernel']:<28} {k['avg']:.1f}/10  {k['days']}d")
                + ("" if k["delta"] is None else f" ({k['delta']:+.1f} vs previous)") + (" WORSE" if k["worse"] else "") for k in kern]
    return "\n".join(out) + "\n"
