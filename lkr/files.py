"""Files and folders from another of your devices, over Tailscale.

You choose folders in your home folder (the same path on both devices) and, if you like, file types.
Files that only the other device has are copied here. Files both have, with other content, are yours to
decide, one by one or all at once: keep yours, take theirs (yours is kept as a backup), or keep both
(theirs lands next to yours as "<name> (from <device>).<ext>"). Nothing is deleted on either device,
and the other device is only read."""
import hashlib
import io
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
SUMS = ".lkr-sha256/"  # in the transfer: the checksum that follows each file
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


class _Hashing:
    """Reads exactly `size` bytes of a file for tar, hashing them as they go. A file that shrank while it
    was sent is padded with zeros and marked changed, so the stream stays intact and the receiver drops it."""

    def __init__(self, f, size):
        self.f, self.left, self.sha, self.changed = f, size, hashlib.sha256(), False

    def read(self, n=-1):
        n = self.left if n < 0 else min(n, self.left)
        data = self.f.read(n)
        if len(data) < n:
            self.changed, data = True, data + b"\0" * (n - len(data))
        self.left -= n
        self.sha.update(data)
        return data


def bundle(paths, out):
    """Write those files to a binary stream as a tar, each followed by its SHA-256 (".lkr-sha256/<path>"),
    worked out while the file was read, so the receiver can check every file without reading any twice."""
    with tarfile.open(fileobj=out, mode="w|", bufsize=1 << 20) as tar:
        for rel in paths:
            path = inside(rel)
            try:
                if not path or not stat.S_ISREG(os.lstat(path).st_mode):
                    continue
                f = open(path, "rb")
            except OSError:
                continue  # gone or unreadable since it was listed: the receiver reports it as not sent
            with f:
                info = tar.gettarinfo(arcname=rel, fileobj=f)
                src = _Hashing(f, info.size)
                tar.addfile(info, src)
                grew = os.fstat(f.fileno()).st_size != info.size
            digest = ("changed" if src.changed or grew else src.sha.hexdigest()).encode()
            mark = tarfile.TarInfo(SUMS + rel)
            mark.size = len(digest)
            tar.addfile(mark, io.BytesIO(digest))
        out.flush()


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


