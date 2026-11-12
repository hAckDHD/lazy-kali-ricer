"""Browser extensions, bookmarks, and the kit: your own folder with a tool list, bookmarks
and extra extensions, kept somewhere safe (a private git repository) for the next reinstall."""
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid

from . import core

SAMPLE = os.path.join(core.REPO, "kit-sample")
DEFAULT_DIR = os.path.join(core.HOME, "lazy-kali-kit")
TOOLS_DIR = os.path.join(core.HOME, "tools")  # your scripts and folders; git entries of tools.txt are cloned here too
SCRIPTS = "tools"  # the folder inside the kit that mirrors ~/tools
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
BIG = 5 * 1024 * 1024  # files above this stay out of the kit: GitHub is no place for archives and binaries
POLICY_NAME = "lazy-kali-ricer.json"
WEBSTORE = "https://clients2.google.com/service/update2/crx"

# Chromium-family browsers: name -> (command, process name, profile folder under ~, policy folder)
BROWSERS = {
    "Brave": ("brave-browser", "brave", ".config/BraveSoftware/Brave-Browser", "/etc/brave/policies/managed"),
    "Chromium": ("chromium", "chromium", ".config/chromium", "/etc/chromium/policies/managed"),
    "Google Chrome": ("google-chrome", "chrome", ".config/google-chrome", "/etc/opt/chrome/policies/managed"),
}
# (name, Chrome Web Store id)
EXTENSIONS = [
    ("Bitwarden", "nngceckbapebfimnlniiiahkandclblb"),
    ("Dark Reader", "eimadpbcbfnmbkopoojfekhnkhdbieeh"),
    ("Wappalyzer", "gppongmhjkpfnbhagpmjfkannfbllamg"),
    ("Sputnik (OSINT)", "manapjdamopgbpimgojkccikaabhmocd"),
    ("Read Aloud", "hdhinadidafjejdhmfkjgnolgimiaplp"),
]
EXT_ID = re.compile(r"^[a-p]{32}$")
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")


# ---------- the kit folder ----------
def kit_dir(s):
    return s.get("kit_dir") or DEFAULT_DIR


def set_dir(path):
    """Remember the kit folder without applying anything else."""
    s = core.load()
    s["kit_dir"] = path
    core.save(s)


def create_sample(folder):
    """Copy the sample files into the kit folder. Files already there are left alone. Returns the new names."""
    os.makedirs(os.path.join(folder, SCRIPTS), exist_ok=True)
    if not os.listdir(os.path.join(folder, SCRIPTS)):
        open(os.path.join(folder, SCRIPTS, ".gitkeep"), "w").close()  # git does not store an empty folder
    added = []
    for name in sorted(os.listdir(SAMPLE)):
        if not os.path.exists(os.path.join(folder, name)):
            shutil.copy(os.path.join(SAMPLE, name), os.path.join(folder, name))
            added.append(name)
    return added


def _lines(path):
    """Lines of a kit text file as word lists, comments and blanks dropped."""
    try:
        with open(path) as f:
            return [words for words in (line.split("#", 1)[0].split() for line in f) if words]
    except OSError:
        return []


# ---------- browsers ----------
def installed_browsers():
    return [name for name, (cmd, *_rest) in BROWSERS.items() if shutil.which(cmd)]


def _running(browser):
    return subprocess.run(["pgrep", "-x", BROWSERS[browser][1]], capture_output=True).returncode == 0


def _bookmarks_file(browser):
    return os.path.join(core.HOME, BROWSERS[browser][2], "Default", "Bookmarks")


# ---------- extensions ----------
def extensions(folder):
    """Built-in extensions, then the ones in the kit's extensions.txt ("<id>  # name" per line)."""
    found = list(EXTENSIONS)
    try:
        with open(os.path.join(folder, "extensions.txt")) as f:
            for line in f:
                ext_id, _hash, name = line.partition("#")
                ext_id = ext_id.strip()
                if EXT_ID.match(ext_id) and ext_id not in [i for _n, i in found]:
                    found.append((name.strip() or ext_id, ext_id))
    except OSError:
        pass
    return found


