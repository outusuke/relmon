# SPDX-License-Identifier: GPL-3.0-or-later
"""Reliability Monitor UI. Needs: PyGObject, GTK4, libadwaita (python3-gi, gir1.2-adw-1)."""
import sys
if "--report" in sys.argv:   # headless: never touches GTK
    import collector; sys.exit(collector.main_report(sys.argv))
import gi, threading
import collector, integration
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); gi.require_version("PangoCairo", "1.0")
from datetime import date, datetime
from gi.repository import Adw, Gtk, GLib, Gdk, PangoCairo
from core import (connect, daily_scores, score_inputs, mute, unmute, muted_rows, event_detail, event_markdown,
                  boot_label, bar_index, label_step, day_label, score_tooltip, kernel_report, kernel_boots, list_events, PAGE, app_trends, trend_line,
                  score_rgb, step_index, chart_summary)

LABEL_H = 16   # px reserved under the bars for day labels
WINDOWS = (10, 30, 120)   # seconds of journal either side of an event; index = log window dropdown

CATS = [("all", "All events"), ("panic", "Panics"), ("crash", "App crashes"),
        ("oom", "Out of memory"), ("disk", "Disk / filesystem"),
        ("gpu", "GPU"), ("thermal", "Thermal"), ("kernel", "Other kernel / hardware"), ("service", "Service failures"), ("unclean", "Unclean shutdowns"),
        ("change", "Changes"), ("error", "Other errors"), ("apps", "App trends"), ("kernels", "Kernels"),
        ("muted", "Muted")]


