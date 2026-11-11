# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop integration from inside the app: background timer (systemd --user) + app-menu entry.
Units/launcher are generated for the copy of the app that is running, so no install step is needed."""
import os, subprocess, sys

UNIT = "relmon-collector"
DESKTOP = "io.github.example.RelMon.desktop"
HERE = os.path.dirname(os.path.abspath(__file__))

TIMER = """[Unit]
Description=Run the Reliability Monitor collector periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
"""


def _q(s):
    """Quote a path for ExecStart= / Exec= (% is special in both)."""
    return '"' + s.replace("%", "%%").replace('"', '\\"') + '"'


def render_service(py, script):
    return (f"[Unit]\nDescription=Reliability Monitor collector\n\n[Service]\nType=oneshot\n"
            f"ExecStart={_q(py)} {_q(script)}\nNice=10\nIOSchedulingClass=idle\n")


def render_desktop(py, script):
    return ("[Desktop Entry]\nType=Application\nName=Reliability Monitor\n"
            "Comment=Timeline of crashes, kernel errors and failed services\n"
            f"Exec={_q(py)} {_q(script)}\nIcon=utilities-system-monitor\nTerminal=false\n"
            "Categories=System;Monitor;GTK;\nKeywords=crash;reliability;journal;errors;\nStartupNotify=true\n")


def _sctl(*args, run=subprocess.run):
    try:
        r = run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, (r.stderr or r.stdout or "").strip()


def background_enabled(run=subprocess.run):
    return _sctl("is-enabled", f"{UNIT}.timer", run=run)[0] == 0


def set_background(on, home=None, run=subprocess.run, py=None, script=None):
    """Enable/disable the collector timer. Returns None on success, else an error message."""
    d = os.path.join(home or os.path.expanduser("~"), ".config/systemd/user")
    timer = f"{UNIT}.timer"
    try:
        if on:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{UNIT}.service"), "w") as f:
                f.write(render_service(py or sys.executable, script or os.path.join(HERE, "collector.py")))
            with open(os.path.join(d, timer), "w") as f:
                f.write(TIMER)
            for args in (("daemon-reload",), ("enable", "--now", timer)):
                rc, err = _sctl(*args, run=run)
                if rc:
                    return err or "systemctl failed"
            return None
        rc, err = _sctl("disable", "--now", timer, run=run)
        if rc and background_enabled(run):   # "not found" while already off is fine
            return err or "systemctl failed"
        for n in (f"{UNIT}.service", timer):
            try: os.remove(os.path.join(d, n))
            except FileNotFoundError: pass
        _sctl("daemon-reload", run=run)
    except OSError as e:
        return str(e)
    return None


def _desktop_path(home):
    return os.path.join(home or os.path.expanduser("~"), ".local/share/applications", DESKTOP)


def menu_entry_enabled(home=None):
    return os.path.exists(_desktop_path(home))


def set_menu_entry(on, home=None, py=None, script=None):
    """Create/remove the app-menu launcher. Returns None on success, else an error message."""
    p = _desktop_path(home)
    try:
        if on:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(render_desktop(py or sys.executable, script or os.path.join(HERE, "app.py")))
        else:
            try: os.remove(p)
            except FileNotFoundError: pass
    except OSError as e:
        return str(e)
    return None
