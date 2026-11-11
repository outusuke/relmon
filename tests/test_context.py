# SPDX-License-Identifier: GPL-3.0-or-later
from collector import window_slice

items = [(float(i), 6, f"l{i}", False) for i in range(1000)]
assert window_slice(items[:50], 25.0) == items[:50]                       # small input untouched
r = window_slice(items, 500.0)
assert len(r) == 300 and 500.0 in [x[0] for x in r] and r[0][0] == 350.0  # centred on the event
r = window_slice(items, 3.0);   assert len(r) == 300 and r[0][0] == 0.0 and 3.0 in [x[0] for x in r]    # start edge
r = window_slice(items, 998.0); assert len(r) == 300 and r[-1][0] == 999.0 and 998.0 in [x[0] for x in r]  # end edge
r = window_slice(items, 500.3); assert 500.0 in [x[0] for x in r]          # inexact ts -> nearest line
print("context ok")
