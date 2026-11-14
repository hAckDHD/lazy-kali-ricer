"""Sync between your own devices over Tailscale: your shortcuts, zsh files, tools and browser
extensions. One device can be the master the other copies, or the two can be merged.

The devices talk over SSH on the tailnet, and each side runs its own lazy-kali-ricer. So each device
reads and writes its own system, and decides for itself what fits its architecture: a package that
does not exist there is named and skipped, a compiled program from another architecture is not copied.
Screens, apps and the theme stay per device and are never synced."""
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import types

from . import core, kit

PARTS = {"shortcuts": "Shortcuts", "zsh": "Zsh files", "tools": "Tools and scripts", "extensions": "Browser extensions"}
ZSH_FILES = (".zshrc", ".zshenv", ".zprofile", ".zlogin", ".zlogout", ".zsh_aliases", ".p10k.zsh")  # never the history
REMOTE = "~/.local/bin/lazy-kali-ricer"  # where install.sh puts the command on every device
SHORTCUTS_BACKUP = os.path.join(core.OWN, "shortcuts-before-sync.json")


# ---------- this device ----------
def arch():
    return core._quiet("dpkg", "--print-architecture").strip() or os.uname().machine


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _put(path, data, mode):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, mode & 0o777 or 0o644)


def state():
    """What this device has, as the other one gets to see it. File contents travel separately."""
    s = core.load()
    folder = kit.kit_dir(s)
    found, listed = kit.scan_tools(), kit.read_tools(folder)
    names = {ext_id: name for name, ext_id in kit.extensions(folder)}
    exts = {ext_id: name for browser in kit.installed_browsers() for name, ext_id in kit.installed_extensions(browser)}
    for ext_id in kit.chosen_extensions() | kit.kit_extension_ids(folder):
        exts.setdefault(ext_id, names.get(ext_id, ext_id))
    scripts = [os.path.relpath(os.path.join(root, name), kit.TOOLS_DIR)
               for root, files in kit.own_files(kit.TOOLS_DIR) for name in files
               if os.path.getsize(os.path.join(root, name)) <= kit.BIG]
    return dict(version=1, host=socket.gethostname(), arch=arch(),
                shortcuts=[sc for sc in s["shortcuts"] if sc.get("keys") and sc.get("cmd")],
                zsh={name: hashlib.sha256(data).hexdigest() for name in ZSH_FILES
                     for data in [_read(os.path.join(core.HOME, name))] if data is not None},
                tools={kind: list(dict.fromkeys(found[kind] + listed[kind])) for kind in found},
                scripts=scripts, extensions=exts)


def export(out, parts=()):
    """Write this device's bundle to a binary stream: state.json, then the files of the given parts."""
    st = state()
    with tarfile.open(fileobj=out, mode="w|gz", dereference=True) as tar:
        text = json.dumps(st).encode()
        info = tarfile.TarInfo("state.json")
        info.size = len(text)
        tar.addfile(info, io.BytesIO(text))
        if "zsh" in parts:
            for name in st["zsh"]:
                tar.add(os.path.join(core.HOME, name), "zsh/" + name)
        if "tools" in parts:
            for rel in st["scripts"]:
                tar.add(os.path.join(kit.TOOLS_DIR, rel), "tools/" + rel)


