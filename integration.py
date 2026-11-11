# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop integration from inside the app: background timer (systemd --user) + app-menu entry.
Units/launcher are generated for the copy of the app that is running, so no install step is needed."""
import os, shutil, subprocess, sys

UNIT = "relmon-collector"
DESKTOP = "io.github.example.RelMon.desktop"
HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ("app.py", "collector.py", "core.py", "integration.py")   # what the installed copy needs

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


def app_dir(home=None):
    return os.path.join(home or os.path.expanduser("~"), ".local/share/relmon/app")


def install_app(home=None):
    """Copy the app to app_dir() so the launcher and timer keep working if the download folder goes away.
    Returns the directory (HERE itself if we already run from there)."""
    dest = app_dir(home)
    if os.path.realpath(dest) == os.path.realpath(HERE):
        return dest
    os.makedirs(dest, exist_ok=True)
    for n in FILES:
        shutil.copy2(os.path.join(HERE, n), os.path.join(dest, n + ".tmp"))
        os.replace(os.path.join(dest, n + ".tmp"), os.path.join(dest, n))   # never a half-written file
    return dest


def _unit_dir(home):
    return os.path.join(home or os.path.expanduser("~"), ".config/systemd/user")


def _cleanup(home):
    """Remove the installed copy once neither the launcher nor the timer uses it."""
    dest = app_dir(home)
    if (menu_entry_enabled(home) or os.path.exists(os.path.join(_unit_dir(home), f"{UNIT}.timer"))
            or os.path.realpath(dest) == os.path.realpath(HERE)):
        return
    shutil.rmtree(dest, ignore_errors=True)


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
    d = _unit_dir(home)
    timer = f"{UNIT}.timer"
    try:
        if on:
            os.makedirs(d, exist_ok=True)
            script = script or os.path.join(install_app(home), "collector.py")
            with open(os.path.join(d, f"{UNIT}.service"), "w") as f:
                f.write(render_service(py or sys.executable, script))
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
        _cleanup(home)
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
                f.write(render_desktop(py or sys.executable, script or os.path.join(install_app(home), "app.py")))
        else:
            try: os.remove(p)
            except FileNotFoundError: pass
            _cleanup(home)
    except OSError as e:
        return str(e)
    return None
