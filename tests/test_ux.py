from datetime import date
from core import connect, category_counts, score_band, chart_header
import os, tempfile, time

def main():
    d = connect(os.path.join(tempfile.mkdtemp(), "t.db")); now = time.time()
    for i, (cat, fp) in enumerate([("crash", "a"), ("crash", "a"), ("crash", "b"), ("oom", "c")]):
        d.execute("INSERT INTO events(ts,category,severity,source,message,fingerprint,boot_id) VALUES(?,?,?,?,?,?,?)",
                  (now - i, cat, 3, "s", "m", fp, "b1"))
    d.commit()
    assert category_counts(d) == {"crash": 2, "oom": 1}
    d.execute("INSERT INTO muted VALUES('a','s','m',0)"); d.commit()
    assert category_counts(d) == {"crash": 1, "oom": 1}
    assert category_counts(d, now - 100) == {"crash": 1, "oom": 1}
    assert category_counts(d, now + 86400) == {}
    assert score_band(2)[0] == "Poor" and score_band(5)[0] == "Fair" and score_band(9)[0] == "Good" and score_band(None)[0] == "Off"
    days = [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)]
    t, sub = chart_header(days, {days[0]: 10.0, days[1]: 4.0, days[2]: None})
    assert "7.0" in t and "Nov 2" in sub, (t, sub)
    assert chart_header([], {})[1] == "No data yet"
    print("ok")
main()