def take(stream, mode, parts, install):
    """Bring another device's bundle into this one. Returns (log lines, True when the shortcuts changed).

    mode "replace": the sender is the master. Its shortcuts and zsh files take the place of ours (ours are
    kept as backups). mode "merge": we only gain what we lack; nothing of ours is changed.
    Tools, scripts and extensions are only ever added, in both modes. With install=False (nobody is at
    this device to type a password) they are noted in the kit and installed later from the GUI."""
    replace, stamp, st = mode == "replace", time.strftime("%Y%m%d-%H%M%S"), None
    zsh = {"added": [], "replaced": [], "aside": []}
    scripts = {"added": 0, "differ": 0, "foreign": []}
    tools_root = os.path.realpath(kit.TOOLS_DIR) + os.sep
    try:
        with tarfile.open(fileobj=stream, mode="r|gz") as tar:
            for member in tar:
                if member.name == "state.json":
                    st = json.load(tar.extractfile(member))
                    if not isinstance(st, dict) or st.get("version") != 1:
                        raise ValueError("unknown bundle version")
                    them = re.sub(r"[^A-Za-z0-9._-]", "-", str(st.get("host") or "other-device"))
                    continue
                kind, _slash, rel = member.name.partition("/")
                if st is None or kind not in parts or not member.isfile() or member.size > kit.BIG:
                    continue
                data = tar.extractfile(member).read()
                if kind == "zsh" and rel in ZSH_FILES:
                    dest = os.path.join(core.HOME, rel)
                    old = _read(dest)
                    if old is None:
                        _put(dest, data, member.mode)
                        zsh["added"].append(rel)
                    elif old != data and replace:
                        os.rename(dest, "%s.bak-%s" % (dest, stamp))
                        _put(dest, data, member.mode)
                        zsh["replaced"].append(rel)
                    elif old != data:
                        _put("%s.from-%s" % (dest, them), data, member.mode)
                        zsh["aside"].append(rel)
                elif kind == "tools":
                    dest = os.path.realpath(os.path.join(kit.TOOLS_DIR, rel))
                    if not dest.startswith(tools_root):
                        continue  # a path that would land outside ~/tools
                    if data[:4] == b"\x7fELF" and st.get("arch") != arch():
                        scripts["foreign"].append(rel)
                    elif os.path.exists(dest):
                        scripts["differ"] += _read(dest) != data
                    else:
                        _put(dest, data, member.mode)
                        scripts["added"] += 1
    except (tarfile.TarError, EOFError, ValueError, KeyError) as e:
        raise ValueError("That is not a lazy-kali-ricer sync bundle (%s)." % e) from e
    if st is None:
        raise ValueError("That is not a lazy-kali-ricer sync bundle.")

    log, changed, s = [], False, core.load()
    folder, today = kit.kit_dir(s), time.strftime("%Y-%m-%d")
    if "shortcuts" in parts:
        theirs = [dict(keys=str(sc["keys"]), cmd=str(sc["cmd"]), desc=str(sc.get("desc", "")))
                  for sc in st.get("shortcuts", []) if isinstance(sc, dict) and sc.get("keys") and sc.get("cmd")]
        mine = s["shortcuts"]
        have = {sc["keys"]: sc["cmd"] for sc in mine}
        clash = [sc["keys"] for sc in theirs if have.get(sc["keys"], sc["cmd"]) != sc["cmd"]]
        new = theirs if replace else mine + [sc for sc in theirs if sc["keys"] not in have]
        if new == mine:
            log.append("Shortcuts: already the same." if not clash else "Shortcuts: nothing new.")
        else:
            if replace and mine:
                with open(SHORTCUTS_BACKUP, "w") as f:
                    json.dump(mine, f, indent=2)
            s["shortcuts"], changed = new, True
            core.save(s)
            log.append("Shortcuts: now %d (were %d)%s." % (len(new), len(mine), ", the old ones are in %s"
                       % SHORTCUTS_BACKUP.replace(core.HOME, "~") if replace and mine else ""))
        if clash and not replace:
            log.append("Shortcuts: %s mean something else on %s; yours were kept." % (", ".join(clash), them))
    if "zsh" in parts:
        for names, text in ((zsh["added"], "added %s"), (zsh["replaced"], "replaced %%s (old kept as <name>.bak-%s)" % stamp),
                            (zsh["aside"], "%%s differ: yours untouched, the other one is next to it as <name>.from-%s" % them)):
            if names:
                log.append("Zsh files: " + text % ", ".join(names) + ".")
        if not any(zsh.values()):
            log.append("Zsh files: already the same.")
    if "tools" in parts:
        fits = {"apt": kit.PACKAGE.match, "pipx": kit.PACKAGE.match, "git": lambda item: item.startswith("https://")}
        added = {}
        for kind, ok in fits.items():
            items = [i for i in (st.get("tools") or {}).get(kind, []) if isinstance(i, str) and ok(i)]
            added[kind] = len(kit.add_tools(folder, kind, items, "" if any(added.values()) else "From %s, %s" % (them, today)))
        log.append("Tools: %d packages, %d pipx tools, %d git repositories new in tools.txt."
                   % (added["apt"], added["pipx"], added["git"]) if any(added.values()) else "Tools: nothing new.")
        if install:
            log += kit.install_tools(folder)
        elif any(added.values()):
            log.append("Tools: install them on this device from the Browsers & tools tab.")
        log.append("Scripts: %d added to ~/tools%s%s." % (
            scripts["added"], ", %d differ and were left alone" % scripts["differ"] if scripts["differ"] else "",
            ", %d compiled for %s not copied (%s)" % (len(scripts["foreign"]), st.get("arch"), ", ".join(
                scripts["foreign"][:4] + ["…"] * (len(scripts["foreign"]) > 4))) if scripts["foreign"] else ""))
    if "extensions" in parts:
        theirs = {i: str(n) for i, n in (st.get("extensions") or {}).items() if kit.EXT_ID.match(str(i))}
        new = kit.add_extensions(folder, [(name, ext_id) for ext_id, name in theirs.items()])
        browsers, now = kit.installed_browsers(), kit.chosen_extensions()
        have = now | {ext_id for browser in browsers for _name, ext_id in kit.installed_extensions(browser)}
        want = set(theirs) if replace else now | set(theirs)
        if not install:
            log.append("Extensions: %d new in extensions.txt%s." % (
                len(new), "; set them on this device from the Browsers & tools tab" if new else ""))
        elif not browsers:
            log.append("Extensions: no Brave, Chromium or Chrome here to give them to.")
        elif want == now or (not replace and set(theirs) <= have):
            log.append("Extensions: already the same.")
        else:
            log.append("Extensions: %d set in %s. Restart the browser to get them." % (len(want), ", ".join(browsers))
                       if kit.set_extensions(browsers, sorted(want)) else "Extensions: NOT set (cancelled or failed).")
    return log, changed


