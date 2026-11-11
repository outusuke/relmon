# SPDX-License-Identifier: GPL-3.0-or-later
import os, tempfile
from types import SimpleNamespace as NS
import integration as I

ok = lambda *a, **k: NS(returncode=0, stdout="", stderr="")
home = tempfile.mkdtemp(); dest = I.app_dir(home)
exist = lambda: sorted(os.listdir(dest)) if os.path.isdir(dest) else None

# menu entry: copies the app, launcher points at the copy
assert I.set_menu_entry(True, home) is None
assert exist() == sorted(I.FILES)
exe = open(I._desktop_path(home)).read()
assert os.path.join(dest, "app.py") in exe and I.HERE not in exe.replace(dest, "")
assert open(os.path.join(dest, "core.py")).read() == open(os.path.join(I.HERE, "core.py")).read()
# background timer shares the copy
assert I.set_background(True, home, run=ok) is None
assert os.path.join(dest, "collector.py") in open(os.path.join(I._unit_dir(home), f"{I.UNIT}.service")).read()
# switching one off keeps the copy while the other uses it; the last one off removes it
assert I.set_menu_entry(False, home) is None and exist() == sorted(I.FILES)
assert I.set_background(False, home, run=ok) is None and exist() is None
# re-enable refreshes a stale copy; explicit script path (tests/dev) installs nothing
I.set_menu_entry(True, home); open(os.path.join(dest, "core.py"), "w").write("stale")
I.set_menu_entry(True, home); assert open(os.path.join(dest, "core.py")).read() != "stale"
I.set_menu_entry(False, home)
h2 = tempfile.mkdtemp(); I.set_menu_entry(True, h2, script="/x/app.py")
assert not os.path.exists(I.app_dir(h2)) and "/x/app.py" in open(I._desktop_path(h2)).read()
# never deletes the running copy
assert I.install_app(home) == dest
print("integration ok")