def named(widget, label):
    """Give an icon-only widget a name for screen readers."""
    widget.update_property([Gtk.AccessibleProperty.LABEL], [label])
    return widget


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Reliability Monitor", default_width=1000, default_height=650)
        self.db, self.cat, self.day, self.days = connect(), "all", None, []
        self.limit, self.text = PAGE, ""
        # sidebar
        side = Gtk.ListBox(css_classes=["navigation-sidebar"])
        for key, label in CATS:
            r = Gtk.ListBoxRow(); r.key = key
            r.set_child(Gtk.Label(label=label, xalign=0, margin_top=10, margin_bottom=10, margin_start=8))
            side.append(r)
        side.connect("row-selected", lambda _, r: r and self.set_cat(r.key))
        side_page = Adw.NavigationPage(title="Categories", child=Adw.ToolbarView(content=side))
        side_page.get_child().add_top_bar(Adw.HeaderBar())
        # content: score chart + event list
        self.chart = Gtk.DrawingArea(content_height=120 + LABEL_H, margin_start=12, margin_end=12, margin_top=8,
                                     has_tooltip=True, focusable=True)
        self.kb = None   # keyboard-focused bar
        self.chart.set_draw_func(self.draw)
        keys = Gtk.EventControllerKey(); keys.connect("key-pressed", self.on_key); self.chart.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda _: self.chart.queue_draw()); focus.connect("leave", lambda _: self.chart.queue_draw())
        self.chart.add_controller(focus)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.chart.queue_draw())
        self.chart.connect("query-tooltip", self.on_tip)
        click = Gtk.GestureClick(); click.connect("pressed", self.on_click); self.chart.add_controller(click)
        self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                margin_start=12, margin_end=12, margin_bottom=12, margin_top=8)
        self.empty = Adw.StatusPage(icon_name="emblem-ok-symbolic", title="All healthy",
                                    description="No events for this selection.")
        self.stack = Gtk.Stack(); self.stack.add_named(self.list, "list"); self.stack.add_named(self.empty, "empty")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # detail pane under the list, hidden until a row is clicked
        self.sel = None
        self.meta = Gtk.Label(xalign=0, selectable=True, wrap=True, margin_start=12, margin_end=40, margin_top=8)
        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                left_margin=12, top_margin=6, bottom_margin=6)
        buf, hl = self.log.get_buffer(), Gdk.RGBA()
        hl.parse("rgba(128,128,128,0.25)")
        buf.create_tag("err", foreground="#e0463a"); buf.create_tag("warn", foreground="#d99a1a")
        buf.create_tag("dim", foreground="#888888"); buf.create_tag("event", weight=700, background_rgba=hl)
        close = named(Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat"]), "Close details")
        close.connect("clicked", lambda _: self.detail.set_visible(False))
        self.copy_btn = named(Gtk.Button(icon_name="edit-copy-symbolic", css_classes=["flat"],
                                         tooltip_text="Copy for bug report (Markdown)", sensitive=False),
                              "Copy for bug report (Markdown)")
        self.copy_btn.connect("clicked", self.copy_report)
        btns = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START)
        btns.append(self.copy_btn); btns.append(close)
        top = Gtk.Overlay(child=self.meta); top.add_overlay(btns)
        self.cur = None   # (detail dict, ts, journal lines or None) of the open event
        self.detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        fbar = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=4, margin_bottom=4)
        self.f_err = Gtk.ToggleButton(label="Warnings and errors", tooltip_text="Hide info and debug lines")
        self.f_unit = Gtk.ToggleButton(label="Related lines", tooltip_text="Only lines that mention the event's service or process")
        self.f_wrap = Gtk.ToggleButton(label="Wrap lines")
        self.log_info = Gtk.Label(css_classes=["dim-label"], hexpand=True, xalign=1)
        self.f_win = named(Gtk.DropDown(model=Gtk.StringList.new(["±10 s", "±30 s", "±2 min"]), selected=1,
                                        tooltip_text="Journal time window around the event"), "Journal time window")
        self.f_win.connect("notify::selected", lambda *_: self.cur and self.load_log())
        self.copy_cmd = named(Gtk.Button(icon_name="utilities-terminal-symbolic", css_classes=["flat"],
                                         tooltip_text="Copy journalctl command"), "Copy journalctl command")
        self.copy_cmd.connect("clicked", self.copy_command)
        fbar.append(self.f_win)
        for b in (self.f_err, self.f_unit, self.f_wrap):
            b.connect("toggled", lambda _: self.render_log()); fbar.append(b)
        fbar.append(self.log_info); fbar.append(self.copy_cmd)
        self.detail.append(top)
        self.detail.append(fbar)
        self.detail.append(Gtk.ScrolledWindow(child=self.log, vexpand=True, min_content_height=160))
        paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True, resize_end_child=True,
                          shrink_end_child=False, start_child=Gtk.ScrolledWindow(child=self.stack),
                          end_child=self.detail)
        self.search = Gtk.SearchEntry(placeholder_text="Filter by source or message", margin_start=12, margin_end=12,
                                      margin_top=8)
        self.search.connect("search-changed", self.on_search)
        box.append(self.chart); box.append(self.search); box.append(paned)
        refresh = named(Gtk.Button(icon_name="view-refresh-symbolic"), "Refresh"); refresh.connect("clicked", lambda _: self.refresh())
        bar = Adw.HeaderBar(); bar.pack_end(self.setup_menu()); bar.pack_end(refresh)
        view = Adw.ToolbarView(content=box); view.add_top_bar(bar)
        split = Adw.NavigationSplitView(sidebar=side_page, content=Adw.NavigationPage(title="Timeline", child=view))
        self.set_content(split)
        side.select_row(side.get_row_at_index(0))
        self.refresh()
        GLib.timeout_add_seconds(60, self.tick)

    def tick(self):
        """Redraw when the date rolls over while the window stays open."""
        if date.today() != self.today: self.refresh()
        return True

    def refresh(self):
        """Run the collector in the background, then redraw."""
        def work():
            d = connect(); collector.run_once(d); d.close()
            GLib.idle_add(self.reload)
        threading.Thread(target=work, daemon=True).start()

    def set_cat(self, key):
        self.cat, self.limit = key, PAGE; self.fill()

    def on_search(self, entry):
        self.text, self.limit = entry.get_text().strip(), PAGE; self.fill()

    def more(self):
        self.limit += PAGE; self.fill()

    def reload(self):
        self.today = date.today()
        rows, boots = score_inputs(self.db)   # muted fingerprints already excluded
        self.scores = daily_scores(rows, boots)
        self.days = sorted(self.scores)[-30:]
        self.kb = None if self.kb is None or self.kb >= len(self.days) else self.kb
        named(self.chart, chart_summary(self.days, self.scores))
        self.chart.queue_draw(); self.fill()

    def draw(self, area, cr, w, h):
        n = max(len(self.days), 1); bw = w / n; ph = h - LABEL_H   # ph = plot height above the labels
        dark = Adw.StyleManager.get_default().get_dark()
        for i, d in enumerate(self.days):
            s = self.scores[d]
            if s is None:  # machine was off: thin grey stub, not a "perfect" bar
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.3); cr.rectangle(i * bw + 1, ph - 4, bw - 2, 4); cr.fill()
                continue
            r, g, b = score_rgb(s, dark)
            cr.set_source_rgba(r, g, b, 1.0 if d == self.day else 0.8)
            bh = ph * s / 10
            cr.rectangle(i * bw + 1, ph - bh, bw - 2, bh); cr.fill()
        c = area.get_color() if hasattr(area, "get_color") else area.get_style_context().get_color()
        for i, d in enumerate(self.days):   # non-colour cues: solid frame = selected day, dashed = keyboard focus
            if d == self.day or (i == self.kb and area.has_focus()):
                cr.set_source_rgba(c.red, c.green, c.blue, 0.9); cr.set_line_width(2 if d == self.day else 1.5)
                cr.set_dash([] if d == self.day else [3, 2])
                cr.rectangle(i * bw + 1.5, 1, bw - 3, ph - 2); cr.stroke(); cr.set_dash([])
        step = label_step(bw)
        for i, d in enumerate(self.days):
            if (len(self.days) - 1 - i) % step: continue   # always label the last day
            lay = area.create_pango_layout("")
            lay.set_markup(f'<span size="small">{day_label(d)}</span>')
            cr.set_source_rgba(c.red, c.green, c.blue, 0.9 if d == self.day else 0.6)
            cr.move_to(i * bw + (bw - lay.get_pixel_size()[0]) / 2, ph + 2)
            PangoCairo.show_layout(cr, lay)

    def on_tip(self, w, x, y, kb, tip):
        i = bar_index(x, w.get_width(), len(self.days))
        if i is None: return False
        d = self.days[i]
        tip.set_text(score_tooltip(d, self.scores[d]))
        return True

    def on_click(self, g, n, x, y):
        i = bar_index(x, self.chart.get_width(), len(self.days))
        if i is None: return
        self.chart.grab_focus(); self.kb = i; self.toggle_day(self.days[i])

    def toggle_day(self, d):
        self.day, self.limit = (None if d == self.day else d), PAGE
        self.chart.queue_draw(); self.fill()

    def on_key(self, ctrl, key, code, state):
        """Left/Right/Home/End move over the days, Enter/Space select the day, Escape clears the filter."""
        n = len(self.days)
        move = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1}.get(key)
        if move is not None or key in (Gdk.KEY_Home, Gdk.KEY_End):
            self.kb = 0 if key == Gdk.KEY_Home and n else step_index(self.kb, move, n) if move else step_index(None, 0, n)
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space) and self.kb is not None and self.kb < n:
            self.toggle_day(self.days[self.kb])
        elif key == Gdk.KEY_Escape and self.day:
            self.toggle_day(self.day)
        else:
            return False
        if self.kb is not None and self.kb < n:
            d = self.days[self.kb]
            named(self.chart, score_tooltip(d, self.scores[d]) + (", selected" if d == self.day else ""))
        self.chart.queue_draw()
        return True

    def fill(self):
        if not hasattr(self, "scores"): return self.reload()
        self.search.set_sensitive(self.cat not in ("muted", "kernels", "apps"))
        if self.cat == "muted": return self.fill_muted()
        if self.cat == "kernels": return self.fill_kernels()
        if self.cat == "apps": return self.fill_apps()
        t0 = datetime.combine(self.day, datetime.min.time()).timestamp() if self.day else None
        rows, more = list_events(self.db, self.cat, t0, self.text, self.limit)
        while (c := self.list.get_first_child()): self.list.remove(c)
        for ts, cat, src, msg, count, fp in rows:
            row = Adw.ActionRow(title=GLib.markup_escape_text(src or "?"), subtitle=GLib.markup_escape_text((msg or "")[:200]),
                                subtitle_lines=2, activatable=True)
            row.connect("activated", lambda _, f=fp, t=ts: self.show_detail(f, t))
            row.add_suffix(Gtk.Label(label=f"{datetime.fromtimestamp(ts):%b %d %H:%M}" + (f"  ×{count}" if count > 1 else ""),
                                     css_classes=["dim-label"]))
            row.add_suffix(self.row_button("Hide this", lambda _, f=fp, s=src, m=msg: (mute(self.db, f, s, m), self.reload())))
            self.list.append(row)
        if more:
            b = Gtk.Button(label=f"Show more ({len(rows)} shown)", css_classes=["flat"])
            b.connect("clicked", lambda _: self.more())
            self.list.append(b)
        if self.day or self.cat != "all" or self.text:
            self.empty.set_title("No matching events")
            self.empty.set_description(dict(CATS)[self.cat] + (f" on {self.day:%b %d}" if self.day else "")
                                       + (f' matching "{self.text}"' if self.text else "")
                                       + (". Click the selected day again to clear the day filter." if self.day else "."))
        else:
            self.empty.set_title("All healthy"); self.empty.set_description("No events recorded.")
        self.stack.set_visible_child_name("list" if rows else "empty")

    def show_detail(self, fp, ts):
        d = event_detail(self.db, fp, ts)
        if not d: return
        boot = boot_label(d)
        self.cur = (d, ts, None); self.copy_btn.set_sensitive(False)
        can = any(collector.related_terms(d)); self.f_unit.set_sensitive(can)
        if not can: self.f_unit.set_active(False)
        self.meta.set_text(f"{d['source']}   {datetime.fromtimestamp(ts):%Y-%m-%d %H:%M:%S}\n"
                           f"{(d['message'] or '')[:500]}\n"
                           f"Unit: {d['unit'] or '-'}    PID: {d['pid'] or '-'}    Boot: {boot}")
        self.detail.set_visible(True)
        self.sel = (fp, ts); self.load_log()

    def load_log(self):
        """(Re)load the open event's journal lines for the selected time window."""
        d, ts = self.cur[0], self.cur[1]
        win = WINDOWS[self.f_win.get_selected()]
        self.cur = (d, ts, None, win); self.copy_btn.set_sensitive(False)
        self.log.get_buffer().set_text("Loading journal lines..."); self.log_info.set_text("")
        self.sel = key = (self.sel[0], ts, win)
        def work():
            lines, err = collector.context_lines(ts, d["boot_id"], win)
            GLib.idle_add(self.show_log, key, lines, err)
        threading.Thread(target=work, daemon=True).start()

    def show_log(self, key, lines, err):
        if key != self.sel: return
        self.cur = (self.cur[0], self.cur[1], lines, self.cur[3]); self.copy_btn.set_sensitive(True)
        if err: self.log.get_buffer().set_text(err); self.log_info.set_text(""); return
        self.render_log()

    def render_log(self):
        """Draw the open event's journal lines through the filter toggles."""
        if not self.cur or self.cur[2] is None: return
        d, lines = self.cur[0], self.cur[2]
        same = self.f_unit.get_active()
        terms, pid = collector.related_terms(d) if same else ((), None)
        kept = collector.filter_lines(lines, self.f_err.get_active(), terms, pid)
        self.log_info.set_text(f"{len(kept)} of {len(lines)} lines")
        self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if self.f_wrap.get_active() else Gtk.WrapMode.NONE)
        buf = self.log.get_buffer(); buf.set_text(""); mark = None
        for l in collector.collapse(kept):
            start = buf.get_end_iter().get_offset()
            buf.insert(buf.get_end_iter(), collector.display_line(l) + "\n")
            tags = ["err" if l.prio <= 3 else "warn" if l.prio == 4 else "dim" if l.prio >= 7 else None,
                    "event" if l.hit else None]
            for tag in filter(None, tags):
                buf.apply_tag_by_name(tag, buf.get_iter_at_offset(start), buf.get_end_iter())
            if l.hit and mark is None: mark = buf.create_mark(None, buf.get_iter_at_offset(start), True)
        if mark:  # scroll to the event once the text has been laid out
            GLib.idle_add(lambda: self.log.scroll_to_mark(mark, 0.0, True, 0.0, 0.3) and False)

    def setup_menu(self):
        """Header-bar popover with switches for background collection and the app-menu entry."""
        self._syncing = False
        lb = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE, width_request=360,
                         margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        for attr, title, desc, fn in (
                ("sw_bg", "Collect in background", "Checks the journal every 15 minutes, even when this window is closed",
                 integration.set_background),
                ("sw_menu", "Add to app menu", "Copies the app to ~/.local/share/relmon/app and adds a launcher", integration.set_menu_entry)):
            row = Adw.SwitchRow(title=title, subtitle=desc); row.desc = desc
            row.connect("notify::active", self.on_setup, fn)
            setattr(self, attr, row); lb.append(row)
        pop = Gtk.Popover(child=lb); pop.connect("show", self.sync_setup)
        return named(Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Setup", popover=pop), "Setup")

    def sync_setup(self, *_):
        self._syncing = True
        self.sw_bg.set_active(integration.background_enabled())
        self.sw_menu.set_active(integration.menu_entry_enabled())
        self._syncing = False

    def on_setup(self, row, _pspec, fn):
        if self._syncing: return
        err = fn(row.get_active())
        if err:   # revert the switch and show why
            self._syncing = True; row.set_active(not row.get_active()); self._syncing = False
        row.set_subtitle(f"Failed: {err[:150]}" if err else row.desc)

    def copy_report(self, btn):
        if not self.cur: return
        Gdk.Display.get_default().get_clipboard().set(event_markdown(*self.cur))
        btn.set_icon_name("object-select-symbolic")
        GLib.timeout_add(1500, lambda: btn.set_icon_name("edit-copy-symbolic") and False)

    def copy_command(self, btn):
        if not self.cur: return
        d, ts, _, win = self.cur
        Gdk.Display.get_default().get_clipboard().set(
            collector.journalctl_command(ts, d["boot_id"], win, self.f_err.get_active()))
        btn.set_icon_name("object-select-symbolic")
        GLib.timeout_add(1500, lambda: btn.set_icon_name("utilities-terminal-symbolic") and False)

    @staticmethod
    def row_button(tip, cb):
        b = Gtk.Button(icon_name="action-unavailable-symbolic" if tip == "Hide this" else "edit-undo-symbolic",
                       tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=["flat"])
        b.connect("clicked", cb)
        return named(b, tip)

    def fill_apps(self):
        """Crashes per app, last 30 days vs the 30 before; red when rising."""
        while (c := self.list.get_first_child()): self.list.remove(c)
        rows = app_trends(self.db)
        for t in rows:
            sub, suffix, worse = trend_line(t)
            row = Adw.ActionRow(title=GLib.markup_escape_text(t["source"]), subtitle=sub)
            if suffix: row.add_suffix(Gtk.Label(label=suffix, css_classes=["error" if worse else "dim-label"]))
            self.list.append(row)
        self.empty.set_title("No app crashes"); self.empty.set_description("Nothing crashed in the last 30 days.")
        self.stack.set_visible_child_name("list" if rows else "empty")

    def fill_kernels(self):
        """Average score per kernel, newest first; red when clearly worse than the previous kernel."""
        while (c := self.list.get_first_child()): self.list.remove(c)
        rows = kernel_report(self.scores, kernel_boots(self.db))[::-1]
        for r in rows:
            row = Adw.ActionRow(title=GLib.markup_escape_text(r["kernel"]),
                                subtitle=f"{r['days']} scored day(s), first {r['first']:%b %d}")
            d = r["delta"]
            row.add_suffix(Gtk.Label(label=f"{r['avg']:.1f}/10" + ("" if d is None else f"  ({d:+.1f} vs previous)"),
                                     css_classes=["error" if r["worse"] else "dim-label"]))
            self.list.append(row)
        self.empty.set_title("No kernel data yet")
        self.empty.set_description("Needs at least one recorded boot with a known kernel.")
        self.stack.set_visible_child_name("list" if rows else "empty")

    def fill_muted(self):
        while (c := self.list.get_first_child()): self.list.remove(c)
        rows = muted_rows(self.db)
        for fp, src, msg, _ in rows:
            row = Adw.ActionRow(title=GLib.markup_escape_text(src or "?"), subtitle=GLib.markup_escape_text(msg or ""),
                                subtitle_lines=2)
            row.add_suffix(self.row_button("Unmute", lambda _, f=fp: (unmute(self.db, f), self.reload())))
            self.list.append(row)
        self.empty.set_title("Nothing muted"); self.empty.set_description("Use the hide button on an event to mute it.")
        self.stack.set_visible_child_name("list" if rows else "empty")


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.example.RelMon")
        self.connect("activate", lambda a: Window(a).present())


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