def kit_extension_ids(folder):
    """Ids listed in the kit's extensions.txt: the ones you want back after a reinstall."""
    return {words[0] for words in _lines(os.path.join(folder, "extensions.txt")) if EXT_ID.match(words[0])}


def installed_extensions(browser):
    """[(name, id)] of the Web Store extensions you added to a browser yourself."""
    settings = {}
    for name in ("Preferences", "Secure Preferences"):
        try:
            with open(os.path.join(core.HOME, BROWSERS[browser][2], "Default", name)) as f:
                settings.update(json.load(f).get("extensions", {}).get("settings", {}))
        except (OSError, ValueError, AttributeError):
            pass
    known, found = {i: n for n, i in EXTENSIONS}, []
    for ext_id, ext in settings.items():
        if isinstance(ext, dict) and ext.get("location") == 1 and ext.get("from_webstore") and EXT_ID.match(ext_id):
            name = str(ext.get("manifest", {}).get("name", ""))
            found.append((known.get(ext_id) or (ext_id if name.startswith("__MSG_") or not name else name), ext_id))
    return sorted(found, key=lambda e: e[0].lower())


def add_extensions(folder, found):
    """Append (name, id) pairs to the kit's extensions.txt that it does not list yet. Returns the names added."""
    have = kit_extension_ids(folder)
    new = [(name, ext_id) for name, ext_id in found if ext_id not in have]
    if new:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "extensions.txt"), "a") as f:
            f.write("".join("%s  # %s\n" % (ext_id, name.replace("\n", " ")) for name, ext_id in new))
    return [name for name, _id in new]


def _mirror(src, dest, replace):
    """Copy a folder tree: every folder (empty ones too) and every file, minus git checkouts, caches,
    virtual environments, links and files over BIG. With replace=False a file already at the
    destination is left alone. Returns (files copied, files left alone, names skipped for size)."""
    copied, kept, big = 0, 0, []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(root, d))
                         and not os.path.isdir(os.path.join(root, d, ".git")))
        there = os.path.join(dest, os.path.relpath(root, src))
        os.makedirs(there, exist_ok=True)
        for name in sorted(files):
            a, b = os.path.join(root, name), os.path.join(there, name)
            if os.path.islink(a) or not os.path.isfile(a) or name == ".gitkeep":
                continue
            if os.path.getsize(a) > BIG:
                big.append(os.path.relpath(a, src))
            elif os.path.exists(b) and not replace:
                kept += 1
            else:
                shutil.copy2(a, b)  # keeps the executable bit
                copied += 1
    return copied, kept, big


def save_scripts(folder):
    """~/tools -> the kit's tools/ folder. Git checkouts are left out (tools.txt lists them instead).
    Folders that would be empty get a .gitkeep, because git does not store empty folders."""
    if not os.path.isdir(TOOLS_DIR):
        return 0, 0, []
    dest = os.path.join(folder, SCRIPTS)
    result = _mirror(TOOLS_DIR, dest, replace=True)
    for root, dirs, files in os.walk(dest):
        if not dirs and not files:
            open(os.path.join(root, ".gitkeep"), "w").close()
    return result


def restore_scripts(folder):
    """The kit's tools/ folder -> ~/tools. Files you already have there are never overwritten."""
    src = os.path.join(folder, SCRIPTS)
    return _mirror(src, TOOLS_DIR, replace=False) if os.path.isdir(src) else (0, 0, [])


def count_scripts(folder):
    return sum(len([f for f in files if f != ".gitkeep"]) for _r, _d, files in os.walk(os.path.join(folder, SCRIPTS)))


