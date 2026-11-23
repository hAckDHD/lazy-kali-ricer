"""Files and folders from another of your devices, over Tailscale.

You choose folders in your home folder (the same path on both devices) and, if you like, file types.
Files that only the other device has are copied here. Files both have, with other content, are yours to
decide, one by one or all at once: keep yours, take theirs (yours is kept as a backup), or keep both
(theirs lands next to yours as "<name> (from <device>).<ext>"). Nothing is deleted on either device,
and the other device is only read."""
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import time

from . import core, sync

TYPES = {"Documents": "pdf doc docx odt rtf txt md csv xls xlsx ods ppt pptx odp epub",
         "Images": "jpg jpeg png gif webp svg bmp tif tiff heic",
         "Video": "mp4 mkv webm mov avi m4v",
         "Audio": "mp3 flac ogg opus wav m4a aac",
         "Code": "py sh js ts c h cpp hpp rs go java rb php html css json yaml yml toml ini conf",
         "Archives": "zip tar gz tgz xz bz2 7z rar zst iso"}
# Never walked into: rebuilt by tools, huge, or (.git) unsafe to merge file by file.
SKIP = {".git", "node_modules", "__pycache__", ".cache", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache"}
CHOICES = {"mine": "Keep yours", "theirs": "Take theirs", "both": "Keep both"}
VERBS = {"new": "New", "theirs": "Took theirs", "both": "Kept both", "already": "Already there"}
COMMANDS = ("files", "folders", "files-dirs", "files-list", "files-hash", "files-get")


class Trouble(Exception):
    """The other device could not be asked, or answered something unexpected."""


def backups():
    return os.path.join(core.HOME, ".local", "share", "lazy-kali-ricer", "file-backups")


# ---------- paths and types ----------
def inside(rel):
    """The full path of rel in the home folder, or None when rel would land outside it."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\0" in rel:
        return None
    home = os.path.normpath(core.HOME)
    path = os.path.normpath(os.path.join(home, rel))
    return path if path.startswith(home + os.sep) else None


def clean_folders(folders):
    """Folders as paths in the home folder, without repeats or ones inside another chosen one."""
    out = []
    for rel in sorted({os.path.relpath(p, core.HOME) for p in map(inside, folders) if p}):
        if not any(rel.startswith(top + "/") for top in out):
            out.append(rel)
    return out


def parse_types(text):
    """"pdf, .JPG tar.gz" -> ["pdf", "jpg", "tar.gz"]."""
    return list(dict.fromkeys(t.lower().lstrip(".") for t in re.split(r"[\s,;]+", text or "")
                              if re.fullmatch(r"\.?[A-Za-z0-9+_-]+(\.[A-Za-z0-9+_-]+)*", t)))


def wanted(name, types):
    low = name.lower()
    return not types or any(low.endswith("." + t) for t in types)


def size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024


def when(mtime):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))


def newer(conflict):
    """The side whose copy changed last."""
    return "theirs" if conflict["there"][1] > conflict["here"][1] else "mine"


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- this device, as the other one sees it ----------
def folders_here(depth=2, limit=3000):
    """The folders in the home folder, two levels deep, without hidden ones: what there is to choose from."""
    out = []

    def walk(path, rel, level):
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            if len(out) >= limit:
                return
            try:
                if e.name.startswith(".") or e.name in SKIP or not e.is_dir(follow_symlinks=level == 0):
                    continue
            except OSError:
                continue
            here = os.path.join(rel, e.name) if rel else e.name
            out.append(here)
            if level + 1 < depth:
                walk(e.path, here, level + 1)

    walk(core.HOME, "", 0)
    return out


def listing(folders, types):
    """{path in home: [bytes, modified]} for the regular files in those folders with those types."""
    found = {}
    for top in clean_folders(folders):
        start = inside(top)
        if not os.path.isdir(start):
            continue
        for root, dirs, names in os.walk(start):
            dirs[:] = [d for d in dirs if d not in SKIP]
            for name in names:
                if not wanted(name, types):
                    continue
                path = os.path.join(root, name)
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    found[os.path.relpath(path, core.HOME)] = [st.st_size, int(st.st_mtime)]
    return found


def hashes(paths):
    out = {}
    for rel in paths:
        path = inside(rel)
        try:
            if path and stat.S_ISREG(os.lstat(path).st_mode):
                out[rel] = _sha(path)
        except OSError:
            pass
    return out


def bundle(paths, out):
    """Write those files to a binary stream as a tar.gz."""
    with tarfile.open(fileobj=out, mode="w|gz") as tar:
        for rel in paths:
            path = inside(rel)
            try:
                if path and stat.S_ISREG(os.lstat(path).st_mode):
                    tar.add(path, rel, recursive=False)
            except OSError:
                pass  # gone or unreadable since it was listed: the other side reports it as not sent


# ---------- the other device ----------
def _ask(dev, user, what, ui, data=None):
    """Run `lazy-kali-ricer sync <what>` there with data as JSON on its input. Returns its output as a file."""
    stdin = None
    if data is not None:
        stdin = tempfile.TemporaryFile()
        stdin.write(json.dumps(data).encode())
        stdin.seek(0)
    failed, out = sync._remote(dev, user, what, ui, stdin=stdin)
    if failed:
        raise Trouble(failed)
    return out


def _json(dev, user, what, ui, data=None, kind=dict):
    try:
        answer = json.load(_ask(dev, user, what, ui, data))
    except ValueError as e:
        raise Trouble("%s sent something unexpected (%s)." % (dev["name"], e)) from e
    if not isinstance(answer, kind):
        raise Trouble("%s sent something unexpected." % dev["name"])
    return answer


def remote_folders(dev, user, ui):
    return [rel for rel in _json(dev, user, "files-dirs", ui, kind=list) if inside(rel)]


def plan(dev, user, folders, types, ui):
    """Compare the chosen folders on both devices. Returns
    {"device", "new": [(path, bytes, modified)], "conflicts": [{"path", "here", "there"}], "same", "only_here", "skipped"}.
    here and there are (bytes, modified)."""
    folders = clean_folders(folders)
    ui.say("Listing the files on %s …" % dev["name"])
    theirs = _json(dev, user, "files-list", ui, {"folders": folders, "types": types})
    ui.say("Listing the files here …")
    mine = listing(folders, types)
    new, maybe, same, skipped = [], [], 0, []
    for rel, info in sorted(theirs.items()):
        path = inside(rel)
        if not (path and isinstance(info, list) and len(info) == 2 and all(isinstance(n, int) for n in info)
                and any(rel.startswith(top + "/") for top in folders)):
            continue  # not something we asked for
        if rel in mine:
            same += mine[rel] == info
            if mine[rel] != info:
                maybe.append(rel)
        elif os.path.lexists(path):
            skipped.append(rel)  # a folder or a link here where the other device has a file
        else:
            new.append((rel, info[0], info[1]))
    check = [rel for rel in maybe if mine[rel][0] == theirs[rel][0]]  # same size: maybe only the date differs
    if check:
        ui.say("Checking %d files that may only differ in date …" % len(check))
        there_h, here_h = _json(dev, user, "files-hash", ui, check), hashes(check)
        alike = {rel for rel in check if here_h.get(rel) and here_h.get(rel) == there_h.get(rel)}
        same += len(alike)
        maybe = [rel for rel in maybe if rel not in alike]
    return dict(device=dev["name"], new=new, same=same, skipped=skipped,
                conflicts=[dict(path=rel, here=tuple(mine[rel]), there=tuple(theirs[rel])) for rel in maybe],
                only_here=len(set(mine) - set(theirs)))


def _aside(dest, there, tmp):
    """Where theirs goes when both are kept: "<name> (from <device>).<ext>", numbered when that name holds
    something else. None when a copy with the same content as tmp is already there."""
    folder, name = os.path.split(dest)
    stem, ext = os.path.splitext(name)
    for n in range(1, 1000):
        path = os.path.join(folder, "%s (from %s%s)%s" % (stem, there, "" if n == 1 else " %d" % n, ext))
        if not os.path.lexists(path):
            return path
        if os.path.isfile(path) and not os.path.islink(path) and _sha(path) == _sha(tmp):
            return None
    raise OSError("too many copies of %s" % name)


def fetch(dev, user, the_plan, choices, ui, say):
    """Copy the new files here, and for each conflict do what choices[path] says ("mine", "theirs", "both").
    say(text, part) reports progress. Returns the lines to show."""
    there = re.sub(r"[^A-Za-z0-9._-]", "-", dev["name"])
    get = {rel: "new" for rel, _s, _m in the_plan["new"]}
    get.update({rel: how for rel, how in choices.items() if how in ("theirs", "both")})
    sizes = {rel: n for rel, n, _m in the_plan["new"]}
    sizes.update({c["path"]: c["there"][0] for c in the_plan["conflicts"]})
    total, moved = sum(sizes.get(rel, 0) for rel in get) or 1, 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(backups(), stamp)
    done = {"new": 0, "theirs": 0, "both": 0, "already": 0}
    failed = []
    if get:
        say("Getting %d files (%s) from %s …" % (len(get), size(total), dev["name"]), 0.0)
        stream = _ask(dev, user, "files-get", ui, sorted(get))
        try:
            with tarfile.open(fileobj=stream, mode="r|gz") as tar:
                for member in tar:
                    how = get.pop(member.name, None)
                    if not how or not member.isfile():
                        continue
                    dest, tmp = inside(member.name), None
                    try:
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with tempfile.NamedTemporaryFile(dir=os.path.dirname(dest), prefix=".lkr-", delete=False) as f:
                            tmp = f.name
                            shutil.copyfileobj(tar.extractfile(member), f, 1 << 20)
                        os.chmod(tmp, member.mode & 0o777 or 0o644)
                        os.utime(tmp, (member.mtime, member.mtime))
                        if how == "new" and os.path.lexists(dest):
                            how = "both"  # appeared here since the comparison: do not overwrite it
                        if how == "both":
                            aside = _aside(dest, there, tmp)
                            if aside:
                                os.rename(tmp, aside)
                            else:
                                os.unlink(tmp)
                                how = "already"
                        elif how == "theirs":
                            keep = os.path.join(backup, member.name)
                            os.makedirs(os.path.dirname(keep), exist_ok=True)
                            shutil.move(dest, keep)
                            os.rename(tmp, dest)
                        else:
                            os.rename(tmp, dest)
                        done[how] += 1
                    except OSError as e:
                        failed.append("%s (%s)" % (member.name, e.strerror or e))
                        if tmp and os.path.exists(tmp):
                            os.unlink(tmp)
                    moved += member.size
                    say("%s  %s" % (VERBS.get(how, "Copied"), member.name), min(moved / total, 0.99))
        except (tarfile.TarError, EOFError) as e:
            failed.append("the transfer broke off (%s)" % e)
    home = lambda path: path.replace(core.HOME, "~", 1)
    kept = sum(how == "mine" for how in choices.values())
    lines = ["From %s:" % dev["name"]]
    for count, text in ((done["new"], "%d new files copied here."),
                        (done["theirs"], "%%d replaced with theirs; yours are kept in %s." % home(backup)),
                        (done["both"], "%%d kept both: theirs is next to yours as \"<name> (from %s).<ext>\"." % there),
                        (done["already"], "%d kept both: their copy was already next to yours."),
                        (kept, "%d kept yours."),
                        (the_plan["same"], "%d already the same."),
                        (the_plan["only_here"], "%d only on this device, left alone.")):
        if count:
            lines.append("    " + text % count)
    if the_plan["skipped"]:
        lines.append("    %d skipped: a folder or link here has that name (%s)." % (
            len(the_plan["skipped"]), ", ".join(the_plan["skipped"][:3]) + ", …" * (len(the_plan["skipped"]) > 3)))
    if get:
        failed += ["%s (no longer on %s)" % (rel, dev["name"]) for rel in sorted(get)]
    if failed:
        lines.append("    Failed: %d not copied: %s" % (len(failed), "; ".join(failed[:5]) + "; …" * (len(failed) > 5)))
    if len(lines) == 1:
        lines.append("    Nothing to do.")
    return lines


# ---------- what you chose last time ----------
def saved():
    s = core.load().get("sync_files") or {}
    return list(s.get("folders") or []), list(s.get("types") or [])


def remember(folders=None, types=None):
    s = core.load()
    keep = dict(s.get("sync_files") or {})
    if folders is not None:
        keep["folders"] = clean_folders(folders)
    if types is not None:
        keep["types"] = list(types)
    s["sync_files"] = keep
    core.save(s)


# ---------- command line ----------
def _ask_conflicts(conflicts, there):
    """Ask in the terminal what to do about each conflict. Returns {path: choice}."""
    print("\n%d files differ on the two devices." % len(conflicts))
    keys = {"k": "mine", "t": "theirs", "b": "both", "n": "newer"}
    choices, rest = {}, None
    for c in conflicts:
        how = rest
        if not how:
            mark = newer(c)
            print("\n  %s\n    here:      %s, %s%s\n    %-10s %s, %s%s" % (
                c["path"], size(c["here"][0]), when(c["here"][1]), "  (newer)" * (mark == "mine"),
                there + ":", size(c["there"][0]), when(c["there"][1]), "  (newer)" * (mark == "theirs")))
            try:
                answer = input("  [k]eep yours, [t]ake theirs, keep [b]oth, [n]ewer wins "
                               "(capital letter: this one and all the rest) [k]: ").strip()
            except EOFError:
                answer = ""
            how = keys.get(answer.lower(), "mine")
            if answer[:1].isupper():
                rest = how
        choices[c["path"]] = newer(c) if how == "newer" else how
    return choices


def command(args):
    """`lazy-kali-ricer sync files|folders ...`, and the commands the other device runs over SSH.
    Returns False when the arguments make no sense."""
    read = lambda: json.load(sys.stdin.buffer)
    if args == ["files-dirs"]:
        print(json.dumps(folders_here()))
    elif args == ["files-list"]:
        req = read()
        print(json.dumps(listing(req.get("folders") or [], req.get("types") or [])))
    elif args == ["files-hash"]:
        print(json.dumps(hashes(read())))
    elif args == ["files-get"]:
        bundle(read(), sys.stdout.buffer)
    elif args[:1] == ["folders"] and len(args) == 2:
        dev, user = sync.find(args[1])
        try:
            print("\n".join(remote_folders(dev, user, sync.CLI)) or "No folders on %s." % dev["name"])
        except Trouble as e:
            sys.exit(str(e))
    elif args[:1] == ["files"] and len(args) >= 2:
        rest, types = args[2:], None
        if "--types" in rest:
            at = rest.index("--types")
            types, rest = parse_types(" ".join(rest[at + 1:at + 2])), rest[:at] + rest[at + 2:]
        dev, user = sync.find(args[1])
        folders, old_types = saved()
        folders = clean_folders(rest) if rest else folders
        types = old_types if types is None else types
        if not folders:
            sys.exit("Which folders? For example: lazy-kali-ricer sync files %s Documents Pictures\n"
                     "See what is there: lazy-kali-ricer sync folders %s" % (args[1], args[1]))
        remember(folders, types)
        print("Folders: %s. Types: %s." % (", ".join(folders), ", ".join(types) or "all"), file=sys.stderr)
        try:
            the_plan = plan(dev, user, folders, types, sync.CLI)
            choices = _ask_conflicts(the_plan["conflicts"], dev["name"]) if the_plan["conflicts"] else {}
            print("\n".join(fetch(dev, user, the_plan, choices, sync.CLI, lambda text, part=None: print(text, file=sys.stderr))))
        except Trouble as e:
            sys.exit(str(e))
    else:
        return False
    return True
