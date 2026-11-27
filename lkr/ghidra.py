"""Ghidra first-run fix for a Wayland / Hyprland desktop.

On a non-reparenting Wayland compositor, Ghidra's modal first-run "User Agreement" dialog can come up
as a blank white window you can't click, so the whole app hangs before its main window ever shows --
the classic "Ghidra is just a white screen" symptom. Recording the agreement up front (and silencing
the Tip-of-the-Day and What's-New dialogs, which blank the same way) lets it boot straight to the UI.
This writes exactly what clicking "I Agree" would; it skips a stuck dialog, it does not bypass a
choice you would otherwise get to make differently.
"""
import glob
import os
import shutil

from . import core

# Ghidra keeps per-user settings in ~/.config/ghidra/ghidra_<version>_<release>/preferences,
# a Java .properties file. Each key here turns off a startup modal that otherwise blocks the app.
SETTINGS = os.path.join(core.HOME, ".config", "ghidra")
FIX_PREFS = {
    "USER_AGREEMENT": "ACCEPT",      # the first-run licence dialog that hangs a blank window
    "SHOW_TIPS": "false",            # "Tip of the Day" modal
    "GhidraShowWhatsNew": "false",   # "What's New" modal
}
PROC = "ghidra.GhidraRun"  # the hung java process' command line (its process name is just "java")


def installed():
    return bool(shutil.which("ghidraRun") or shutil.which("ghidra"))


def _settings_dir_from_install():
    """The ghidra_<version>_<release> dir Ghidra will use, read from the install so the fix works
    even before Ghidra has been run once. Returns a path, or None if it can't be determined."""
    run = shutil.which("ghidraRun")
    if not run:
        return None
    props = os.path.join(os.path.dirname(os.path.realpath(run)), "Ghidra", "application.properties")
    version = release = ""
    try:
        with open(props) as f:
            for line in f:
                if line.startswith("application.version="):
                    version = line.split("=", 1)[1].strip()
                elif line.startswith("application.release.name="):
                    release = line.split("=", 1)[1].strip()
    except OSError:
        return None
    if not version:
        return None
    name = "ghidra_%s_%s" % (version, release) if release else "ghidra_%s" % version
    return os.path.join(SETTINGS, name)


def settings_dirs():
    """Every Ghidra settings dir to fix: existing ones, plus the one the installed Ghidra will use."""
    dirs = [d for d in glob.glob(os.path.join(SETTINGS, "ghidra_*")) if os.path.isdir(d)]
    expected = _settings_dir_from_install()
    if expected and expected not in dirs:
        dirs.append(expected)
    return sorted(dirs)


def _load(path):
    props = {}
    try:
        with open(path) as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#") and "=" in s:
                    key, value = s.split("=", 1)
                    props[key] = value
    except OSError:
        pass
    return props


def needs_fix():
    """True when a settings dir is missing one of the fix keys, so the button has work to do."""
    for d in settings_dirs():
        props = _load(os.path.join(d, "preferences"))
        if any(props.get(key) != value for key, value in FIX_PREFS.items()):
            return True
    return False


def running():
    return core._quiet("pgrep", "-f", PROC).strip() != ""


def _patch_preferences(path):
    """Set each fix key in a .properties file, leaving every other line (Theme, comments, the Java
    "\\:" escapes) exactly as it was. Returns the keys that were changed or added."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        lines = ["#User Preferences"]
    changed = []
    for key, value in FIX_PREFS.items():
        target = "%s=%s" % (key, value)
        for i, line in enumerate(lines):
            if line.strip().startswith(key + "="):
                if line != target:
                    lines[i] = target
                    changed.append(key)
                break
        else:
            lines.append(target)
            changed.append(key)
    if changed:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
    return changed


def fix():
    """Close any hung Ghidra (a running one would rewrite preferences on exit and undo this), then
    apply the startup-dialog fixes to every settings dir. Returns a short, human-readable summary."""
    if not installed():
        return "Ghidra is not installed. Tick it under Reverse engineering and install it first."
    was_running = running()
    if was_running:
        core._quiet("pkill", "-9", "-f", PROC)
    touched = [os.path.basename(d) for d in settings_dirs()
               if _patch_preferences(os.path.join(d, "preferences"))]
    if touched:
        note = "Fixed Ghidra: it will now open straight to its main window instead of a blank dialog."
    else:
        note = "Ghidra was already set to skip the blank first-run dialogs."
    if was_running:
        note += " Closed a stuck Ghidra window — start it again."
    return note