def build(folder, browsers, tools=True, exts=True, marks=True, scripts=True):
    """Fill the kit from this system: the tool list, your scripts, and the extensions and bookmarks of
    the given browsers. Tools and extensions are only ever added to; scripts and bookmarks.json are
    brought up to date. Returns log lines."""
    log = []
    if scripts:
        copied, _kept, big = save_scripts(folder)
        log.append("Scripts: %d files from ~/tools saved%s." % (
            copied, " (left out, over 5 MB: %s)" % ", ".join(big[:4] + ["…"] * (len(big) > 4)) if big else ""))
    if tools:
        added = build_tools(folder)
        log.append("Tools: added %d packages, %d pipx tools, %d git repositories." % (added["apt"], added["pipx"], added["git"])
                   if any(added.values()) else "Tools: nothing new found.")
    if exts:
        names = [n for browser in browsers for n in add_extensions(folder, installed_extensions(browser))]
        log.append("Extensions: added %s." % ", ".join(names) if names else "Extensions: nothing new found.")
    if marks:
        for browser in browsers:
            try:
                data = load_bookmarks(_bookmarks_file(browser))
            except ValueError:
                continue  # this browser has no bookmarks
            export_bookmarks(browser, folder)
            log.append("Bookmarks: saved %d from %s." % (count_bookmarks(data), browser))
            break
        else:
            log.append("Bookmarks: none found in the ticked browsers.")
    return log


def policy_files():
    """[(browser, path)] for every extension list we wrote."""
    paths = [(name, os.path.join(b[3], POLICY_NAME)) for name, b in BROWSERS.items()]
    return [(name, path) for name, path in paths if os.path.isfile(path)]


def chosen_extensions():
    """Ids in the extension lists written so far."""
    ids = set()
    for _name, path in policy_files():
        try:
            with open(path) as f:
                ids.update(json.load(f).get("ExtensionSettings", {}))
        except (OSError, ValueError):
            pass
    return ids


def set_extensions(browsers, ids):
    """Tell each browser to install exactly these extensions (it does so itself on its next start).
    An empty list removes our file again. Needs the admin password. Returns True when it worked."""
    ids = [i for i in ids if EXT_ID.match(i)]
    targets = [shlex.quote(os.path.join(BROWSERS[b][3], POLICY_NAME)) for b in browsers]
    if not targets:
        return False
    if not ids:
        return core._root("rm -f %s" % " ".join(targets))
    # normal_installed: installed for you, and you can still switch it off in the browser.
    policy = {"ExtensionSettings": {i: {"installation_mode": "normal_installed", "update_url": WEBSTORE} for i in ids}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(policy, tmp, indent=2)
    try:
        return core._root(" && ".join("install -D -m 644 %s %s" % (shlex.quote(tmp.name), t) for t in targets))
    finally:
        os.remove(tmp.name)


# ---------- bookmarks ----------
def _complete(node, ids, stamp):
    """Fill in what the browser expects on a bookmark node, so a short hand-written file loads."""
    node.setdefault("name", "")
    node.setdefault("type", "url" if "url" in node else "folder")
    node.setdefault("id", str(next(ids)))
    node.setdefault("guid", str(uuid.uuid4()))
    node.setdefault("date_added", stamp)
    if node["type"] == "folder":
        node.setdefault("date_modified", "0")
        for child in node.setdefault("children", []):
            _complete(child, ids, stamp)


def _used_ids(node):
    yield int(node["id"]) if str(node.get("id", "")).isdigit() else 0
    for child in node.get("children", []):
        yield from _used_ids(child)


def load_bookmarks(path):
    """Read a bookmarks file (the browser's own, or the short sample form) into the full form.
    Raises ValueError when the file is something else."""
    try:
        with open(path) as f:
            data = json.load(f)
        roots = data["roots"]
        names = (("bookmark_bar", "Bookmarks bar"), ("other", "Other bookmarks"), ("synced", "Mobile bookmarks"))
        for key, name in names:
            roots.setdefault(key, {})
        start = max(n for key, _name in names for n in _used_ids(roots[key])) + 1
        ids = iter(range(start, start + 10 ** 7))
        stamp = str(int((time.time() + 11644473600) * 1000000))  # microseconds since 1601, as browsers store it
        for key, name in names:
            roots[key].setdefault("name", name)
            roots[key]["type"] = "folder"
            _complete(roots[key], ids, stamp)
        data.setdefault("version", 1)
        data.pop("sync_metadata", None)  # belongs to one browser profile's sync state; never carry it over
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise ValueError("Not a bookmarks file: %s (%s)" % (path, e)) from e


def count_bookmarks(data):
    def count(node):
        return 1 if node.get("type") == "url" else sum(count(c) for c in node.get("children", []))
    return sum(count(root) for root in data["roots"].values() if isinstance(root, dict))


def import_bookmarks(path, browsers):
    """Replace each browser's bookmarks with the file. The old ones are kept next to it as
    Bookmarks.bak-<time>. The browser must be closed, or it would overwrite the file on exit."""
    data = load_bookmarks(path)
    log, stamp = [], time.strftime("%Y%m%d-%H%M%S")
    for browser in browsers:
        dest = _bookmarks_file(browser)
        if _running(browser):
            log.append("%s is open: close it and import again." % browser)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(dest):
            os.rename(dest, "%s.bak-%s" % (dest, stamp))
        with open(dest, "w") as f:
            json.dump(data, f, indent=3)
        os.chmod(dest, 0o600)
        log.append("%s: %d bookmarks imported." % (browser, count_bookmarks(data)))
    return log


def export_bookmarks(browser, folder):
    """Copy a browser's bookmarks into the kit as bookmarks.json. Returns the new file's path."""
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, "bookmarks.json")
    data = load_bookmarks(_bookmarks_file(browser))
    with open(dest, "w") as f:
        json.dump(data, f, indent=3)
    os.chmod(dest, 0o600)
    return dest


