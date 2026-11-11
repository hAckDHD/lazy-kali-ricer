#!/usr/bin/env python3
"""Drop-down control panels for the bar (GTK).

  panel.py network     Ethernet section (switch per device) and Wi-Fi section
                       (radio switch + nearby networks, click one to join).
  panel.py bluetooth   Bluetooth power switch + paired devices (switch = connected).

Running it again while a panel is open closes that panel. Esc closes it too.
"""
import os
import re
import signal
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

APP_ID = "rice-panel"  # Hyprland window rule matches this class
PIDFILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "rice-panel.pid")

CSS = b"""
window { background: transparent; }
#panel {
    background: rgba(22, 25, 30, 0.97);
    border: 2px solid __LKR_PRIMARY__;
    border-radius: 14px;
    padding: 16px;
}
* { font-family: "Hack", "Font Awesome 6 Free Solid", "Font Awesome 6 Brands Regular", monospace; font-size: 14px; color: #dfe9e3; }
.section { color: __LKR_ACCENT__; font-weight: bold; font-size: 15px; }
.card { background: #23272e; border-radius: 10px; padding: 10px 12px; }
.name { font-weight: bold; }
.sub { color: #8a988f; font-size: 12px; }
.on { color: __LKR_PRIMARY__; }
.off { color: #ff5370; }
separator { background: rgba(__LKR_ACCENT_RGB__, 0.35); min-height: 1px; margin: 6px 0; }
switch { background: #3a3f48; border-radius: 14px; border: none; min-width: 46px; min-height: 24px; }
switch:checked { background: __LKR_PRIMARY__; }
switch slider { background: #f2f5f3; border-radius: 12px; min-width: 22px; min-height: 22px; margin: 1px; border: none; }
switch image { color: transparent; }
list, row { background: transparent; }
row { border-radius: 10px; padding: 0; margin: 2px 0; }
row:hover .card, row:selected .card { background: #2e3440; }
row.active .card { border-left: 3px solid __LKR_PRIMARY__; }
entry { background: #15181c; border: 1px solid __LKR_ACCENT__; border-radius: 8px; padding: 6px 8px; color: #ffffff; }
button { background: __LKR_PRIMARY__; border: none; border-radius: 8px; padding: 6px 12px; }
button label { color: #0a0f0b; font-weight: bold; }
scrolledwindow { border: none; }
"""


def run(*cmd, timeout=25):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timed out"


def nm_rows(*args):
    """Rows of a terse nmcli listing, split on unescaped colons."""
    out = run("nmcli", "-t", *args)[1]
    return [[f.replace("\\:", ":") for f in re.split(r"(?<!\\):", line)] for line in out.splitlines() if line]


def bt_devices(kind):
    return [line.split(" ", 2)[1:] for line in run("bluetoothctl", "devices", kind)[1].splitlines()
            if line.startswith("Device ")]


def label(text, cls=None, xalign=0.0):
    w = Gtk.Label(label=text, xalign=xalign)
    w.set_ellipsize(3)
    if cls:
        w.get_style_context().add_class(cls)
    return w


