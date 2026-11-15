#!/usr/bin/env python3
"""Screen-share picker with previews, for xdg-desktop-portal-hyprland (set in ../xdph.conf).

The portal runs this when an app (Discord, a browser, OBS) asks to share the screen. It gives the
screens and windows in the environment and reads the choice from our output:

  [SELECTION]<r>/screen:<name>   [SELECTION]<r>/window:<id>   [SELECTION]<r>/region:<name>@x,y,w,h

<r> is the letter r when "remember" is ticked (offered only when the portal passes --allow-token).
No output means cancelled. Each choice shows a picture taken with grim.
"""
import json
import os
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

THUMB = (300, 170)
CSS = """
window { background: #14141a; color: #e8e8ee; border: 2px solid __LKR_PRIMARY__; border-radius: 10px; }
flowboxchild { border: 2px solid transparent; border-radius: 8px; padding: 6px; }
flowboxchild:hover { border-color: alpha(__LKR_ACCENT__, 0.5); }
flowboxchild:selected { border-color: __LKR_PRIMARY__; background: alpha(__LKR_PRIMARY__, 0.15); }
.suggested-action { background: __LKR_PRIMARY__; color: #000000; }
"""


def screens():
    """[(choice, label, grim arguments)] from XDPH_OUTPUT_SHARING_LIST: <n>:name:x:y:w:h; per screen."""
    found = []
    for part in os.environ.get("XDPH_OUTPUT_SHARING_LIST", "").split(";"):
        bits = part.split(":")
        if len(bits) >= 6:
            name, (x, y, w, h) = bits[-5], bits[-4:]
            found.append(("screen:" + name, "%s  (%s×%s)" % (name, w, h), ["-o", name]))
    return found


def windows():
    """[(choice, label, grim arguments)] from XDPH_WINDOW_SHARING_LIST:
    <id>[HC>]<class>[HT>]<title>[HE>]<address>[HA>] per window. grim wants Hyprland's stableId,
    which we look up by address."""
    try:
        clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=5).stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        clients = []
    stable = {int(c["address"], 16): c.get("stableId") for c in clients}
    found = []
    for part in os.environ.get("XDPH_WINDOW_SHARING_LIST", "").split("[HA>]"):
        try:
            ident, rest = part.split("[HC>]", 1)
            cls, rest = rest.split("[HT>]", 1)
            title, address = rest.split("[HE>]", 1)
        except ValueError:
            continue
        if cls == "lkr-share-picker":
            continue
        handle = stable.get(int(address)) if address.isdigit() else None
        found.append(("window:" + ident, "%s — %s" % (cls, title) if title else cls, ["-T", str(handle)] if handle else None))
    return found


def picture(grim_args):
    """A thumbnail of what grim captures, or None."""
    if not grim_args:
        return None
    try:
        data = subprocess.run(["grim", "-l", "0", *grim_args, "-"], capture_output=True, timeout=10).stdout
        loader = GdkPixbuf.PixbufLoader()
        loader.connect("size-prepared", lambda l, w, h: l.set_size(*fit(w, h)))
        loader.write(data)
        loader.close()
        return loader.get_pixbuf()
    except (OSError, GLib.Error, subprocess.TimeoutExpired):
        return None


def fit(w, h):
    scale = min(THUMB[0] / w, THUMB[1] / h, 1)
    return max(1, int(w * scale)), max(1, int(h * scale))


class Picker(Gtk.Window):
    def __init__(self, allow_token):
        super().__init__(title="Select what to share")
        self.set_default_size(700, 560)
        self.set_resizable(False)
        self.choice = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        self.add(box)
        self.stack = Gtk.Stack()
        switcher = Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.CENTER)
        box.pack_start(switcher, False, False, 0)
        box.pack_start(self.stack, True, True, 0)
        todo = []
        for title, items in (("Screens", screens()), ("Windows", windows())):
            flow = Gtk.FlowBox(max_children_per_line=2, min_children_per_line=2, homogeneous=True,
                               activate_on_single_click=False, valign=Gtk.Align.START)
            flow.connect("child-activated", lambda *_: self.share())
            flow.connect("selected-children-changed", self.on_select)
            for choice, label, grim_args in items:
                cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                image = Gtk.Image.new_from_icon_name("image-loading", Gtk.IconSize.DIALOG)
                image.set_size_request(*THUMB)
                cell.pack_start(image, False, False, 0)
                cell.pack_start(Gtk.Label(label=label, ellipsize=Pango.EllipsizeMode.END, max_width_chars=34), False, False, 0)
                child = Gtk.FlowBoxChild()
                child.choice = choice
                child.add(cell)
                flow.add(child)
                todo.append((image, grim_args))
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            scroll.add(flow)
            self.stack.add_titled(scroll, title, title)
        self.stack.connect("notify::visible-child", lambda *_: self.on_select(self.stack.get_visible_child().get_child().get_child()))

        row = Gtk.Box(spacing=8)
        self.remember = Gtk.CheckButton(label="Remember this choice for future requests", no_show_all=not allow_token)
        row.pack_start(self.remember, False, False, 0)
        self.share_button = Gtk.Button(label="Share", sensitive=False)
        self.share_button.get_style_context().add_class("suggested-action")
        self.share_button.connect("clicked", lambda _b: self.share())
        row.pack_end(self.share_button, False, False, 0)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _b: self.close())
        row.pack_end(cancel, False, False, 0)
        region = Gtk.Button(label="Region…", tooltip_text="Drag a rectangle on a screen")
        region.connect("clicked", self.on_region)
        row.pack_end(region, False, False, 0)
        box.pack_start(row, False, False, 0)

        self.connect("destroy", Gtk.main_quit)
        self.connect("key-press-event", lambda _w, e: e.keyval == 0xff1b and self.close())  # Escape
        threading.Thread(target=self.load, args=(todo,), daemon=True).start()

    def load(self, todo):
        for image, grim_args in todo:
            shot = picture(grim_args)
            GLib.idle_add(image.set_from_pixbuf, shot) if shot else GLib.idle_add(
                image.set_from_icon_name, "image-missing", Gtk.IconSize.DIALOG)

    def on_select(self, flow):
        picked = flow.get_selected_children()
        self.choice = picked[0].choice if picked else None
        self.share_button.set_sensitive(bool(self.choice))

    def share(self):
        if self.choice:
            print("[SELECTION]%s/%s" % ("r" if self.remember.get_active() else "", self.choice), flush=True)
            self.close()

    def on_region(self, _button):
        self.hide()
        while Gtk.events_pending():
            Gtk.main_iteration()
        try:
            out = subprocess.run(["slurp", "-f", "%o %x %y %w %h"], capture_output=True, text=True).stdout.split()
            name, (x, y, w, h) = out[0], map(int, out[1:5])
        except (OSError, ValueError, IndexError):  # cancelled, or no slurp
            self.show()
            return
        for part in os.environ.get("XDPH_OUTPUT_SHARING_LIST", "").split(";"):  # the portal wants it relative to the screen
            bits = part.split(":")
            if len(bits) >= 6 and bits[-5] == name:
                x, y = x - int(bits[-4]), y - int(bits[-3])
        self.choice = "region:%s@%d,%d,%d,%d" % (name, x, y, w, h)
        self.share()


def main():
    GLib.set_prgname("lkr-share-picker")  # the window class, for the float rule in hyprland.lua
    style = Gtk.CssProvider()
    style.load_from_data(CSS.encode())
    win = Picker("--allow-token" in sys.argv)
    Gtk.StyleContext.add_provider_for_screen(win.get_screen(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
