"""GTK window: pick apps, theme, wallpaper and clipboard options, then Apply."""
import math
import os
import re
import shutil
import sys
import threading
import types

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from . import core, kit  # noqa: E402

NONE, CUSTOM_CMD = "", "__custom__"


def to_hex(rgba):
    return "#%02x%02x%02x" % (round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255))


def to_rgba(hex_colour):
    c = Gdk.RGBA()
    c.parse(hex_colour)
    return c


class Ricer(Gtk.Window):
    def __init__(self):
        super().__init__(title="lazy-kali-ricer")
        self.set_default_size(760, 640)
        self.set_border_width(12)
        self.s = core.load()
        self.loading = True  # widgets are being filled: do not treat it as the user editing

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(outer)
        book = Gtk.Notebook()
        outer.pack_start(book, True, True, 0)
        for title, build in (("Apps", self.page_apps), ("Displays", self.page_displays), ("Shortcuts", self.page_shortcuts),
                             ("Get apps", self.page_get_apps), ("Browsers & tools", self.page_kit),
                             ("Theme", self.page_theme), ("Wallpaper", self.page_wallpaper),
                             ("Clipboard", self.page_clipboard), ("Update & debug", self.page_debug),
                             ("Uninstall", self.page_uninstall)):
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, border_width=14)
            build(page)
            book.append_page(page, Gtk.Label(label=title))

        bottom = Gtk.Box(spacing=10)
        self.status = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.status.set_text("Detected: %s.\nPick what you want, then Apply. Existing configs are backed up first."
                             % core.hardware())
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.get_style_context().add_class("suggested-action")
        apply_btn.connect("clicked", self.on_apply)
        bottom.pack_start(self.status, True, True, 0)
        bottom.pack_end(apply_btn, False, False, 0)
        outer.pack_start(bottom, False, False, 0)
        book.connect("switch-page", lambda *_: (self.list_uninstall(), self.list_binds()))  # both change after an Apply
        self.loading = False

    # ----- small builders -----
    def grid(self, page):
        g = Gtk.Grid(column_spacing=14, row_spacing=10)
        page.pack_start(g, False, False, 0)
        return g

    def row(self, grid, n, text, widget, hint=""):
        grid.attach(Gtk.Label(label=text, xalign=0), 0, n, 1, 1)
        grid.attach(widget, 1, n, 1, 1)
        if hint:
            dim = Gtk.Label(label=hint, xalign=0)
            dim.get_style_context().add_class("dim-label")
            grid.attach(dim, 2, n, 1, 1)

    def note(self, page, text):
        lbl = Gtk.Label(label=text, xalign=0, wrap=True)
        lbl.get_style_context().add_class("dim-label")
        page.pack_start(lbl, False, False, 0)

    def head(self, page, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("heading")
        page.pack_start(lbl, False, False, 0)

    def background(self, button, work, done):
        """Run work() off the GUI thread (it may wait for a password), then done(result) back on it."""
        button.set_sensitive(False)

        def finish(result):
            button.set_sensitive(True)
            done(result)

        threading.Thread(target=lambda: GLib.idle_add(finish, work()), daemon=True).start()

    def check(self, page, text, key):
        box = Gtk.CheckButton(label=text, active=bool(self.s[key]))
        box.connect("toggled", lambda w: self.s.__setitem__(key, w.get_active()))
        page.pack_start(box, False, False, 0)
        return box

    # ----- Apps -----
    def page_apps(self, page):
        self.note(page, "Programs found on this system. Each one gets the key shown on the right. "
                        "Choose \"Custom command\" to type your own, or \"None\" to leave the key unbound.")
        g = self.grid(page)
        self.app_rows = {}  # role -> (combo, entry)
        for n, (role, (label, keys, _cats)) in enumerate(core.ROLES.items()):
            combo = Gtk.ComboBoxText(hexpand=True)
            entry = Gtk.Entry(placeholder_text="command to run", width_chars=24)
            self.app_rows[role] = (combo, entry)

            def changed(_w, role=role, combo=combo, entry=entry):
                if self.loading:
                    return
                custom = combo.get_active_id() == CUSTOM_CMD
                entry.set_sensitive(custom)
                if not custom:
                    entry.set_text(combo.get_active_id())
                self.s["apps"][role] = entry.get_text().strip()

            combo.connect("changed", changed)
            entry.connect("changed", changed)
            box = Gtk.Box(spacing=8, homogeneous=True)
            box.pack_start(combo, True, True, 0)
            box.pack_start(entry, True, True, 0)
            self.row(g, n, label, box, keys)
        self.known = None  # commands seen by the last scan
        self.fill_apps()
        again = Gtk.Button(label="Rediscover installed programs", halign=Gtk.Align.START)
        again.connect("clicked", lambda *_: self.fill_apps())
        page.pack_start(again, False, False, 0)

        n = len(core.ROLES)
        shot = Gtk.ComboBoxText()
        for tool, text in (("flameshot", "Flameshot (region editor)"), ("grim", "grim + slurp (plain region)")):
            binary = "flameshot" if tool == "flameshot" else "grim"
            shot.append(tool, text + ("" if shutil.which(binary) else "  — not installed"))
        shot.set_active_id(self.s["apps"]["screenshot"])
        shot.connect("changed", lambda w: self.s["apps"].__setitem__("screenshot", w.get_active_id()))
        self.row(g, n, "Screenshots", shot, "Print, Super+Shift+S")

        kb = Gtk.Entry(text=self.s["kb_layout"], width_chars=10)
        kb.connect("changed", lambda w: self.s.__setitem__("kb_layout", w.get_text().strip() or "us"))
        self.row(g, n + 1, "Keyboard layout", kb, "XKB code: us, gb, de, us,ru …")
        self.check(page, "Show the shortcuts list every time I log in (Super+K opens it any time)", "keys_on_login")

    def fill_apps(self):
        """Scan for installed programs and refill the lists, keeping each current choice."""
        found = core.detect_apps()
        was, self.loading = self.loading, True
        for role, (combo, entry) in self.app_rows.items():
            combo.remove_all()
            for name, cmd in found[role]:
                combo.append(cmd, name)
            combo.append(NONE, "None")
            combo.append(CUSTOM_CMD, "Custom command…")
            current = self.s["apps"].get(role, "")
            if not combo.set_active_id(current):
                combo.set_active_id(CUSTOM_CMD)
            entry.set_text(current)
            entry.set_sensitive(combo.get_active_id() == CUSTOM_CMD)
        self.loading = was
        names = {cmd: name for apps in found.values() for name, cmd in apps}
        if self.known is not None:
            new = sorted(names[c] for c in set(names) - self.known)
            self.status.set_text("Found %d programs. New: %s." % (len(names), ", ".join(new) if new else "none"))
        self.known = set(names)

    # ----- Displays -----
    def page_displays(self, page):
        self.note(page, "Drag the screens to match how they sit on your desk; they snap to each other. "
                        "Identify shows each screen's number on the screen itself. Apply saves the layout.")
        self.screens = core.live_monitors()
        self.picked = self.drag = None  # index of the selected screen; drag = (index, pointer offset)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(-1, 300)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK)
        self.canvas.connect("draw", self.draw_screens)
        self.canvas.connect("button-press-event", self.on_screen_press)
        self.canvas.connect("motion-notify-event", self.on_screen_drag)
        self.canvas.connect("button-release-event", self.on_screen_drop)
        page.pack_start(self.canvas, True, True, 0)

        bar = Gtk.Box(spacing=8)
        identify = Gtk.Button(label="Identify screens")
        identify.connect("clicked", self.on_identify)
        self.mode = Gtk.ComboBoxText()
        self.mode.connect("changed", self.on_mode)
        auto = Gtk.Button(label="Back to automatic")
        auto.connect("clicked", self.on_auto_layout)
        bar.pack_start(identify, False, False, 0)
        bar.pack_start(Gtk.Label(label="Mode of selected screen"), False, False, 0)
        bar.pack_start(self.mode, False, False, 0)
        bar.pack_end(auto, False, False, 0)
        page.pack_start(bar, False, False, 0)
        if not self.screens:
            self.note(page, "No screens found: open this window inside a Hyprland session.")

    def view(self):
        """(factor, x offset, y offset) that fits every screen into the canvas."""
        W, H = self.canvas.get_allocated_width(), self.canvas.get_allocated_height()
        x0, y0 = min(m["x"] for m in self.screens), min(m["y"] for m in self.screens)
        x1 = max(m["x"] + m["w"] for m in self.screens)
        y1 = max(m["y"] + m["h"] for m in self.screens)
        f = min((W - 40) / max(1, x1 - x0), (H - 40) / max(1, y1 - y0))
        return f, (W - (x1 - x0) * f) / 2 - x0 * f, (H - (y1 - y0) * f) / 2 - y0 * f

    def draw_screens(self, _area, cr):
        if not self.screens:
            return
        f, ox, oy = self.frozen if self.drag else self.view()
        rgb = lambda h: tuple(v / 255 for v in core._rgb(h))
        for n, m in enumerate(self.screens):
            x, y, w, h = m["x"] * f + ox, m["y"] * f + oy, m["w"] * f, m["h"] * f
            cr.set_source_rgb(0.13, 0.15, 0.18)
            cr.rectangle(x, y, w, h)
            cr.fill()
            cr.set_source_rgb(*rgb(self.s["primary"] if n == self.picked else self.s["inactive"]))
            cr.set_line_width(3 if n == self.picked else 2)
            cr.rectangle(x + 1.5, y + 1.5, w - 3, h - 3)
            cr.stroke()
            cr.save()
            cr.rectangle(x, y, w - 6, h)  # long labels stay inside their screen
            cr.clip()
            cr.set_source_rgb(0.9, 0.93, 0.91)
            cr.set_font_size(max(14, h / 3))
            cr.move_to(x + 12, y + max(14, h / 3) + 6)
            cr.show_text(str(n + 1))
            cr.set_font_size(12)
            cr.move_to(x + 12, y + h - 26)
            cr.show_text(m["name"])
            cr.move_to(x + 12, y + h - 10)
            cr.show_text("%s  at %d,%d" % (m["mode"], m["x"], m["y"]))
            cr.restore()

    def on_screen_press(self, _area, event):
        f, ox, oy = self.frozen = self.view()  # keep the zoom steady while dragging
        px, py = (event.x - ox) / f, (event.y - oy) / f
        for n in reversed(range(len(self.screens))):
            m = self.screens[n]
            if m["x"] <= px <= m["x"] + m["w"] and m["y"] <= py <= m["y"] + m["h"]:
                self.picked, self.drag = n, (n, px - m["x"], py - m["y"])
                self.loading = True
                self.mode.remove_all()
                for mode in m["modes"] or [m["mode"]]:
                    self.mode.append(mode, mode)
                if not self.mode.set_active_id(m["mode"]):
                    self.mode.prepend(m["mode"], m["mode"])
                    self.mode.set_active_id(m["mode"])
                self.loading = False
                break
        self.canvas.queue_draw()

    def on_screen_drag(self, _area, event):
        if self.drag:
            n, dx, dy = self.drag
            f, ox, oy = self.frozen
            self.screens[n]["x"], self.screens[n]["y"] = round((event.x - ox) / f - dx), round((event.y - oy) / f - dy)
            self.canvas.queue_draw()

    def on_screen_drop(self, _area, _event):
        if not self.drag:
            return
        n, self.drag = self.drag[0], None
        me, others = self.screens[n], [m for i, m in enumerate(self.screens) if i != n]
        if others:
            # snap each axis to the nearest edge of another screen: beside it, or flush with it
            xs = [x for o in others for x in (o["x"] - me["w"], o["x"] + o["w"], o["x"], o["x"] + o["w"] - me["w"])]
            ys = [y for o in others for y in (o["y"] - me["h"], o["y"] + o["h"], o["y"], o["y"] + o["h"] - me["h"])]
            me["x"] = min(xs, key=lambda x: abs(x - me["x"]))
            me["y"] = min(ys, key=lambda y: abs(y - me["y"]))
            hit = lambda o: me["x"] < o["x"] + o["w"] and o["x"] < me["x"] + me["w"] and me["y"] < o["y"] + o["h"] and o["y"] < me["y"] + me["h"]
            if any(hit(o) for o in others):  # landed on top of one: put it at the far right instead
                me["x"] = max(o["x"] + o["w"] for o in others)
        self.keep_screens()

    def keep_screens(self):
        """Shift the layout so it starts at 0,0 and store it for Apply."""
        x0, y0 = min(m["x"] for m in self.screens), min(m["y"] for m in self.screens)
        for m in self.screens:
            m["x"], m["y"] = m["x"] - x0, m["y"] - y0
        self.s["monitors"] = [{k: m[k] for k in ("name", "mode", "x", "y", "scale", "transform")} for m in self.screens]
        self.canvas.queue_draw()

    def on_mode(self, combo):
        if self.loading or self.picked is None or not combo.get_active_id():
            return
        m = self.screens[self.picked]
        m["mode"] = combo.get_active_id()
        w, h = (int(v) for v in m["mode"].split("@")[0].split("x"))
        m["w"], m["h"] = (round(h / m["scale"]), round(w / m["scale"])) if m["transform"] % 2 else (round(w / m["scale"]), round(h / m["scale"]))
        self.keep_screens()

    def on_auto_layout(self, _button):
        self.s["monitors"] = []
        self.status.set_text("Saved layout cleared. After Apply, Hyprland places the screens itself.")

    def on_identify(self, _button):
        """Show each screen's number on that screen for a few seconds."""
        display = Gdk.Display.get_default()
        live = core.live_monitors()  # where the screens are now, to match GTK's screens by position
        for i in range(display.get_n_monitors()):
            g = display.get_monitor(i).get_geometry()
            match = [n for n, m in enumerate(live) if (m["x"], m["y"]) == (g.x, g.y)]
            if not match:
                continue
            m = live[match[0]]
            win = Gtk.Window(title="lazy-kali-ricer identify", decorated=False)
            text = Gtk.Label()
            text.set_markup("<span size='200000' weight='bold'>%d</span>\n<span size='30000'>%s   %s</span>"
                            % (match[0] + 1, GLib.markup_escape_text(m["name"]), m["mode"]))
            text.set_justify(Gtk.Justification.CENTER)
            win.add(text)
            win.fullscreen_on_monitor(Gdk.Screen.get_default(), i)
            win.show_all()
            GLib.timeout_add(2500, win.destroy)

    # ----- Shortcuts -----
    def page_shortcuts(self, page):
        self.note(page, "Your own keyboard shortcuts. Click a Keys field and press the combination, then type the "
                        "command it runs. They are added to the Super+K list. Combinations Hyprland already uses "
                        "never reach this window, so they cannot be recorded. Backspace clears a Keys field.")
        self.shortcut_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add(self.shortcut_rows)
        page.pack_start(scroll, True, True, 0)
        add = Gtk.Button(label="Add shortcut", halign=Gtk.Align.START)
        add.connect("clicked", lambda *_: self.add_shortcut())
        page.pack_start(add, False, False, 0)
        for sc in self.s["shortcuts"]:
            self.add_shortcut(sc)

        head = Gtk.Label(label="Shortcuts in use now", xalign=0)
        head.get_style_context().add_class("heading")
        page.pack_start(head, False, False, 0)
        self.note(page, "Everything Hyprland answers to at the moment, yours included after an Apply. "
                        "The built-in ones are fixed; pick different keys for your own.")
        self.binds = Gtk.Grid(column_spacing=24, row_spacing=4)
        listing = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        listing.add(self.binds)
        page.pack_start(listing, True, True, 0)
        self.list_binds()

    def list_binds(self):
        for child in self.binds.get_children():
            self.binds.remove(child)
        rows = core.current_binds() or [("", "Open this window inside a Hyprland session to see them.")]
        for n, (keys, what) in enumerate(rows):
            key = Gtk.Label(label=keys, xalign=0, selectable=True)
            key.get_style_context().add_class("dim-label")
            self.binds.attach(key, 0, n, 1, 1)
            self.binds.attach(Gtk.Label(label=what, xalign=0), 1, n, 1, 1)
        self.binds.show_all()

    def add_shortcut(self, sc=None):
        if sc is None:
            sc = {"keys": "", "cmd": "", "desc": ""}
            self.s["shortcuts"].append(sc)
        row = Gtk.Box(spacing=8)
        keys = Gtk.Entry(text=sc["keys"], placeholder_text="Keys: click, then press", width_chars=24, editable=False)
        keys.connect("key-press-event", self.record_keys, sc)
        row.pack_start(keys, False, False, 0)
        for field, hint in (("cmd", "Command to run"), ("desc", "What it does (optional)")):
            entry = Gtk.Entry(text=sc.get(field, ""), placeholder_text=hint)
            entry.connect("changed", lambda w, field=field: sc.__setitem__(field, w.get_text().strip()))
            row.pack_start(entry, True, True, 0)
        remove = Gtk.Button(label="Remove")
        remove.connect("clicked", lambda *_: (self.s["shortcuts"].remove(sc), self.shortcut_rows.remove(row)))
        row.pack_start(remove, False, False, 0)
        self.shortcut_rows.pack_start(row, False, False, 0)
        row.show_all()
        if not sc["keys"]:
            keys.grab_focus()

    def record_keys(self, entry, event, sc):
        """Turn the pressed combination into Hyprland's form, e.g. "SUPER + SHIFT + b"."""
        key = Gdk.keyval_name(Gdk.keyval_to_lower(event.keyval)) or ""
        if re.match(r"(Super|Control|Alt|Shift|Meta|Hyper|ISO_Level\d_Shift|Caps_Lock|Num_Lock)", key):
            return True  # a modifier on its own: wait for the real key
        state = event.state
        mods = [name for mask, name in ((Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK, "SUPER"),
                                        (Gdk.ModifierType.CONTROL_MASK, "CTRL"), (Gdk.ModifierType.MOD1_MASK, "ALT"),
                                        (Gdk.ModifierType.SHIFT_MASK, "SHIFT")) if state & mask]
        if not mods and key in ("Tab", "Escape"):
            return False  # let focus move on
        if not mods and key in ("BackSpace", "Delete"):
            combo = ""
        elif not mods and not re.match(r"(XF86|F\d+$|Print$|Pause$)", key):
            self.status.set_text("Hold Super, Ctrl or Alt with the key: a plain key would stop working for typing.")
            return True
        else:
            combo = " + ".join(mods + [key])
        entry.set_text(combo)
        sc["keys"] = combo
        return True

    # ----- Get apps -----
    def page_get_apps(self, outer):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroll.add(page)
        outer.pack_start(scroll, True, True, 0)
        self.note(page, "Common programs for each job. Tick the ones you want and install them; "
                        "they then show up in the Apps tab. Already installed ones are greyed out.")
        self.catalog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.pack_start(self.catalog, False, False, 0)
        self.get_button = Gtk.Button(label="Install ticked apps", halign=Gtk.Align.START)
        self.get_button.connect("clicked", self.on_get_apps)
        page.pack_start(self.get_button, False, False, 0)

        self.head(page, "Vencord on Discord")
        self.note(page, "Vencord adds plugins and themes to the official Discord app. Discord downloads itself the "
                        "first time it opens, so: install Discord above, open it once, close it, then add Vencord. "
                        "A Discord update can remove it; add it again. Client mods are against Discord's terms; "
                        "Vesktop above is the same thing as its own app.")
        row = Gtk.Box(spacing=8)
        self.vencord_buttons = {}
        for key, label in (("open", "Open Discord"), ("install", "Add Vencord"), ("uninstall", "Remove Vencord")):
            button = Gtk.Button(label=label)
            button.connect("clicked", self.on_vencord, key)
            self.vencord_buttons[key] = button
            row.pack_start(button, False, False, 0)
        self.vencord_note = Gtk.Label(xalign=0)
        row.pack_start(self.vencord_note, False, False, 0)
        page.pack_start(row, False, False, 0)
        self.list_catalog()

    def app_icon(self, pkg):
        theme = Gtk.IconTheme.get_default()
        for name in core.ICONS.get(pkg, "").split() + [pkg, "application-x-executable"]:
            if theme.has_icon(name):
                return Gtk.Image.new_from_icon_name(name, Gtk.IconSize.LARGE_TOOLBAR)
        return Gtk.Image()

    def list_catalog(self):
        for child in self.catalog.get_children():
            self.catalog.remove(child)
        state, self.wanted = core.catalog_state(), []  # (checkbox, package)
        groups = [(core.ROLES[role][0], common) for role, common in core.CATALOG.items()] + list(core.EXTRAS.items())
        for title, common in groups:
            head = Gtk.Label(label=title, xalign=0)
            head.get_style_context().add_class("heading")
            self.catalog.pack_start(head, False, False, 0)
            line = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6)
            for name, pkg in common:
                if state[pkg] == "missing":  # not in this system's package sources
                    continue
                box = Gtk.CheckButton()
                inner = Gtk.Box(spacing=6)
                inner.pack_start(self.app_icon(pkg), False, False, 0)
                inner.pack_start(Gtk.Label(label=name + ("  ✓" if state[pkg] == "installed" else "")), False, False, 0)
                box.add(inner)
                box.set_sensitive(state[pkg] == "available")
                self.wanted.append((box, pkg))
                line.add(box)
            self.catalog.pack_start(line, False, False, 0)
        self.catalog.show_all()
        self.fill_vencord()

    def fill_vencord(self):
        vencord = core.vencord_state()
        self.vencord_note.set_text({"": "Discord is not installed.", "unopened": "Open Discord once so it can download itself.",
                                    "plain": "Discord is ready for Vencord.", "patched": "Vencord is on Discord  ✓"}[vencord])
        for key, button in self.vencord_buttons.items():
            button.set_sensitive({"open": vencord != "", "install": vencord in ("plain", "patched"),
                                  "uninstall": vencord == "patched"}[key])

    def on_vencord(self, button, key):
        if key == "open":
            core._spawn("discord")
            self.status.set_text("Discord is starting. Let it finish updating, close it, then press Add Vencord.")
            GLib.timeout_add_seconds(20, lambda: self.fill_vencord())  # its files appear after the first update
            return
        if core.discord_running():
            self.status.set_text("Discord is open: close it (also from the tray), then press the button again.")
            return
        self.status.set_text("Working on Discord …")

        def done(ok):
            self.fill_vencord()
            self.status.set_text("Failed. Is Discord closed, and are you online?" if not ok else
                                 "Vencord added. Open Discord: its settings now have a Vencord section." if key == "install" else
                                 "Vencord removed from Discord.")

        self.background(button, lambda: core.vencord(key), done)

    def on_get_apps(self, _button):
        packages = [pkg for box, pkg in self.wanted if box.get_active()]
        if not packages:
            return
        web = [p for p in packages if p.startswith("web:")]
        packages = [p for p in packages if p not in web]
        failed = [core.WEBAPPS[p[4:]][0] for p in web if not core.add_webapp(p[4:])]
        if failed:
            self.status.set_text("%s needs Brave, Chromium or Chrome: install one above first." % ", ".join(failed))
        if not packages:
            self.list_catalog()
            self.fill_apps()
            return
        self.get_button.set_sensitive(False)
        self.status.set_text("Installing %s … (enter your password in the dialog)" % ", ".join(packages))

        def done(ok):
            self.get_button.set_sensitive(True)
            self.list_catalog()
            self.fill_apps()
            if not ok:
                self.status.set_text("Install cancelled or failed: %s" % ", ".join(packages))

        threading.Thread(target=lambda: GLib.idle_add(done, core.install_packages(packages)), daemon=True).start()

    # ----- Browsers & tools -----
    def page_kit(self, outer):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroll.add(page)
        outer.pack_start(scroll, True, True, 0)

        self.head(page, "Your kit")
        self.note(page, "One folder with your tool list (tools.txt), your scripts (tools/), bookmarks (bookmarks.json) "
                        "and extensions (extensions.txt). Keep it in a private GitHub repository: after a reinstall, "
                        "\"Get from GitHub\" brings it back.")
        row = Gtk.Box(spacing=8)
        self.kit_folder = Gtk.FileChooserButton(title="Choose your kit folder", action=Gtk.FileChooserAction.SELECT_FOLDER,
                                                hexpand=True)
        self.kit_folder.connect("file-set", lambda w: self.use_kit(w.get_filename()))
        sample = Gtk.Button(label="Add sample files")
        sample.connect("clicked", self.on_kit_sample)
        row.pack_start(self.kit_folder, True, True, 0)
        row.pack_start(sample, False, False, 0)
        page.pack_start(row, False, False, 0)
        self.note(page, "GitHub: the first time, your browser opens for you to sign in, and you are asked whether "
                        "the kit goes to a new private repository or one you already have.")
        row = Gtk.Box(spacing=8)
        for label, action in (("Get from GitHub", kit.download), ("Upload to GitHub", kit.upload)):
            button = Gtk.Button(label=label)
            button.connect("clicked", self.on_kit_git, action)
            row.pack_start(button, False, False, 0)
        page.pack_start(row, False, False, 0)
        row = Gtk.Box(spacing=12)
        row.pack_start(Gtk.Label(label="Build the kit from this system:", xalign=0), False, False, 0)
        self.build_parts = []  # (checkbox, key)
        for key, label, tip in (
                ("tools", "Tools", "Packages you installed by name, pipx tools, git repositories in ~/tools → tools.txt"),
                ("scripts", "Scripts", "Your own files and folders in ~/tools → the kit's tools/ folder "
                                       "(not git checkouts, not files over 5 MB)"),
                ("exts", "Extensions", "Extensions you added to the ticked browsers → extensions.txt"),
                ("marks", "Bookmarks", "Bookmarks of the first ticked browser → bookmarks.json (replaces the file)")):
            box = Gtk.CheckButton(label=label, active=True, tooltip_text=tip)
            self.build_parts.append((box, key))
            row.pack_start(box, False, False, 0)
        build = Gtk.Button(label="Build")
        build.get_style_context().add_class("suggested-action")
        build.connect("clicked", self.on_kit_build)
        row.pack_start(build, False, False, 0)
        page.pack_start(row, False, False, 0)
        self.code_box = None
        # kit.upload / kit.download run off the GUI thread and reach the person through these.
        self.kit_ui = types.SimpleNamespace(
            say=lambda text: self.from_thread(self.status.set_text, text),
            code=lambda code: self.from_thread(self.show_code, code),
            ask_repo=lambda allow_new: self.from_thread(self.ask_repo, allow_new),
            confirm=lambda text: self.from_thread(self.confirm, text))

        self.head(page, "Tools")
        self.tools_note = Gtk.Label(xalign=0, wrap=True)
        self.tools_note.get_style_context().add_class("dim-label")
        page.pack_start(self.tools_note, False, False, 0)
        self.tools_button = Gtk.Button(label="Install tools and scripts from the kit", halign=Gtk.Align.START)
        self.tools_button.connect("clicked", self.on_kit_tools)
        page.pack_start(self.tools_button, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.find_entry = Gtk.Entry(placeholder_text="Look for a tool by name, e.g. wireshark", hexpand=True)
        self.find_entry.connect("activate", self.on_kit_find)
        find = Gtk.Button(label="Search")
        find.connect("clicked", self.on_kit_find)
        self.find_add = Gtk.Button(label="Add ticked to tools.txt", sensitive=False)
        self.find_add.connect("clicked", self.on_kit_find_add)
        for widget, grow in ((self.find_entry, True), (find, False), (self.find_add, False)):
            row.pack_start(widget, grow, grow, 0)
        page.pack_start(row, False, False, 0)
        self.find_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.find_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=150,
                                              no_show_all=True)
        self.find_scroll.add(self.find_list)
        page.pack_start(self.find_scroll, False, False, 0)

        self.head(page, "Browsers")
        self.note(page, "Extensions and bookmarks go to the ticked browsers. Brave and Chromium are in the Get apps tab. "
                        "Firefox and Tor Browser are not covered here.")
        row, found = Gtk.Box(spacing=14), kit.installed_browsers()
        self.kit_browsers = []  # (checkbox, name)
        for name in kit.BROWSERS:
            box = Gtk.CheckButton(label=name if name in found else name + "  — not installed", active=name in found)
            box.set_sensitive(name in found)
            self.kit_browsers.append((box, name))
            row.pack_start(box, False, False, 0)
        page.pack_start(row, False, False, 0)

        self.head(page, "Extensions")
        self.note(page, "The browser installs the ticked ones itself the next time it starts. It then shows "
                        "\"Managed by your organisation\": that is this list. Untick everything and set again to "
                        "remove the list. You sign in to each extension yourself.")
        self.ext_flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4)
        page.pack_start(self.ext_flow, False, False, 0)
        self.ext_button = Gtk.Button(label="Set extensions in ticked browsers", halign=Gtk.Align.START)
        self.ext_button.connect("clicked", self.on_kit_extensions)
        page.pack_start(self.ext_button, False, False, 0)

        self.head(page, "Bookmarks")
        self.note(page, "Import replaces the bookmarks of the ticked browsers with the file (close the browser first). "
                        "The old ones stay next to it as Bookmarks.bak-<time>.")
        row = Gtk.Box(spacing=8)
        self.marks_file = Gtk.FileChooserButton(title="Choose a bookmarks file", hexpand=True)
        load = Gtk.Button(label="Import")
        load.connect("clicked", self.on_kit_import)
        for widget, grow in ((self.marks_file, True), (load, False)):
            row.pack_start(widget, grow, grow, 0)
        page.pack_start(row, False, False, 0)
        self.fill_kit()

    def ticked_browsers(self):
        return [name for box, name in self.kit_browsers if box.get_active()]

    def use_kit(self, folder):
        if folder:
            self.s["kit_dir"] = folder
            kit.set_dir(folder)
            self.fill_kit()

    def fill_kit(self):
        """Show what the kit folder holds: tool counts, its extensions, its bookmarks file."""
        folder = kit.kit_dir(self.s)
        short = folder.replace(core.HOME, "~")
        if os.path.isdir(folder):
            self.kit_folder.set_filename(folder)
        else:
            self.kit_folder.set_current_folder(core.HOME)
        tools = kit.read_tools(folder)
        if os.path.isfile(os.path.join(folder, "tools.txt")):
            self.tools_note.set_text("%s/tools.txt: packages %d, pipx tools %d, git repositories %d (cloned into ~/tools). "
                                     "%s/tools: %d of your own files, put back in ~/tools. What is already there is skipped."
                                     % (short, len(tools["apt"]), len(tools["pipx"]), len(tools["git"]), short,
                                        kit.count_scripts(folder)))
        else:
            self.tools_note.set_text("No tools.txt in %s yet: \"Add sample files\" puts a template there, or Build makes one. "
                                     "%s/tools: %d of your own files." % (short, short, kit.count_scripts(folder)))
        self.tools_button.set_sensitive(any(tools.values()) or kit.count_scripts(folder) > 0)
        for child in self.ext_flow.get_children():
            self.ext_flow.remove(child)
        # Ticked: what the browsers are told to install now, plus what your kit says you want.
        chosen, self.kit_exts = kit.chosen_extensions() | kit.kit_extension_ids(folder), []  # (checkbox, id)
        for name, ext_id in kit.extensions(folder):
            box = Gtk.CheckButton(label=name, active=ext_id in chosen)
            self.kit_exts.append((box, ext_id))
            self.ext_flow.add(box)
        self.ext_flow.show_all()
        marks = os.path.join(folder, "bookmarks.json")
        if os.path.isfile(marks):
            self.marks_file.set_filename(marks)

    def from_thread(self, fn, *args):
        """Called on a worker thread: run fn on the GUI thread and wait for what it returns."""
        result, done = [], threading.Event()

        def run():
            try:
                result.append(fn(*args))
            finally:
                done.set()

        GLib.idle_add(run)
        done.wait()
        return result[0] if result else None

    def show_code(self, code):
        """Sign-in step: open GitHub's page in the browser and show the code to enter there. "" closes it again."""
        if self.code_box:
            self.code_box.destroy()
            self.code_box = None
        if not code:
            return
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(code, -1)
        self.open_page(kit.DEVICE_PAGE)
        box = Gtk.MessageDialog(transient_for=self, modal=False, message_type=Gtk.MessageType.INFO,
                                buttons=Gtk.ButtonsType.NONE, text="Sign in to GitHub")
        box.format_secondary_markup(
            "Your browser is opening <b>github.com/login/device</b>.\nEnter this code there:\n\n"
            "<span size='xx-large' weight='bold' font_family='monospace'>%s</span>\n\n"
            "It is on your clipboard already: just paste.\nThis window closes by itself once GitHub confirms." % code)
        box.add_button("Open the page again", 1)
        box.add_button("Cancel sign-in", Gtk.ResponseType.CANCEL)
        box.connect("response", lambda _w, answer: self.open_page(kit.DEVICE_PAGE) if answer == 1 else kit.cancel_sign_in())
        box.show_all()
        self.code_box = box
        self.status.set_text("GitHub sign-in: enter the code %s on the page in your browser. Waiting for you …" % code)

    def open_page(self, url):
        """Open a web page in the desktop's default browser, or the one picked in the Apps tab."""
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error:
            if self.s["apps"].get("browser"):
                core._spawn("%s %s" % (self.s["apps"]["browser"], url))

    def confirm(self, text):
        ask = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK_CANCEL, text=text)
        go = ask.run() == Gtk.ResponseType.OK
        ask.destroy()
        return go

    def ask_repo(self, allow_new):
        """Which repository holds the kit: ("new", name), ("existing", "owner/name"), or None when cancelled."""
        ask = Gtk.Dialog(title="Your kit on GitHub", transient_for=self, modal=True)
        ask.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Continue", Gtk.ResponseType.OK)
        ask.set_default_response(Gtk.ResponseType.OK)
        box = ask.get_content_area()
        box.set_spacing(8)
        box.set_border_width(14)
        new = Gtk.RadioButton.new_with_label(None, "New repository: created for you, private")
        old = Gtk.RadioButton.new_with_label_from_widget(new, "Existing repository: one you already have on GitHub")
        entry = Gtk.Entry(activates_default=True, width_chars=44)
        hint = Gtk.Label(xalign=0)
        hint.get_style_context().add_class("dim-label")

        def picked(*_):
            entry.set_text("lazy-kali-kit" if new.get_active() else "")
            hint.set_text("Its name." if new.get_active() else "owner/name, or paste its address from the browser.")

        new.connect("toggled", picked)
        if allow_new:
            box.add(new)
            box.add(old)
        else:
            old.set_active(True)
            box.add(Gtk.Label(label="Which repository holds your kit?", xalign=0))
        for widget in (entry, hint):
            box.add(widget)
        picked()
        ask.show_all()
        answer = None
        while ask.run() == Gtk.ResponseType.OK:
            text = entry.get_text().strip()
            if new.get_active() and kit.NEW_NAME.match(text):
                answer = ("new", text)
            elif old.get_active() and kit.repo_slug(text):
                answer = ("existing", kit.repo_slug(text))
            if answer:
                break
            hint.set_text("That is not a repository name. Use letters, digits, - . _"
                          + ("" if new.get_active() else ", as owner/name."))
        ask.destroy()
        return answer

    def on_kit_git(self, button, action):
        folder = kit.kit_dir(self.s)

        def done(message):
            self.status.set_text(message)
            if os.path.isdir(folder):
                self.use_kit(folder)

        self.background(button, lambda: action(folder, self.kit_ui), done)

    def on_kit_sample(self, _button):
        folder = kit.kit_dir(self.s)
        try:
            added = kit.create_sample(folder)
        except OSError as e:
            self.status.set_text("Failed: %s" % e)
            return
        self.use_kit(folder)
        self.status.set_text("%s: %s" % (folder.replace(core.HOME, "~"),
                                         "added " + ", ".join(added) if added else "the sample files are already there."))

    def on_kit_tools(self, button):
        folder = kit.kit_dir(self.s)
        self.status.set_text("Installing your tools … (enter your password in the dialog; cloning can take a while)")

        def done(log):
            self.status.set_text("\n".join(log[-4:]))
            self.list_catalog()
            self.fill_apps()

        self.background(button, lambda: kit.install_tools(folder), done)

    def on_kit_build(self, _button):
        folder = kit.kit_dir(self.s)
        parts = {key: box.get_active() for box, key in self.build_parts}
        if not any(parts.values()):
            self.status.set_text("Tick what to build: Tools, Scripts, Extensions, Bookmarks.")
            return
        try:
            log = kit.build(folder, self.ticked_browsers(), **parts)
        except OSError as e:
            self.status.set_text("Failed: %s" % e)
            return
        self.use_kit(folder)
        self.status.set_text("\n".join(log + ["Kit built in %s. \"Upload to GitHub\" keeps it safe." % folder.replace(core.HOME, "~")]))

    def on_kit_find(self, _widget):
        for child in self.find_list.get_children():
            self.find_list.remove(child)
        listed = kit.read_tools(kit.kit_dir(self.s))["apt"]
        self.found = []  # (checkbox, package)
        for name, description, state in kit.search_packages(self.find_entry.get_text()):
            mark = "  — in tools.txt" if name in listed else "  ✓ installed" if state == "installed" else ""
            box = Gtk.CheckButton(label="%s%s    %s" % (name, mark, description))
            box.set_sensitive(name not in listed)
            self.found.append((box, name))
            self.find_list.pack_start(box, False, False, 0)
        if not self.found:
            self.find_list.pack_start(Gtk.Label(label="No package with that name.", xalign=0), False, False, 0)
        self.find_add.set_sensitive(bool(self.found))
        self.find_scroll.show()
        self.find_list.show_all()

    def on_kit_find_add(self, _button):
        folder = kit.kit_dir(self.s)
        try:
            added = kit.add_tools(folder, "apt", [name for box, name in self.found if box.get_active()])
        except OSError as e:
            self.status.set_text("Failed: %s" % e)
            return
        self.use_kit(folder)
        self.on_kit_find(None)
        self.status.set_text("Added to tools.txt: %s. \"Install tools and scripts from the kit\" installs them."
                             % ", ".join(added) if added else "Tick a result first.")

    def on_kit_extensions(self, button):
        browsers, ids = self.ticked_browsers(), [ext_id for box, ext_id in self.kit_exts if box.get_active()]
        if not browsers:
            self.status.set_text("Tick a browser first.")
            return

        def done(ok):
            self.status.set_text("%s: %s" % (", ".join(browsers), "cancelled or failed." if not ok else
                                             "%d extensions set. Restart the browser to get them." % len(ids) if ids else
                                             "extension list removed."))
            self.fill_kit()

        self.background(button, lambda: kit.set_extensions(browsers, ids), done)

    def on_kit_import(self, _button):
        path, browsers = self.marks_file.get_filename(), self.ticked_browsers()
        if not path or not browsers:
            self.status.set_text("Choose a bookmarks file and tick a browser first.")
            return
        try:
            count = kit.count_bookmarks(kit.load_bookmarks(path))
            ask = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                    buttons=Gtk.ButtonsType.OK_CANCEL,
                                    text="Replace the bookmarks in %s?" % ", ".join(browsers))
            ask.format_secondary_text("%d bookmarks from %s take their place. The current ones are kept as Bookmarks.bak-<time>."
                                      % (count, os.path.basename(path)))
            go = ask.run() == Gtk.ResponseType.OK
            ask.destroy()
            if go:
                self.status.set_text("\n".join(kit.import_bookmarks(path, browsers)))
        except (OSError, ValueError) as e:
            self.status.set_text("Failed: %s" % e)

    # ----- Theme -----
    def page_theme(self, page):
        g = self.grid(page)
        self.theme = Gtk.ComboBoxText()
        self.theme.connect("changed", self.on_theme)
        self.row(g, 0, "Theme", self.theme, "Change anything below to make it Custom")

        self.w = {}  # theme key -> (widget, getter, setter)

        def colour(key):
            b = Gtk.ColorButton(use_alpha=False)
            b.connect("color-set", lambda *_: self.edited(key))
            self.w[key] = (b, lambda: to_hex(b.get_rgba()), lambda v: b.set_rgba(to_rgba(v)))
            return b

        def spin(key, lo, hi):
            b = Gtk.SpinButton.new_with_range(lo, hi, 1)
            b.connect("value-changed", lambda *_: self.edited(key))
            self.w[key] = (b, b.get_value_as_int, b.set_value)
            return b

        self.row(g, 1, "Primary colour", colour("primary"), "Focused outline, bar values, highlights")
        self.row(g, 2, "Accent colour", colour("accent"), "Bar icons, second outline colour")
        self.row(g, 3, "Unfocused outline", colour("inactive"))
        self.row(g, 4, "Outline thickness", spin("border_size", 0, 12), "pixels")

        effect = Gtk.ComboBoxText()
        for key, text in core.EFFECTS.items():
            effect.append(key, text)
        effect.connect("changed", lambda *_: self.edited("effect"))
        self.w["effect"] = (effect, effect.get_active_id, effect.set_active_id)
        self.row(g, 5, "Outline effect", effect)

        speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 10, 1)
        speed.set_size_request(220, -1)
        speed.connect("value-changed", lambda *_: self.edited("speed"))
        self.w["speed"] = (speed, lambda: int(speed.get_value()), speed.set_value)
        self.row(g, 6, "Effect speed", speed, "For the moving effects")

        self.row(g, 7, "Corner rounding", spin("rounding", 0, 30), "pixels")
        self.row(g, 8, "Gap between windows", spin("gaps_in", 0, 40))
        self.row(g, 9, "Gap at screen edge", spin("gaps_out", 0, 60))
        glow = Gtk.Switch(halign=Gtk.Align.START)
        glow.connect("notify::active", lambda *_: self.edited("glow"))
        self.w["glow"] = (glow, glow.get_active, glow.set_active)
        self.row(g, 10, "Glow around focused window", glow)

        saving = Gtk.Box(spacing=8)
        self.theme_name = Gtk.Entry(placeholder_text="Name for this look", width_chars=24)
        self.theme_name.connect("activate", self.on_save_theme)
        save = Gtk.Button(label="Save as theme")
        save.connect("clicked", self.on_save_theme)
        self.theme_delete = Gtk.Button(label="Delete saved theme")
        self.theme_delete.connect("clicked", self.on_delete_theme)
        for widget in (self.theme_name, save, self.theme_delete):
            saving.pack_start(widget, False, False, 0)
        page.pack_start(saving, False, False, 0)

        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(-1, 150)
        self.preview.connect("draw", self.draw_preview)
        page.pack_start(self.preview, True, True, 0)
        self.list_themes()
        self.fill_theme()

    def list_themes(self):
        """Refill the theme list: built-in, saved, Custom. Selects the current one."""
        was, self.loading = self.loading, True
        self.theme.remove_all()
        saved = core.user_themes()
        for name in list(core.THEMES) + sorted(n for n in saved if n not in core.THEMES) + [core.CUSTOM]:
            self.theme.append(name, name)
        if not self.theme.set_active_id(self.s["theme"]):  # a saved theme that is gone
            self.s["theme"] = core.CUSTOM
            self.theme.set_active_id(core.CUSTOM)
        self.loading = was
        self.theme_delete.set_sensitive(self.s["theme"] in saved and self.s["theme"] not in core.THEMES)

    def on_save_theme(self, _widget):
        name = self.theme_name.get_text().strip()
        if not name or name == core.CUSTOM or name in core.THEMES:
            self.status.set_text("Type a name that is not a built-in theme." if name else "Type a name for the theme first.")
            return
        core.save_theme(name, self.s)
        self.s["theme"] = name
        self.theme_name.set_text("")
        self.list_themes()
        self.status.set_text("Theme \"%s\" saved. Find it in the Theme list." % name)

    def on_delete_theme(self, _button):
        name = self.s["theme"]
        core.save_theme(name, None)
        self.s["theme"] = core.CUSTOM  # the look stays on screen, only the saved name goes
        self.list_themes()
        self.status.set_text("Saved theme \"%s\" deleted." % name)

    def fill_theme(self):
        self.loading = True
        for key, (_widget, _get, put) in self.w.items():
            put(self.s[key])
        self.loading = False
        self.preview.queue_draw()

    def on_theme(self, combo):
        name = combo.get_active_id()
        if self.loading or name is None:
            return
        self.s["theme"] = name
        themes = core.all_themes()
        self.theme_delete.set_sensitive(name in themes and name not in core.THEMES)
        if name in themes:
            self.s.update(themes[name])
            self.fill_theme()
            self.wall_colour.set_rgba(to_rgba(self.s["wallpaper_color"]))

    def edited(self, key):
        if self.loading:
            return
        self.s[key] = self.w[key][1]()
        self.loading = True
        self.theme.set_active_id(core.CUSTOM)
        self.loading = False
        self.s["theme"] = core.CUSTOM
        self.preview.queue_draw()

    def draw_preview(self, area, cr):
        """Mock desktop: wallpaper colour, an unfocused and a focused window, a bar pill."""
        import cairo

        s, W, H = self.s, area.get_allocated_width(), area.get_allocated_height()
        rgb = lambda h: tuple(v / 255 for v in core._rgb(h))

        def rounded(x, y, w, h, r):
            r = min(r, w / 2, h / 2)
            cr.new_sub_path()
            for cx, cy, a in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
                cr.arc(cx, cy, r, math.radians(a), math.radians(a + 90))
            cr.close_path()

        cr.set_source_rgb(*rgb(s["wallpaper_color"]))
        cr.paint()
        gap, top = max(4, s["gaps_out"]), 34
        cr.set_source_rgba(0.05, 0.06, 0.07, 0.88)
        rounded(gap, 6, 150, 22, 8)
        cr.fill()
        cr.set_font_size(12)
        cr.move_to(gap + 10, 22)
        cr.set_source_rgb(*rgb(s["accent"]))
        cr.show_text("CPU ")
        cr.set_source_rgb(*rgb(s["primary"]))
        cr.show_text("12%   14:02")

        half = (W - 2 * gap - s["gaps_in"] * 2) / 2
        for i, focused in enumerate((False, True)):
            x, y, w, h = gap + i * (half + s["gaps_in"] * 2), top, half, H - top - gap
            if h <= 0 or w <= 0:
                return
            cr.set_source_rgb(0.09, 0.10, 0.12)
            rounded(x, y, w, h, s["rounding"])
            cr.fill()
            if not s["border_size"]:
                continue
            if focused:
                grad = cairo.LinearGradient(x, y, x + w, y + h)
                colours = core.effect_colours(s)
                for n, c in enumerate(colours):
                    grad.add_color_stop_rgb(n / max(1, len(colours) - 1), *rgb(c))
                cr.set_source(grad)
            else:
                cr.set_source_rgb(*rgb(s["inactive"]))
            cr.set_line_width(s["border_size"])
            rounded(x, y, w, h, s["rounding"])
            cr.stroke()

    # ----- Wallpaper -----
    def page_wallpaper(self, page):
        self.note(page, "Used for the desktop and the lock screen. Themes ship a plain colour; pick any image of your own.")
        g = self.grid(page)
        solid = Gtk.RadioButton.new_with_label(None, "Solid colour")
        image = Gtk.RadioButton.new_with_label_from_widget(solid, "Image")
        rotate = Gtk.RadioButton.new_with_label_from_widget(solid, "Rotate images from a folder")
        for mode, radio in (("color", solid), ("image", image), ("rotate", rotate)):
            radio.set_active(self.s["wallpaper_mode"] == mode)
            radio.connect("toggled", lambda w, mode=mode: w.get_active() and self.s.__setitem__("wallpaper_mode", mode))

        self.wall_colour = Gtk.ColorButton(use_alpha=False, rgba=to_rgba(self.s["wallpaper_color"]))
        self.wall_colour.connect("notify::rgba", self.on_wall_colour)
        self.row(g, 0, "", solid)
        g.attach(self.wall_colour, 2, 0, 1, 1)

        chooser = Gtk.FileChooserButton(title="Choose a wallpaper", hexpand=True)
        images = Gtk.FileFilter()
        images.set_name("Images")
        images.add_pixbuf_formats()
        chooser.add_filter(images)
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
        if os.path.isfile(self.s["wallpaper_image"]):
            chooser.set_filename(self.s["wallpaper_image"])
        elif pictures:
            chooser.set_current_folder(pictures)
        self.row(g, 1, "", image)
        g.attach(chooser, 2, 1, 1, 1)

        folder = Gtk.FileChooserButton(title="Choose a folder of wallpapers", action=Gtk.FileChooserAction.SELECT_FOLDER)
        if os.path.isdir(self.s["wallpaper_dir"]):
            folder.set_filename(self.s["wallpaper_dir"])
        elif pictures:
            folder.set_current_folder(pictures)

        def folder_picked(w):
            self.s["wallpaper_dir"] = w.get_filename() or ""
            rotate.set_active(True)

        folder.connect("file-set", folder_picked)
        self.row(g, 2, "", rotate)
        g.attach(folder, 2, 2, 1, 1)
        minutes = Gtk.SpinButton.new_with_range(0, 1440, 1)
        minutes.set_value(self.s["wallpaper_minutes"])
        minutes.connect("value-changed", lambda w: self.s.__setitem__("wallpaper_minutes", w.get_value_as_int()))
        every = Gtk.Box(spacing=8)
        every.pack_start(minutes, False, False, 0)
        every.pack_start(Gtk.Label(label="minutes between images (0 = only when I press the key)"), False, False, 0)
        self.row(g, 3, "    Change every", every)
        self.note(page, "Rotation uses the .png and .jpg files in the folder, in name order. "
                        "Super+Shift+W shows the next one at any time.")

        self.thumb = Gtk.Image()
        page.pack_start(self.thumb, True, True, 0)

        def picked(w):
            self.s["wallpaper_image"] = w.get_filename() or ""
            image.set_active(True)
            self.show_thumb()

        chooser.connect("file-set", picked)
        self.show_thumb()

    def on_wall_colour(self, button, _param):
        self.s["wallpaper_color"] = to_hex(button.get_rgba())
        self.preview.queue_draw()

    def show_thumb(self):
        try:
            self.thumb.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_size(self.s["wallpaper_image"], 520, 300))
        except GLib.Error:
            self.thumb.clear()

    # ----- Clipboard -----
    def page_clipboard(self, page):
        self.note(page, "Clipboard history (Super+V) is kept on this computer only, in ~/.cache/cliphist. "
                        "Nothing is sent anywhere. Everything you copy is kept, passwords too, unless you tick the options below.")
        self.check(page, "Keep a clipboard history", "clip_enabled")
        self.check(page, "Never store copies from password managers (entries marked sensitive)", "clip_skip_sensitive")
        self.check(page, "Wipe the history every time I log in", "clip_wipe_login")
        self.check(page, "Also keep images and screenshots", "clip_images")
        g = self.grid(page)
        most = Gtk.SpinButton.new_with_range(10, 2000, 10)
        most.set_value(self.s["clip_max"])
        most.connect("value-changed", lambda w: self.s.__setitem__("clip_max", w.get_value_as_int()))
        self.row(g, 0, "Entries to keep", most)
        wipe = Gtk.Button(label="Wipe history now", halign=Gtk.Align.START)
        wipe.connect("clicked", lambda *_: (core.wipe_clipboard(), self.status.set_text("Clipboard history wiped.")))
        page.pack_start(wipe, False, False, 0)

    # ----- Debug -----
    def page_debug(self, page):
        self.head(page, "Update")
        self.note(page, "Gets the newest lazy-kali-ricer from GitHub, writes your configs again with your saved "
                        "settings, and reopens this window. \"Go back\" returns to the version you had before the "
                        "last update. Same as:  lazy-kali-ricer update | revert")
        self.version_label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        page.pack_start(self.version_label, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.update_buttons = {}
        for key, label in (("check", "Check for updates"), ("update", "Update"), ("revert", "Go back to the previous version")):
            button = Gtk.Button(label=label)
            button.connect("clicked", self.on_update, key)
            self.update_buttons[key] = button
            row.pack_start(button, False, False, 0)
        self.update_buttons["update"].get_style_context().add_class("suggested-action")
        page.pack_start(row, False, False, 0)
        self.fill_version()
        self.head(page, "Debug")
        self.note(page, "Something broken? Turn logging on, reproduce the problem, then save a report and "
                        "share that file. Home path, user name and host name are replaced in it. Same as:\n"
                        "    lazy-kali-ricer debug on | off | report")
        box = Gtk.Box(spacing=10)
        box.pack_start(Gtk.Label(label="Debug logging", xalign=0), False, False, 0)
        switch = Gtk.Switch(active=bool(self.s["debug"]))
        switch.connect("notify::active", lambda w, _p: self.s.update(debug=core.set_debug(w.get_active())["debug"]))
        box.pack_start(switch, False, False, 0)
        page.pack_start(box, False, False, 0)
        report = Gtk.Button(label="Save debug report", halign=Gtk.Align.START)
        report.connect("clicked", lambda *_: self.status.set_text("Report saved: " + core.debug_report()))
        page.pack_start(report, False, False, 0)

    def fill_version(self):
        now, earlier = core.version(), core.previous_versions()
        self.version_label.set_text("Installed:  %s%s" % (
            now or "unknown (not installed with git clone)",
            "\nBefore the last update:  %s" % (core.version(earlier[-1]) or earlier[-1][:7]) if earlier else ""))
        self.update_buttons["revert"].set_sensitive(bool(earlier))
        for key in ("check", "update"):
            self.update_buttons[key].set_sensitive(bool(now))

    def on_update(self, button, key):
        if key == "check":
            self.status.set_text("Asking GitHub …")

            def checked(result):
                count, news = result
                self.status.set_text(news if count < 0 else "You have the newest version." if count == 0 else
                                     "%d newer version%s:\n%s" % (count, "s" * (count > 1), news))

            self.background(button, core.update_check, checked)
            return
        self.status.set_text("Updating …" if key == "update" else "Going back …")

        def work():
            changed, message = core.update() if key == "update" else core.revert()
            return changed, message + ("\n" + core.reapply() if changed else "")

        def done(result):
            changed, message = result
            self.status.set_text(message)
            self.fill_version()
            if changed:  # the files on disk are another version now: start again with them
                GLib.timeout_add(1500, lambda: os.execv(sys.executable, [sys.executable, os.path.join(core.REPO, "lazy-kali-ricer")]))

        self.background(button, work, done)

    # ----- Uninstall -----
    def page_uninstall(self, page):
        self.note(page, "Everything lazy-kali-ricer changed on this system. Untick a line to keep it. "
                        "Nothing is removed until you press the button. Packages and clipboard history "
                        "start unticked because other programs, or you, may still want them.")
        self.undo_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add(self.undo_list)
        page.pack_start(scroll, True, True, 0)
        button = Gtk.Button(label="Uninstall ticked items", halign=Gtk.Align.START)
        button.get_style_context().add_class("destructive-action")
        button.connect("clicked", self.on_uninstall)
        page.pack_start(button, False, False, 0)
        self.list_uninstall()

    def list_uninstall(self):
        for child in self.undo_list.get_children():
            self.undo_list.remove(child)
        self.undo = []  # (checkbox, item)
        for item in core.uninstall_items():
            box = Gtk.CheckButton(label=item["label"], active=item["on"])
            self.undo.append((box, item))
            self.undo_list.pack_start(box, False, False, 0)
        if not self.undo:
            self.undo_list.pack_start(Gtk.Label(label="Nothing installed yet.", xalign=0), False, False, 0)
        self.undo_list.show_all()

    def on_uninstall(self, _button):
        chosen = [item for box, item in self.undo if box.get_active()]
        if not chosen:
            return
        ask = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                buttons=Gtk.ButtonsType.OK_CANCEL, text="Carry out these %d steps?" % len(chosen))
        ask.format_secondary_text("\n".join(item["label"] for item in chosen))
        go = ask.run() == Gtk.ResponseType.OK
        ask.destroy()
        if go:
            try:
                self.status.set_text("\n".join(core.uninstall(chosen)[-4:]))
            except OSError as e:
                self.status.set_text("Failed: %s" % e)
            self.list_uninstall()

    # ----- Apply -----
    def on_apply(self, _button):
        try:
            self.status.set_text("\n".join(core.apply(self.s)))
        except OSError as e:
            self.status.set_text("Failed: %s" % e)


def main():
    core.DIALOGS = True
    win = Ricer()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