# ---------- the other device ----------
def problem():
    """"" when Tailscale is up, else what to do about it."""
    if not shutil.which("tailscale"):
        return "Tailscale is not installed. Install it (tailscale.com/download/linux), then run: sudo tailscale up"
    try:
        running = json.loads(core._quiet("tailscale", "status", "--json") or "{}").get("BackendState") == "Running"
    except ValueError:
        running = False
    return "" if running else "Tailscale is not connected. Run: sudo tailscale up"


def _status():
    try:
        return json.loads(core._quiet("tailscale", "status", "--json") or "{}")
    except ValueError:
        return {}


def devices():
    """Your other Linux devices on the tailnet: [{"name", "ip", "online"}]."""
    found = [dict(name=peer.get("HostName") or peer["TailscaleIPs"][0], ip=peer["TailscaleIPs"][0],
                  online=bool(peer.get("Online")))
             for peer in (_status().get("Peer") or {}).values() if peer.get("OS") == "linux" and peer.get("TailscaleIPs")]
    return sorted(found, key=lambda d: d["name"].lower())


def reachable():
    """True when this device lets your other devices in: Tailscale SSH is on."""
    return bool((_status().get("Self") or {}).get("sshHostKeys"))


def allow(on):
    """Switch Tailscale SSH on this device on or off. Needs the admin password. Returns True when it worked."""
    return core._root("tailscale set --ssh=%s" % ("true" if on else "false"))


def default_user():
    return core.load().get("sync_user") or getpass.getuser()


def remember_user(user):
    """Keep the user name to sign in with on other devices, when it is not the one used here."""
    s = core.load()
    if user != (s.get("sync_user") or getpass.getuser()):
        s["sync_user"] = user
        core.save(s)


def _remote(dev, user, command, ui, stdin=None):
    """Run `lazy-kali-ricer sync <command>` on another device.
    Returns (problem or "", what it printed as a binary file)."""
    out = tempfile.TemporaryFile()
    try:
        ssh = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
                                "%s@%s" % (user, dev["ip"]), "%s sync %s" % (REMOTE, command)],
                               stdin=stdin or subprocess.DEVNULL, stdout=out, stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return "Could not start ssh: %s" % e, out
    said = []
    for line in ssh.stderr:
        said.append(line.strip())
        url = re.search(r"https://login\.tailscale\.com/\S+", line)  # Tailscale SSH wants a fresh sign-in
        if url:
            ui.say("Tailscale asks you to confirm it is you. Your browser opens; this continues once you did.")
            ui.open(url.group())
    code, tail = ssh.wait(), "\n".join([line for line in said if line][-3:])
    out.seek(0)
    name = dev["name"]
    if code == 255:
        return ("Could not reach %s as %s. On %s, open lazy-kali-ricer, go to Sync devices and press "
                "\"Let my other devices reach this one\". If your user name is different there, set it above.\n%s"
                % (name, user, name, tail)), out
    if code == 127 or "No such file" in tail:
        return "lazy-kali-ricer is not installed on %s (looked for %s)." % (name, REMOTE), out
    if code and "Open the GUI." in "\n".join(said):
        return "%s has an older lazy-kali-ricer without sync. Update it there first." % name, out
    return ("%s answered with an error:\n%s" % (name, tail) if code else ""), out


def _state_of(bundle):
    with tarfile.open(fileobj=bundle, mode="r|gz") as tar:
        st = json.load(tar.extractfile(tar.next()))
    if st.get("version") != 1:
        raise ValueError("unknown bundle version")
    return st