# ---------- tools ----------
def read_tools(folder):
    """tools.txt as {"apt": [...], "pipx": [...], "git": [...]}. Each line is a kind, then one or more items."""
    tools = {"apt": [], "pipx": [], "git": []}
    for kind, *items in _lines(os.path.join(folder, "tools.txt")):
        if kind in tools:
            ok = (lambda i: i.startswith("https://")) if kind == "git" else PACKAGE.match
            tools[kind] += [i for i in items if ok(i) and i not in tools[kind]]
    return tools


def install_tools(folder):
    """Install everything in the kit's tools.txt. Already-installed entries are skipped. Returns log lines."""
    tools, log = read_tools(folder), []
    if tools["apt"]:
        state = core.package_state(tools["apt"])
        want = [p for p in tools["apt"] if state[p] == "available"]
        missing = [p for p in tools["apt"] if state[p] == "missing"]
        if want:
            ok = core.install_packages(want)
            log.append(("Installed %d packages." if ok else "Packages NOT installed (cancelled or failed): %d.") % len(want))
        if missing:
            log.append("Not in your package sources: %s" % " ".join(missing))
    for name in tools["pipx"]:
        if not shutil.which("pipx"):
            log.append("pipx is not installed: add `apt pipx` to tools.txt.")
            break
        done = subprocess.run(["pipx", "install", name], capture_output=True, text=True)
        if done.returncode and "already" not in done.stdout + done.stderr:
            log.append("pipx could not install %s" % name)
    cloned = 0
    for url in tools["git"]:
        dest = os.path.join(TOOLS_DIR, os.path.basename(url.rstrip("/")).removesuffix(".git"))
        if os.path.exists(dest):
            continue
        os.makedirs(TOOLS_DIR, exist_ok=True)
        done = subprocess.run(["git", "clone", "--depth", "1", "--", url, dest], capture_output=True,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))  # never hang on a password prompt
        cloned += done.returncode == 0
        if done.returncode:
            log.append("Could not clone %s" % url)
    if tools["git"]:
        log.append("Cloned %d new repositories into %s" % (cloned, TOOLS_DIR.replace(core.HOME, "~")))
    copied, kept, _big = restore_scripts(folder)
    if copied or kept:
        log.append("Scripts: %d files put in ~/tools%s." % (copied, ", %d already there and left alone" % kept if kept else ""))
    return log or ["Nothing to install: %s is empty or missing." % os.path.join(folder, "tools.txt").replace(core.HOME, "~")]


