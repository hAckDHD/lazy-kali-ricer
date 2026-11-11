#!/usr/bin/env python3
"""Window helper for Hyprland (Lua-dispatch build) + Waybar.

  winctl.py switch            Alt+Tab menu: every open window with its app icon.
                              Enter focuses it (jumping to its workspace) or, if it
                              is minimized, brings it back to the current workspace.
  winctl.py switch-minimized  Same menu, minimized windows only.
  winctl.py minimize          Send the active window to special:minimized.
  winctl.py count             Waybar block: how many windows are minimized (JSON).
"""
import glob
import json
import os
import subprocess
import sys

MIN_WS = "special:minimized"
CACHE = os.path.expanduser("~/.cache/winctl-icons.json")
APP_DIRS = [
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
    "/usr/local/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
]
ICON_DIRS = [
    "/var/lib/flatpak/exports/share/icons",
    os.path.expanduser("~/.local/share/flatpak/exports/share/icons"),
]
FALLBACK_ICON = "application-x-executable"


def hypr(*args):
    return subprocess.run(["hyprctl", *args], capture_output=True, text=True).stdout


def dispatch(lua):
    hypr("dispatch", lua)


def clients():
    return [c for c in json.loads(hypr("clients", "-j") or "[]") if c.get("mapped", True)]


def minimized():
    return [c for c in clients() if c["workspace"]["name"] == MIN_WS]


def refresh_bar():
    subprocess.run(["pkill", "-RTMIN+8", "-x", "waybar"])


# ---------- icons ----------
def desktop_icon_name(cls):
    """Icon= value of the .desktop file that belongs to a window class."""
    want = cls.lower()
    best = None
    for d in APP_DIRS:
        for path in glob.glob(os.path.join(d, "*.desktop")):
            stem = os.path.basename(path)[:-8].lower()
            icon = wmclass = None
            try:
                with open(path, errors="ignore") as f:
                    for line in f:
                        if line.startswith("Icon=") and icon is None:
                            icon = line[5:].strip()
                        elif line.startswith("StartupWMClass="):
                            wmclass = line[15:].strip().lower()
            except OSError:
                continue
            if not icon:
                continue
            if wmclass == want or stem == want:
                return icon
            if best is None and (stem.endswith("." + want) or want in stem.split(".")):
                best = icon
    return best


def resolve_icon(cls):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    theme = Gtk.IconTheme.get_default()
    for d in ICON_DIRS:
        if os.path.isdir(d):
            theme.append_search_path(d)
    for name in (desktop_icon_name(cls), cls, cls.lower(), FALLBACK_ICON):
        if not name:
            continue
        if os.path.isabs(name) and os.path.exists(name):
            return name
        info = theme.lookup_icon(name, 64, 0)
        if info and info.get_filename():
            return info.get_filename()
    return ""


def icon_for(cls):
    try:
        with open(CACHE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    path = cache.get(cls)
    if not path or not os.path.exists(path):
        path = resolve_icon(cls)
        cache[cls] = path
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w") as f:
                json.dump(cache, f)
        except OSError:
            pass
    return path


# ---------- actions ----------
def restore(win):
    ws = json.loads(hypr("activeworkspace", "-j"))["id"]
    dispatch('hl.dsp.window.move({ window = "address:%s", workspace = %d, follow = true })' % (win["address"], ws))
    refresh_bar()


def go_to(win):
    if win["workspace"]["name"] == MIN_WS:
        restore(win)
    else:
        dispatch('hl.dsp.focus({ window = "address:%s" })' % win["address"])
        dispatch('hl.dsp.window.alter_zorder({ mode = "top" })')


def switch(only_minimized=False):
    if subprocess.run(["pgrep", "-x", "wofi"], capture_output=True).returncode == 0:
        return  # menu already open (Alt+Tab pressed again)
    wins = minimized() if only_minimized else clients()
    if not wins:
        return
    # Most-recently-used first, but the window you are on goes last, so
    # Alt+Tab then Enter lands on the previous window.
    wins.sort(key=lambda c: c.get("focusHistoryID", 0))
    if not only_minimized and len(wins) > 1:
        wins = wins[1:] + wins[:1]

    entries = []
    for c in wins:
        ws = c["workspace"]["name"]
        where = "minimized" if ws == MIN_WS else "workspace " + ws
        title = " ".join((c.get("title") or c["class"]).replace(":", " ").split())[:70]
        label = "%s   ·   %s   (%s)" % (title, c["class"], where)
        icon = icon_for(c["class"])
        entries.append((("img:%s:text:%s" % (icon, label)) if icon else label, label, c))

    out = subprocess.run(
        ["wofi", "--dmenu", "--allow-images", "--prompt", "Minimized" if only_minimized else "Windows",
         "--cache-file", "/dev/null", "--define", "image_size=32",
         "--width", "900", "--height", str(min(140 + 52 * len(entries), 760)),
         # the minimized list drops down from its block in the bar
         *(["--location=top_right", "--yoffset=6"] if only_minimized else [])],
        input="\n".join(e[0] for e in entries), capture_output=True, text=True,
    ).stdout.strip()
    if not out:
        return
    for raw, label, c in entries:
        if out == raw or out == label or out.endswith(":text:" + label):
            go_to(c)
            return


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "switch":
        switch()
    elif cmd == "switch-minimized":
        switch(only_minimized=True)
    elif cmd == "minimize":
        dispatch('hl.dsp.window.move({ workspace = "%s", follow = false })' % MIN_WS)
        refresh_bar()
    elif cmd == "count":
        wins = minimized()
        tip = "\n".join("• " + (c.get("title") or c["class"]) for c in wins) or "No minimized windows"
        text = "<span color='__LKR_ACCENT__'>\uf2d2</span>  <span color='__LKR_PRIMARY__'>%d</span>" % len(wins)
        print(json.dumps({"text": text, "tooltip": tip,
                          "class": "has" if wins else "none"}))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