def differences(mine, theirs):
    """Lines that say what each device has and the other lacks."""
    there = theirs["host"]
    sides = lambda a, b: "%d only here, %d only on %s" % (len(set(a) - set(b)), len(set(b) - set(a)), there)
    a, b = ({sc["keys"]: sc["cmd"] for sc in st["shortcuts"]} for st in (mine, theirs))
    lines = ["Shortcuts: %s, %d with the same keys but another command." % (sides(a, b), sum(a[k] != b[k] for k in set(a) & set(b)))]
    a, b = mine["zsh"], theirs["zsh"]
    words = ["%s %s" % (name, "the same" if a.get(name) == b.get(name) else "differs" if name in a and name in b else
                        "only here" if name in a else "only on " + there) for name in sorted(set(a) | set(b))]
    lines.append("Zsh files: %s." % (", ".join(words) or "none on either device"))
    lines.append("Tools: " + "; ".join("%s %s" % (label, sides(mine[top][kind] if kind else mine[top], theirs[top][kind] if kind else theirs[top]))
                                       for label, top, kind in (("packages", "tools", "apt"), ("pipx tools", "tools", "pipx"),
                                                                ("git repositories", "tools", "git"), ("your scripts", "scripts", ""))) + ".")
    a, b = mine["extensions"], theirs["extensions"]
    lines.append("Browser extensions: %s." % sides(a, b))
    for label, ids, names in (("Only here", set(a) - set(b), a), ("Only on " + there, set(b) - set(a), b)):
        if ids:
            lines.append("    %s: %s" % (label, ", ".join(sorted(names[i] for i in ids))))
    if mine["arch"] != theirs["arch"]:
        lines.append("This device is %s, %s is %s. Packages that do not exist for a device are named and skipped there; "
                     "compiled programs in ~/tools are not copied." % (mine["arch"], there, theirs["arch"]))
    return lines


def sync(dev, user, mode, parts, ui):
    """mode "compare": only look. "from": the other device is the master, this one copies it.
    "to": this device is the master. "merge": each gains what only the other has.
    Returns (lines to show, True when this device's shortcuts changed)."""
    name, wanted, lines, changed, ours = dev["name"], ",".join(parts), [], False, None
    try:
        if mode == "compare":
            ui.say("Reading %s …" % name)
            failed, theirs = _remote(dev, user, "export", ui)
            return ([failed] if failed else differences(state(), _state_of(theirs))), False
        if mode in ("to", "merge"):  # ours as it is now, before anything from the other device lands
            ours = tempfile.TemporaryFile()
            export(ours, parts)
            ours.seek(0)
        if mode in ("from", "merge"):
            ui.say("Reading %s …" % name)
            failed, theirs = _remote(dev, user, "export " + wanted, ui)
            if failed:
                return [failed], False
            ui.say("Bringing it into this device … (enter your password in the dialog if it asks)")
            log, changed = take(theirs, "replace" if mode == "from" else "merge", parts, install=True)
            lines += ["This device:"] + ["    " + line for line in log]
        if ours:
            ui.say("Sending to %s …" % name)
            failed, out = _remote(dev, user, "import %s %s" % ("replace" if mode == "to" else "merge", wanted), ui, stdin=ours)
            lines += [name + ":"] + ["    " + line for line in (failed or out.read().decode(errors="replace")).splitlines()]
    except (OSError, ValueError) as e:
        lines.append("Failed: %s" % e)
    return lines, changed


# ---------- command line ----------
def command(args):
    """`lazy-kali-ricer sync ...`. Returns False when the arguments make no sense."""
    ui = types.SimpleNamespace(say=lambda text: print(text, file=sys.stderr), open=lambda url: print(url, file=sys.stderr))
    known = lambda text: [p for p in text.split(",") if p in PARTS]
    if args[:1] == ["export"] and len(args) <= 2:  # run by the other device, over SSH
        export(sys.stdout.buffer, known(args[1]) if len(args) == 2 else ())
    elif len(args) == 3 and args[0] == "import" and args[1] in ("replace", "merge"):  # likewise
        try:
            log, changed = take(sys.stdin.buffer, args[1], known(args[2]), install=False)
        except (OSError, ValueError) as e:
            sys.exit(str(e))
        if changed:
            core.apply(core.load())
            core._quiet("hyprctl", "-i", "0", "reload")  # a session running on this device picks them up now
        print("\n".join(log))
    elif args == ["devices"]:
        print(problem() or "\n".join("%-24s %s%s" % (d["name"], d["ip"], "" if d["online"] else "  offline")
                                     for d in devices()) or "No other Linux device on your tailnet.")
    elif len(args) == 2 and args[0] in ("compare", "from", "to", "merge"):
        user, _at, name = args[1].rpartition("@")
        dev = next((d for d in devices() if name in (d["name"], d["ip"])), None)
        if not dev:
            sys.exit(problem() or "No device called %s on your tailnet. See: lazy-kali-ricer sync devices" % name)
        lines, changed = sync(dev, user or default_user(), args[0], list(PARTS), ui)
        if changed:
            lines += core.apply(core.load())[-1:]
        print("\n".join(lines))
    else:
        return False
    return True