def _duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "%d s" % seconds
    if seconds < 3600:
        return "%d min" % (seconds // 60)
    return "%d h %d min" % (seconds // 3600, seconds % 3600 // 60)


class _Meter:
    """Files and bytes so far, the speed over the last few seconds and the time left, at most 4 times a second."""

    def __init__(self, files, total, say):
        self.files, self.total, self.say = files, max(total, 1), say
        self.count = self.moved = 0
        self.start = time.monotonic()
        self.recent, self.last = [(self.start, 0)], 0.0

    def speed(self):
        now = time.monotonic()
        self.recent = [(t, b) for t, b in self.recent if now - t <= 5] + [(now, self.moved)]
        t0, b0 = self.recent[0]
        return (self.moved - b0) / (now - t0) if now - t0 > 0.5 else self.moved / max(now - self.start, 0.001)

    def tick(self, name, force=False):
        now = time.monotonic()
        if not force and now - self.last < 0.25:
            return
        self.last = now
        rate = self.speed()
        left = "about %s left" % _duration((self.total - self.moved) / rate) if rate > 0 and self.moved else "starting"
        self.say("File %s of %s  ·  %s of %s  ·  %s/s  ·  %s\n%s" % (
            "{:,}".format(min(self.count + 1, self.files)), "{:,}".format(self.files), size(self.moved), size(self.total),
            size(rate), left, name), min(self.moved / self.total, 0.999), True)

    def summary(self):
        took = time.monotonic() - self.start
        return "%s in %s (%s/s on average)" % (size(self.moved), _duration(took), size(self.moved / max(took, 0.001)))


def _place(dest, tmp, how, there, keep):
    """Put a received, checked file where it belongs. Returns what was done: "new", "theirs", "both" or "already"."""
    if how == "new" and os.path.lexists(dest):
        how = "both"  # appeared here since the comparison: do not overwrite it
    if how == "both":
        aside = _aside(dest, there, tmp)
        if not aside:
            os.unlink(tmp)
            return "already"
        os.rename(tmp, aside)
    elif how == "theirs":
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        shutil.move(dest, keep)
        os.rename(tmp, dest)
    else:
        os.rename(tmp, dest)
    return how


def fetch(dev, user, the_plan, choices, ui, say, stop=None):
    """Copy the new files here, and for each conflict do what choices[path] says ("mine", "theirs", "both").
    Files are written as they arrive and each is checked against the SHA-256 the other device worked out
    while reading it; a file that does not match is not kept. say(text, part, live) reports progress;
    setting the threading.Event `stop` ends it early. Returns the lines to show."""
    there = re.sub(r"[^A-Za-z0-9._-]", "-", dev["name"])
    get = {rel: "new" for rel, _s, _m in the_plan["new"]}
    get.update({rel: how for rel, how in choices.items() if how in ("theirs", "both")})
    sizes = {rel: n for rel, n, _m in the_plan["new"]}
    sizes.update({c["path"]: c["there"][0] for c in the_plan["conflicts"]})
    total = sum(sizes.get(rel, 0) for rel in get)
    backup = os.path.join(backups(), time.strftime("%Y%m%d-%H%M%S"))
    done = {"new": 0, "theirs": 0, "both": 0, "already": 0}
    failed, mismatched, changed, broke, wanted_count = [], [], [], "", len(get)
    meter = _Meter(len(get), total, say)
    if get:
        free = shutil.disk_usage(core.HOME).free
        if free < total + (1 << 30):
            return ["From %s:" % dev["name"], "    Failed: not enough space here: %s to get, %s free (keep 1 GB spare). "
                    "Choose fewer folders or types." % (size(total), size(free))]
        say("Getting %s files (%s) from %s …" % ("{:,}".format(len(get)), size(total), dev["name"]), 0.0)
        request = tempfile.TemporaryFile()
        request.write(json.dumps(sorted(get)).encode())
        request.seek(0)
        try:
            ssh, finish = sync.stream(dev, user, "files-get", ui, stdin=request)
        except OSError as e:
            return ["From %s:" % dev["name"], "    Failed: could not start ssh: %s" % e]
        pending = None  # (path, what to do, temporary file, its SHA-256, mode, mtime): waiting for its checksum

        def drop(item):
            if item and os.path.exists(item[2]):
                os.unlink(item[2])

        try:
            with tarfile.open(fileobj=ssh.stdout, mode="r|*", bufsize=1 << 20) as tar:
                for member in tar:
                    if stop and stop.is_set():
                        break
                    if member.name.startswith(SUMS):
                        sent = tar.extractfile(member).read().decode(errors="replace")
                        if pending and pending[0] == member.name[len(SUMS):]:
                            rel, how, tmp, digest, mode, mtime = pending
                            pending = None
                            if sent != digest:
                                os.unlink(tmp)
                                (changed if sent == "changed" else mismatched).append(rel)
                                continue
                            try:
                                os.chmod(tmp, mode & 0o777 or 0o644)
                                os.utime(tmp, (mtime, mtime))
                                done[_place(inside(rel), tmp, how, there, os.path.join(backup, rel))] += 1
                            except OSError as e:
                                failed.append("%s (%s)" % (rel, e.strerror or e))
                                os.path.exists(tmp) and os.unlink(tmp)
                        continue
                    drop(pending)  # a file whose checksum never came
                    pending = None
                    how = get.pop(member.name, None)
                    if not how or not member.isfile():
                        continue
                    dest, tmp = inside(member.name), None
                    meter.tick(member.name, force=member.size > 1 << 20)
                    try:
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        sha = hashlib.sha256()
                        src = tar.extractfile(member)
                        with tempfile.NamedTemporaryFile(dir=os.path.dirname(dest), prefix=".lkr-", delete=False) as f:
                            tmp = f.name
                            for chunk in iter(lambda: src.read(1 << 20), b""):
                                f.write(chunk)
                                sha.update(chunk)
                                meter.moved += len(chunk)
                                meter.tick(member.name)
                                if stop and stop.is_set():
                                    raise InterruptedError
                        pending = (member.name, how, tmp, sha.hexdigest(), member.mode, member.mtime)
                    except InterruptedError:
                        drop((None, None, tmp))
                        break
                    except OSError as e:
                        failed.append("%s (%s)" % (member.name, e.strerror or e))
                        drop((None, None, tmp))
                    meter.count += 1
        except (tarfile.TarError, EOFError, OSError) as e:
            broke = str(e)
        finally:
            drop(pending)
            stopped = bool(stop and stop.is_set())
            if stopped or broke:
                ssh.kill()
            ssh.stdout.close()
            problem = finish()
        if problem and not stopped:
            broke = problem
    else:
        stopped = False
    home = lambda path: path.replace(core.HOME, "~", 1)
    kept = sum(how == "mine" for how in choices.values())
    checked = done["new"] + done["theirs"] + done["both"] + done["already"]
    lines = ["From %s:" % dev["name"]]
    if checked:
        lines.append("    %s %s copied, every one checked with SHA-256: %s." % (
            "{:,}".format(checked), "file" if checked == 1 else "files", meter.summary()))
    for count, text in ((done["new"], "%s new."),
                        (done["theirs"], "%%s replaced with theirs; yours are kept in %s." % home(backup)),
                        (done["both"], "%%s kept both: theirs is next to yours as \"<name> (from %s).<ext>\"." % there),
                        (done["already"], "%s kept both: their copy was already next to yours."),
                        (kept, "%s kept yours."),
                        (the_plan["same"], "%s already the same."),
                        (the_plan["only_here"], "%s only on this device, left alone.")):
        if count:
            lines.append("    " + text % "{:,}".format(count))
    if the_plan["skipped"]:
        lines.append("    %d skipped: a folder or link here has that name (%s)." % (
            len(the_plan["skipped"]), ", ".join(the_plan["skipped"][:3]) + ", …" * (len(the_plan["skipped"]) > 3)))
    if mismatched:
        lines.append("    Failed: %d did not match their checksum and were not kept (%s). Run it again to get them."
                     % (len(mismatched), ", ".join(mismatched[:3]) + ", …" * (len(mismatched) > 3)))
    if changed:
        lines.append("    %d were being changed on %s while they were sent and were not kept (%s)."
                     % (len(changed), dev["name"], ", ".join(changed[:3]) + ", …" * (len(changed) > 3)))
    if stopped:
        lines.append("    Stopped: %s of %s files copied. Run it again to carry on: what is already here is recognised."
                     % ("{:,}".format(checked), "{:,}".format(wanted_count)))
    elif broke:
        lines.append("    Failed: the transfer broke off (%s). Run it again to carry on." % broke)
    elif get:
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


def _cli_say(text, part=None, live=False):
    """Progress in the terminal: live lines overwrite each other, other lines stay."""
    width = shutil.get_terminal_size().columns - 1
    if live and sys.stderr.isatty():
        stats, _nl, name = text.partition("\n")
        line = "%3d%%  %s  %s" % (round((part or 0) * 100), stats, name)
        print("\r" + line[:width].ljust(width), end="", file=sys.stderr, flush=True)
    elif not live:
        print(("\r" + " " * width + "\r" if sys.stderr.isatty() else "") + text, file=sys.stderr)


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
            lines = fetch(dev, user, the_plan, choices, sync.CLI, _cli_say)
            _cli_say("")
            print("\n".join(lines))
        except Trouble as e:
            sys.exit(str(e))
    else:
        return False
    return True