class Panel(Gtk.Window):
    def __init__(self, mode):
        super().__init__(title=APP_ID)
        self.mode = mode
        self.busy = False
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(440, -1)
        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.connect("key-press-event", lambda _w, e: e.keyval == Gdk.KEY_Escape and Gtk.main_quit())
        self.connect("destroy", Gtk.main_quit)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="panel")
        self.add(self.box)
        self.refresh()

    # ----- plumbing -----
    def act(self, *cmd):
        """Run a command off the UI thread, then redraw with the new state."""
        if self.busy:
            return
        self.busy = True

        def work():
            code, msg = run(*cmd)
            if code != 0 or "Failed" in msg:
                run("notify-send", "-u", "critical", self.mode.capitalize(), msg[-200:] or "Failed")
            GLib.idle_add(self.done)

        threading.Thread(target=work, daemon=True).start()

    def done(self):
        self.busy = False
        self.refresh()

    def refresh(self):
        for child in self.box.get_children():
            self.box.remove(child)
        (self.build_network if self.mode == "network" else self.build_bluetooth)()
        self.box.show_all()
        self.resize(440, 1)

    def header(self, title, state=None, on_toggle=None):
        row = Gtk.Box(spacing=10)
        row.pack_start(label(title, "section"), True, True, 0)
        if on_toggle:
            row.pack_end(self.switch(state, on_toggle), False, False, 0)
        self.box.pack_start(row, False, False, 0)

    def switch(self, state, on_toggle):
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        sw.set_active(bool(state))
        sw.connect("state-set", lambda _s, want: on_toggle(want) or True)
        return sw

    def card(self, title, sub, sub_cls="sub", end=None):
        card = Gtk.Box(spacing=10)
        card.get_style_context().add_class("card")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.pack_start(label(title, "name"), False, False, 0)
        text.pack_start(label(sub, sub_cls), False, False, 0)
        card.pack_start(text, True, True, 0)
        if end:
            card.pack_end(end, False, False, 0)
        return card

    # ----- network -----
    def build_network(self):
        self.header("  Ethernet")
        wired = [r for r in nm_rows("-f", "DEVICE,TYPE,STATE,CONNECTION", "device") if r[1] == "ethernet"]
        for dev, _typ, state, conn in wired:
            up = state.startswith("connected")
            sw = self.switch(up, lambda want, d=dev: self.act("nmcli", "device", "connect" if want else "disconnect", d))
            self.box.pack_start(self.card(dev, ("Connected · " + conn) if up else state.capitalize(),
                                          "on" if up else "off", sw), False, False, 0)
        if not wired:
            self.box.pack_start(label("No wired adapter", "sub"), False, False, 0)

        self.box.pack_start(Gtk.Separator(), False, False, 0)
        radio = run("nmcli", "radio", "wifi")[1] == "enabled"
        self.header("  Wi-Fi", radio, lambda want: self.act("nmcli", "radio", "wifi", "on" if want else "off"))
        if not radio:
            self.box.pack_start(label("Wi-Fi is off", "sub"), False, False, 0)
            return

        saved = {r[0] for r in nm_rows("-f", "NAME", "connection", "show")}
        nets, seen = [], set()
        for row in nm_rows("-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "auto"):
            if len(row) >= 4 and row[1] and row[1] not in seen:
                seen.add(row[1])
                nets.append(row[:4])
        if not nets:
            self.box.pack_start(label("No networks in range", "sub"), False, False, 0)
            return

        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for in_use, ssid, sig, sec in nets:
            active = in_use.strip() == "*"
            secured = bool(sec) and sec != "--"
            sub = "Connected" if active else ("%s%s" % (sec if secured else "Open", " · saved" if ssid in saved else ""))
            row = Gtk.ListBoxRow()
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            holder.pack_start(self.card(("  " if secured else "") + ssid, sub, "on" if active else "sub",
                                        label(sig + "%", "on", 1.0)), False, False, 0)
            row.add(holder)
            if active:
                row.get_style_context().add_class("active")
            row.net = (ssid, active, secured, ssid in saved, holder)
            lst.add(row)
        lst.connect("row-activated", self.on_wifi)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_min_content_height(min(64 * len(nets), 380))
        scroll.add(lst)
        self.box.pack_start(scroll, True, True, 0)

    def on_wifi(self, _lst, row):
        ssid, active, secured, known, holder = row.net
        if active:
            self.act("nmcli", "connection", "down", "id", ssid)
        elif known:
            self.act("nmcli", "connection", "up", "id", ssid)
        elif not secured:
            self.act("nmcli", "device", "wifi", "connect", ssid)
        elif len(holder.get_children()) == 1:  # new secured network: ask for the password inline
            line = Gtk.Box(spacing=8, margin_top=6, margin_bottom=4)
            entry = Gtk.Entry(visibility=False, placeholder_text="Password")
            join = Gtk.Button(label="Join")
            go = lambda *_: entry.get_text() and self.act("nmcli", "device", "wifi", "connect", ssid,
                                                          "password", entry.get_text())
            entry.connect("activate", go)
            join.connect("clicked", go)
            line.pack_start(entry, True, True, 0)
            line.pack_end(join, False, False, 0)
            holder.pack_start(line, False, False, 0)
            holder.show_all()
            entry.grab_focus()

    # ----- bluetooth -----
    def build_bluetooth(self):
        powered = "Powered: yes" in run("bluetoothctl", "show")[1]
        self.header("  Bluetooth", powered,
                    lambda want: self.act("bluetoothctl", "power", "on" if want else "off"))
        if not powered:
            self.box.pack_start(label("Bluetooth is off", "off"), False, False, 0)
            return
        self.box.pack_start(Gtk.Separator(), False, False, 0)
        self.header("Paired devices")
        connected = {mac for mac, _ in bt_devices("Connected")}
        paired = bt_devices("Paired")
        for mac, name in paired:
            on = mac in connected
            sw = self.switch(on, lambda want, m=mac: self.act("bluetoothctl", "connect" if want else "disconnect", m))
            self.box.pack_start(self.card(name, "Connected" if on else "Not connected", "on" if on else "sub", sw),
                                False, False, 0)
        if not paired:
            self.box.pack_start(label("No paired devices", "sub"), False, False, 0)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("network", "bluetooth"):
        sys.exit(__doc__)
    # Second click on the bar item closes the open panel instead of stacking another.
    try:
        with open(PIDFILE) as f:
            old_pid, old_mode = f.read().split()
        os.kill(int(old_pid), signal.SIGTERM)
        if old_mode == mode:
            os.remove(PIDFILE)
            return
    except (OSError, ValueError):
        pass
    with open(PIDFILE, "w") as f:
        f.write("%d %s" % (os.getpid(), mode))

    GLib.set_prgname(APP_ID)
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    GLib.timeout_add(300, lambda: True)  # let Python see SIGTERM while GTK idles
    Panel(mode).show_all()
    Gtk.main()
    try:
        with open(PIDFILE) as f:
            if f.read().split()[0] == str(os.getpid()):
                os.remove(PIDFILE)
    except (OSError, IndexError):
        pass


if __name__ == "__main__":
    main()
