"""GTK window: pick apps, theme, wallpaper and clipboard options, then Apply."""
import math
import os
import shutil
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from . import core  # noqa: E402

NONE, CUSTOM_CMD = "", "__custom__"
THEME_KEYS = list(core.THEMES[core.DEFAULT_THEME])


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
        for title, build in (("Apps", self.page_apps), ("Get apps", self.page_get_apps), ("Theme", self.page_theme), ("Wallpaper", self.page_wallpaper),
                             ("Clipboard", self.page_clipboard), ("Debug", self.page_debug),
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
        book.connect("switch-page", lambda *_: self.list_uninstall())  # the list changes after an Apply
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

    # ----- Get apps -----
    def page_get_apps(self, page):
        self.note(page, "Common programs for each job. Tick the ones you want and install them; "
                        "they then show up in the Apps tab. Already installed ones are greyed out.")
        self.catalog = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.pack_start(self.catalog, False, False, 0)
        self.get_button = Gtk.Button(label="Install ticked apps", halign=Gtk.Align.START)
        self.get_button.connect("clicked", self.on_get_apps)
        page.pack_start(self.get_button, False, False, 0)
        self.list_catalog()

    def list_catalog(self):
        for child in self.catalog.get_children():
            self.catalog.remove(child)
        state, self.wanted = core.catalog_state(), []  # (checkbox, package)
        for role, common in core.CATALOG.items():
            head = Gtk.Label(label=core.ROLES[role][0], xalign=0)
            head.get_style_context().add_class("heading")
            self.catalog.pack_start(head, False, False, 0)
            line = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6)
            for name, pkg in common:
                if state[pkg] == "missing":  # not in this system's package sources
                    continue
                box = Gtk.CheckButton(label=name + ("  ✓" if state[pkg] == "installed" else ""))
                box.set_sensitive(state[pkg] == "available")
                self.wanted.append((box, pkg))
                line.add(box)
            self.catalog.pack_start(line, False, False, 0)
        self.catalog.show_all()

    def on_get_apps(self, _button):
        packages = [pkg for box, pkg in self.wanted if box.get_active()]
        if not packages:
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

    # ----- Theme -----
    def page_theme(self, page):
        g = self.grid(page)
        self.theme = Gtk.ComboBoxText()
        for name in list(core.THEMES) + [core.CUSTOM]:
            self.theme.append(name, name)
        self.theme.set_active_id(self.s["theme"])
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

        self.preview = Gtk.DrawingArea()
        self.preview.set_size_request(-1, 150)
        self.preview.connect("draw", self.draw_preview)
        page.pack_start(self.preview, True, True, 0)
        self.fill_theme()

    def fill_theme(self):
        self.loading = True
        for key, (_widget, _get, put) in self.w.items():
            put(self.s[key])
        self.loading = False
        self.preview.queue_draw()

    def on_theme(self, combo):
        name = combo.get_active_id()
        self.s["theme"] = name
        if name in core.THEMES:
            self.s.update(core.THEMES[name])
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
    win = Ricer()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()