# ---------- the kit on GitHub ----------
# upload() and download() talk to the person through `ui`, an object with:
#   say(text)              progress line
#   code(text)             show the one-time sign-in code
#   ask_repo(allow_new)    -> ("new", "name") | ("existing", "owner/name") | None when cancelled
#   confirm(text)          -> bool
GITHUB = "https://github.com/"
NEW_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
SLUG = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")


def _run(*cmd):
    """(worked, output) of a command. git never stops to ask for a password."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except OSError as e:
        return False, str(e)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def _tail(text):
    return "\n".join(text.splitlines()[-3:])


def repo_slug(text):
    """"owner/name" from what someone typed or pasted (a full address works too), or "" if it is not one."""
    text = text.strip().removeprefix(GITHUB).removeprefix("git@github.com:").removesuffix("/").removesuffix(".git")
    return text if SLUG.match(text) else ""


DEVICE_PAGE = "https://github.com/login/device"
_signing = []  # the running `gh auth login`, so the GUI can cancel it


def cancel_sign_in():
    for gh in _signing:
        gh.terminate()


def sign_in(show_code):
    """Sign in to GitHub through the browser. show_code(code) gets the one-time code and is expected to
    open DEVICE_PAGE for the person; gh itself opens nothing, so nothing lands in a terminal.
    Returns True once GitHub confirmed (this waits for the person, up to 15 minutes)."""
    try:
        gh = subprocess.Popen(["gh", "auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              env=dict(os.environ, GH_BROWSER="true", BROWSER="true", GH_PROMPT_DISABLED="1"))
    except OSError:
        return False
    _signing.append(gh)
    for line in gh.stdout:
        code = re.search(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", line)
        if code:
            show_code(code.group())
            try:
                gh.stdin.write("\n")  # "Press Enter to open github.com in your browser"
                gh.stdin.flush()
            except OSError:
                pass
    _signing.remove(gh)
    return gh.wait() == 0 and _run("gh", "auth", "setup-git")[0]


def _ready(ui):
    """Make sure git and gh are installed and signed in. Returns "" when ready, else what went wrong."""
    missing = [p for p in ("git", "gh") if not shutil.which(p)]
    if missing:
        ui.say("Installing %s … (enter your password in the dialog)" % " and ".join(missing))
        if not core.install_packages(missing):
            return "Could not install %s." % " and ".join(missing)
    if not _run("gh", "auth", "status", "--hostname", "github.com")[0]:
        ui.say("Signing in to GitHub: your browser opens …")
        signed = sign_in(ui.code)
        ui.code("")  # done either way: take the code off the screen
        if not signed:
            return "Not signed in to GitHub."
    return ""


def _visibility(slug):
    """("PUBLIC" | "PRIVATE" | "INTERNAL", default branch) of a repository, or ("", "") if we cannot see it."""
    ok, out = _run("gh", "repo", "view", slug, "--json", "visibility,defaultBranchRef")
    try:
        info = json.loads(out) if ok else {}
    except ValueError:
        info = {}
    return info.get("visibility", ""), (info.get("defaultBranchRef") or {}).get("name") or "main"


def upload(folder, ui):
    """Commit the kit folder and push it to GitHub, asking for the repository the first time. Returns one message."""
    if not os.path.isdir(folder) or not [n for n in os.listdir(folder) if n != ".git"]:
        return "Nothing to upload: %s is empty. Press \"Add sample files\" first." % folder.replace(core.HOME, "~")
    problem = _ready(ui)
    if problem:
        return problem
    git = lambda *args: _run("git", "-C", folder, *args)
    if not os.path.isdir(os.path.join(folder, ".git")):
        git("init", "-b", "main")
    linked, origin = git("remote", "get-url", "origin")
    origin, new_name = origin if linked else "", ""
    public = "%s is PUBLIC. Everyone can read what you upload, your bookmarks included.\n\nUpload anyway?"
    if origin and _visibility(repo_slug(origin))[0] == "PUBLIC" and not ui.confirm(public % repo_slug(origin)):
        return "Cancelled. Nothing was uploaded."
    if not origin:
        choice = ui.ask_repo(True)
        if not choice:
            return "Cancelled. Nothing was uploaded."
        if choice[0] == "new":
            new_name = choice[1]
        else:
            slug = choice[1]
            visibility, branch = _visibility(slug)
            if not visibility:
                return "No repository %s, or your account cannot see it." % slug
            if visibility == "PUBLIC" and not ui.confirm(public % slug):
                return "Cancelled. Nothing was uploaded."
            git("remote", "add", "origin", "%s%s.git" % (GITHUB, slug))
            fetched, out = git("fetch", "origin")
            if not fetched:
                git("remote", "remove", "origin")
                return "Could not read %s:\n%s" % (slug, _tail(out))
            if git("rev-parse", "--verify", "-q", "origin/" + branch)[0]:  # it already has commits
                if git("rev-parse", "--verify", "-q", "HEAD")[0]:
                    joined, out = git("pull", "--rebase", "origin", branch)
                    if not joined:
                        git("rebase", "--abort")
                        git("remote", "remove", "origin")
                        return ("%s and this folder each have their own history, and they clash. "
                                "Sort it out in a terminal, or choose an empty folder and download instead." % slug)
                else:  # folder not committed yet: its files become the next commit on top of the repository
                    git("symbolic-ref", "HEAD", "refs/heads/" + branch)
                    git("reset", "origin/" + branch)
            origin = slug
    if not git("config", "user.email")[1]:  # git refuses to commit without a name; use the GitHub account
        ok, who = _run("gh", "api", "user", "--jq", '"\\(.id) \\(.login)"')
        if ok and len(who.split()) == 2:
            uid, login = who.split()
            git("config", "user.name", login)
            git("config", "user.email", "%s+%s@users.noreply.github.com" % (uid, login))
    git("add", "-A")
    if not git("diff", "--cached", "--quiet")[0]:
        saved, out = git("commit", "-m", "Kit update %s" % time.strftime("%Y-%m-%d %H:%M"))
        if not saved:
            return "Could not commit:\n%s" % _tail(out)
    ui.say("Uploading …")
    if new_name:
        sent, out = _run("gh", "repo", "create", new_name, "--private", "--source", folder, "--remote", "origin", "--push")
        return ("Created the private repository %s and uploaded your kit." % new_name) if sent else \
            "Could not create %s:\n%s" % (new_name, _tail(out))
    sent, out = git("push", "-u", "origin", "HEAD")
    if not sent and ("fetch first" in out or "non-fast-forward" in out):  # GitHub has newer changes: put ours on top
        if not git("pull", "--rebase")[0]:
            git("rebase", "--abort")
            return ("GitHub has newer changes to the same lines as yours. Nothing was uploaded; "
                    "sort it out in a terminal with `git pull` in the kit folder.")
        sent, out = git("push", "-u", "origin", "HEAD")
    return "Kit uploaded to %s." % repo_slug(origin) if sent else "Could not upload:\n%s" % _tail(out)


def download(folder, ui):
    """Bring the kit folder up to date from GitHub, or fetch it the first time. Returns one message."""
    problem = _ready(ui)
    if problem:
        return problem
    short = folder.replace(core.HOME, "~")
    if os.path.isdir(os.path.join(folder, ".git")) and _run("git", "-C", folder, "remote", "get-url", "origin")[0]:
        ui.say("Downloading …")
        ok, out = _run("git", "-C", folder, "pull", "--rebase", "--autostash")  # keeps what you changed here
        if not ok:
            _run("git", "-C", folder, "rebase", "--abort")
            return "Could not update %s:\n%s" % (short, _tail(out))
        return "Kit in %s is up to date with GitHub." % short
    if os.path.isdir(folder) and os.listdir(folder):
        return ("%s already has files that did not come from GitHub. Press \"Upload to GitHub\" to link it "
                "to a repository, or choose an empty folder." % short)
    choice = ui.ask_repo(False)
    if not choice:
        return "Cancelled. Nothing was downloaded."
    ui.say("Downloading …")
    ok, out = _run("gh", "repo", "clone", choice[1], folder)
    return "Downloaded %s to %s." % (choice[1], short) if ok else "Could not download %s:\n%s" % (choice[1], _tail(out))


# ---------- building tools.txt ----------
APT_LOGS = "/var/log/apt/history.log*"


def add_tools(folder, kind, items, heading=""):
    """Append entries to tools.txt that it does not list yet. Returns the ones added."""
    have = read_tools(folder)[kind]
    new = [i for i in dict.fromkeys(items) if i not in have]
    if not new:
        return []
    path = os.path.join(folder, "tools.txt")
    os.makedirs(folder, exist_ok=True)
    fresh = not os.path.exists(path)
    with open(path, "a") as f:
        if fresh:
            f.write("# My tools. One line = a kind (apt, pipx, git), then one or more items.\n")
        if heading:
            f.write("\n# %s\n" % heading)
        if kind == "git":
            f.write("".join("git   %s\n" % url for url in new))
        else:
            f.write("".join("%-5s %s\n" % (kind, " ".join(new[i:i + 6])) for i in range(0, len(new), 6)))
    return new


def scan_tools():
    """What you added to this system yourself, as {"apt": [...], "pipx": [...], "git": [...]}:
    packages you asked apt for by name (from apt's log, still installed), pipx tools,
    and git repositories in ~/tools."""
    import glob
    import gzip

    asked = []
    for path in sorted(glob.glob(APT_LOGS)):
        try:
            with (gzip.open if path.endswith(".gz") else open)(path, "rt", errors="replace") as f:
                lines = [line for line in f if line.startswith("Commandline:")]
        except OSError:
            continue
        for line in lines:
            words = line.split()[1:]
            if "install" not in words or os.path.basename(words[0]) not in ("apt", "apt-get"):
                continue
            for word in words[words.index("install") + 1:]:
                name = word.split("=")[0].split(":")[0]
                if not word.startswith("-") and "/" not in word and PACKAGE.match(name):
                    asked.append(name)
    asked = list(dict.fromkeys(asked))
    state = core.package_state(asked) if asked else {}
    found = {"apt": sorted(p for p in asked if state[p] == "installed"), "pipx": [], "git": []}
    if shutil.which("pipx"):
        found["pipx"] = sorted(m.group(1) for line in _run("pipx", "list", "--short")[1].splitlines()
                               for m in [re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*) \d", line)] if m)  # "name 1.2.3"
    if os.path.isdir(TOOLS_DIR):
        for name in sorted(os.listdir(TOOLS_DIR)):
            ok, url = _run("git", "-C", os.path.join(TOOLS_DIR, name), "remote", "get-url", "origin")
            url = url.strip().removesuffix(".git")
            if ok and url.startswith("git@github.com:"):
                url = GITHUB + url.removeprefix("git@github.com:")
            if ok and url.startswith("https://") and os.path.isdir(os.path.join(TOOLS_DIR, name, ".git")):
                found["git"].append(url)
    return found


def build_tools(folder):
    """Add everything scan_tools() finds to tools.txt (nothing is ever removed). Returns {kind: number added}."""
    found, heading = scan_tools(), "Found on this system, %s" % time.strftime("%Y-%m-%d")
    added = {}
    for kind in ("apt", "pipx", "git"):
        added[kind] = len(add_tools(folder, kind, found[kind], "" if any(added.values()) else heading))
    return added


def search_packages(text, limit=40):
    """Packages whose name matches every word typed: [(name, description, "installed" | "available")]."""
    words = [w for w in re.findall(r"[A-Za-z0-9+._-]+", text) if not w.startswith("-")]
    if not words:
        return []
    rows = [line.split(" - ", 1) for line in _run("apt-cache", "search", "--names-only", "--", *words)[1].splitlines()]
    rows = sorted((r for r in rows if len(r) == 2 and PACKAGE.match(r[0])), key=lambda r: (len(r[0]), r[0]))[:limit]
    state = core.package_state([name for name, _d in rows]) if rows else {}
    return [(name, description, state[name]) for name, description in rows]
