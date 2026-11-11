#!/usr/bin/env python3
"""wofi menus for Hyprland.

  menus.py clipboard   Clipboard history (cliphist); images show a thumbnail.
                       Enter copies the chosen entry back to the clipboard.
  menus.py keys        Searchable list of every keybinding and what it does.
  menus.py keys login  The same list, shown at login when that option is on. Its first
                       row turns the option off.
"""
import json
import os
import re
import subprocess
import sys

THUMBS = os.path.expanduser("~/.cache/cliphist-thumbs")
IMAGE = re.compile(r"^\[\[ binary data (.+?) (png|jpe?g|bmp|gif|webp) (\d+x\d+) \]\]$")
MODS = ((64, "SUPER"), (4, "CTRL"), (8, "ALT"), (1, "SHIFT"))
SETTINGS = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "lazy-kali-ricer", "settings.json")
NO_LOGIN = "Don't show this list on next login"


def wofi(lines, prompt, *extra):
    if subprocess.run(["pgrep", "-x", "wofi"], capture_output=True).returncode == 0:
        return ""
    return subprocess.run(
        ["wofi", "--dmenu", "--prompt", prompt, "--cache-file", "/dev/null", *extra],
        input="\n".join(lines), capture_output=True, text=True,
    ).stdout.rstrip("\n")


def clipboard():
    raw = subprocess.run(["cliphist", "list"], capture_output=True).stdout.decode(errors="replace")
    os.makedirs(THUMBS, exist_ok=True)
    entries, keep = [], set()  # (shown line, label, cliphist line)
    for line in raw.splitlines():
        ident, _, text = line.partition("\t")
        m = IMAGE.match(text)
        if m:
            thumb = os.path.join(THUMBS, "%s.%s" % (ident, m.group(2)))
            keep.add(os.path.basename(thumb))
            if not os.path.exists(thumb):
                with open(thumb, "wb") as f:
                    subprocess.run(["cliphist", "decode"], input=line.encode(), stdout=f)
            label = "Image  %s  (%s)  #%s" % (m.group(3), m.group(1), ident)
            entries.append(("img:%s:text:%s" % (thumb, label), label, line))
        else:
            label = " ".join(text.split())[:140] or "(blank)"
            entries.append((label, label, line))
    for name in os.listdir(THUMBS):  # drop thumbnails of entries that left the history
        if name not in keep:
            os.remove(os.path.join(THUMBS, name))
    if not entries:
        return
    out = wofi([e[0] for e in entries], "Clipboard", "--allow-images",
               "--define", "image_size=140", "--width", "900", "--height", "720")
    for shown, label, line in entries:
        if out and (out == shown or out == label or out.endswith(":text:" + label)):
            data = subprocess.run(["cliphist", "decode"], input=line.encode(), capture_output=True).stdout
            subprocess.run(["wl-copy"], input=data)
            return


def keys(login=False):
    if login:
        try:
            with open(SETTINGS) as f:
                settings = json.load(f)
        except (OSError, ValueError):
            return
        if not settings.get("keys_on_login"):
            return
    binds = json.loads(subprocess.run(["hyprctl", "binds", "-j"], capture_output=True, text=True).stdout)
    rows = []
    for b in binds:
        if not b.get("description"):
            continue
        key = {"mouse:272": "Left-drag", "mouse:273": "Right-drag"}.get(b["key"], b["key"])
        combo = " + ".join([name for bit, name in MODS if b["modmask"] & bit] + [key])
        rows.append("%-28s %s" % (combo, b["description"]))
    if login:
        rows.insert(0, NO_LOGIN)
    if wofi(rows, "Keybindings", "--width", "900", "--height", "760") == NO_LOGIN:
        settings["keys_on_login"] = False
        with open(SETTINGS, "w") as f:
            json.dump(settings, f, indent=2)


if __name__ == "__main__":
    {"clipboard": clipboard, "keys": lambda: keys(login=sys.argv[2:] == ["login"])}.get(
        sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
