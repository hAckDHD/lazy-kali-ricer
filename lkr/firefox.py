"""Firefox: extensions, bookmarks and Burp's certificate, all through its policy file.

Firefox differs from the Chromium family in three ways this file deals with: it has one policy file
instead of a folder of them, its add-ons have their own ids and come from addons.mozilla.org, and
its bookmarks sit in a database (places.sqlite) instead of a JSON file. Bookmarks are therefore not
written into the profile but handed over as policy: Firefox adds them beside its own. (Its other
way in, importing bookmarks.html at startup, is switched off by the NoDefaultBookmarks policy Kali
ships.)"""
import configparser
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile

from . import core

NAME = "Firefox"
COMMANDS = ("firefox-esr", "firefox")

# Once the policy file in /etc exists, Firefox stops reading the one shipped next to the program
# (Kali's home page and so on), so ours starts as a copy of that.
POLICY = "/etc/firefox/policies/policies.json"
SHIPPED = ("/usr/lib/firefox-esr/distribution/policies.json", "/usr/lib/firefox/distribution/policies.json")
CERT = "/etc/firefox/policies/lazy-kali-ricer-burp-ca.pem"  # Burp's certificate; the policy file points here
BOOKMARKS = os.path.join(core.OWN, "firefox-bookmarks.json")  # the bookmark entries we put in the policy file
XPI = "https://addons.mozilla.org/firefox/downloads/latest/%s/latest.xpi"

# Chrome Web Store id -> (Firefox add-on id, its name in the addons.mozilla.org address).
# The same extension by the same maker; ids checked against addons.mozilla.org.
ADDONS = {
    "nngceckbapebfimnlniiiahkandclblb": ("{446900e4-71c2-419f-a6a7-df9c091e268b}", "bitwarden-password-manager"),
    "eimadpbcbfnmbkopoojfekhnkhdbieeh": ("addon@darkreader.org", "darkreader"),
    "gppongmhjkpfnbhagpmjfkannfbllamg": ("wappalyzer@crunchlabz.com", "wappalyzer"),
    "manapjdamopgbpimgojkccikaabhmocd": ("{78eea43d-42ab-4410-98b9-de9d50ddb1a3}", "sputnik-osint"),
    "hdhinadidafjejdhmfkjgnolgimiaplp": ("{ddc62400-f22d-4dd3-8b4a-05837de53c2e}", "read-aloud"),
    "gcknhkkoolaabfmlnjonogaaifnjlfnp": ("foxyproxy@eric.h.jung", "foxyproxy-standard"),
}


def installed():
    return any(shutil.which(c) for c in COMMANDS)


def running():
    return any(subprocess.run(["pgrep", "-x", c], capture_output=True).returncode == 0 for c in COMMANDS)


# ---------- the policy file ----------
def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _shipped():
    return next((d for d in map(_read, SHIPPED) if d), {})


def _section(holder, key, kind=dict):
    """holder[key] as a dict (or list), put there fresh when it is missing or something else."""
    if not isinstance(holder.get(key), kind):
        holder[key] = kind()
    return holder[key]


def load():
    """The policies to build on: the file in /etc when there is one, else the one shipped with Firefox.
    Returns None when the file in /etc is there but is not valid JSON: better to leave it alone than to
    replace someone's work."""
    if not os.path.exists(POLICY):
        return _shipped()
    try:
        with open(POLICY) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _entry(chrome_id):
    guid, slug = ADDONS[chrome_id]
    return guid, {"installation_mode": "normal_installed", "install_url": XPI % slug}


def _ours(data):
    """Chrome ids of the add-ons on the list that we put there (same id, same address)."""
    listed = _section(_section(data, "policies"), "ExtensionSettings")
    return [i for i in ADDONS if listed.get(_entry(i)[0]) == _entry(i)[1]]


def chosen():
    """Chrome ids of the extensions Firefox is told to install now."""
    return set(_ours(_read(POLICY)))


def add_addons(data, chrome_ids):
    """Put these extensions on the list; what is listed stays."""
    listed = _section(_section(data, "policies"), "ExtensionSettings")
    for chrome_id in chrome_ids:
        if chrome_id in ADDONS:
            guid, entry = _entry(chrome_id)
            listed.setdefault(guid, entry)


def set_addons(data, chrome_ids):
    """Make the list hold exactly these of our extensions. Entries that are not ours are never touched."""
    listed = _section(_section(data, "policies"), "ExtensionSettings")
    for chrome_id in _ours(data):
        if chrome_id not in chrome_ids:
            del listed[_entry(chrome_id)[0]]
    add_addons(data, chrome_ids)


def set_cert(data, on=True):
    """Add or drop the line that makes Firefox trust the certificate in CERT."""
    certs = _section(_section(data, "policies"), "Certificates")
    paths = [p for p in _section(certs, "Install", list) if p != CERT] + [CERT] * on
    certs["Install"] = paths


def _our_bookmarks():
    """The bookmark entries we put in the policy file last time."""
    try:
        with open(BOOKMARKS) as f:
            entries = json.load(f)
        return entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        return []


def bookmark_entries(data):
    """Bookmarks (the kit's form) as Firefox policy entries: bookmark_bar -> its toolbar, the rest ->
    its bookmarks menu. The policy knows one level of folders, so a folder inside a folder becomes one
    folder named "Outer / Inner"."""
    entries = []

    def walk(nodes, placement, folders):
        for node in nodes:
            if "children" in node or node.get("type") == "folder":
                walk(node.get("children", []), placement, folders + [str(node.get("name") or "Folder")])
            elif node.get("url"):
                entry = {"Title": str(node.get("name") or node["url"]), "URL": str(node["url"]), "Placement": placement}
                if folders:
                    entry["Folder"] = " / ".join(folders)
                if entry not in entries:
                    entries.append(entry)

    roots = data.get("roots", {})
    for key, placement in (("bookmark_bar", "toolbar"), ("other", "menu"), ("synced", "menu")):
        walk((roots.get(key) or {}).get("children", []), placement, [])
    return entries


def set_bookmarks(data, entries):
    """Make the policy hold these bookmarks in place of the ones we put there before. Bookmark entries
    that are not ours are never touched."""
    ours = _our_bookmarks()
    listed = _section(_section(data, "policies"), "Bookmarks", list)
    listed[:] = [e for e in listed if e not in ours and e not in entries] + entries


def remember_bookmarks(entries):
    """Record which bookmark entries are ours, once they are in the policy file."""
    if entries:
        os.makedirs(core.OWN, exist_ok=True)
        with open(BOOKMARKS, "w") as f:
            json.dump(entries, f, indent=1)
    elif os.path.exists(BOOKMARKS):
        os.remove(BOOKMARKS)


def tidy(data):
    """Drop the sections we may have left empty. Returns True when nothing of anyone's is left, so the
    file can go and Firefox reads the shipped one again."""
    policies = _section(data, "policies")
    certs = policies.get("Certificates")
    if isinstance(certs, dict) and not certs.get("Install", True):
        del certs["Install"]
    for key in ("ExtensionSettings", "Certificates", "Bookmarks"):
        if key in policies and not policies[key]:
            del policies[key]
    return not policies or data == _shipped()


def touched():
    """True when Firefox's policies hold something of ours."""
    data = _read(POLICY)
    certs = _section(_section(data, "policies"), "Certificates")
    marks = _section(data["policies"], "Bookmarks", list)
    return (bool(_ours(data)) or CERT in _section(certs, "Install", list) or os.path.exists(CERT)
            or any(e in marks for e in _our_bookmarks()))


def remove():
    """Take our extensions, bookmarks and Burp's certificate out of Firefox's policies again (Firefox
    then removes the bookmarks itself). The file goes when nothing else is in it. Needs the admin
    password. Returns True when done."""
    if not touched():
        return True
    data = _read(POLICY)
    set_addons(data, [])
    set_bookmarks(data, [])
    set_cert(data, False)
    done = core.root_files([], [POLICY, CERT]) if tidy(data) else core.root_files([(data, POLICY)], [CERT])
    if done:
        remember_bookmarks([])
    return done


# ---------- profiles ----------
def profiles():
    """Folders of the profiles Firefox opens by default (newer versions keep them under ~/.config)."""
    found = []
    for root in (os.path.join(core.CFG, "mozilla", "firefox"), os.path.join(core.HOME, ".mozilla", "firefox")):
        ini = configparser.ConfigParser(interpolation=None)
        ini.optionxform = str
        try:
            ini.read(os.path.join(root, "profiles.ini"))
        except configparser.Error:
            continue
        listed = [s for s in ini.sections() if s.startswith("Profile") and ini[s].get("Path")]
        paths = ([ini[s]["Default"] for s in ini.sections() if s.startswith("Install") and ini[s].get("Default")]
                 or [ini[s]["Path"] for s in listed if ini[s].get("Default") == "1"]
                 or [ini[s]["Path"] for s in listed])
        for path in paths:
            folder = os.path.normpath(os.path.join(root, path))
            if os.path.isdir(folder) and folder not in found:
                found.append(folder)
    return found


# ---------- bookmarks ----------
TOOLBAR, MENU, UNFILED, MOBILE = "toolbar_____", "menu________", "unfiled_____", "mobile______"


def read_bookmarks(profile):
    """A profile's bookmarks in the short form of the kit's bookmarks.json: toolbar -> bookmark_bar,
    menu and other bookmarks -> other, mobile -> synced. Used to save them into the kit. Works on a
    copy, so Firefox may be open.
    Raises ValueError when the profile has no readable bookmarks."""
    source = os.path.join(profile, "places.sqlite")
    if not os.path.isfile(source):
        raise ValueError("No bookmarks in %s" % profile)
    with tempfile.TemporaryDirectory() as tmp:
        for tail in ("", "-wal"):  # the -wal file holds what Firefox has not written into the main file yet
            if os.path.isfile(source + tail):
                shutil.copy(source + tail, os.path.join(tmp, "places.sqlite" + tail))
        try:
            db = sqlite3.connect(os.path.join(tmp, "places.sqlite"))
            rows = db.execute("SELECT b.id, b.type, b.parent, b.title, p.url, b.guid FROM moz_bookmarks b "
                              "LEFT JOIN moz_places p ON p.id = b.fk ORDER BY b.parent, b.position").fetchall()
            db.close()
        except sqlite3.Error as e:
            raise ValueError("Cannot read the bookmarks of %s (%s)" % (profile, e)) from e
    nodes, roots = {}, {}
    for row_id, kind, _parent, title, url, guid in rows:
        if kind == 2:  # folder
            nodes[row_id] = {"name": title or "", "children": []}
            if guid in (TOOLBAR, MENU, UNFILED, MOBILE):
                roots[guid] = nodes[row_id]
        elif kind == 1 and url and not url.startswith("place:"):  # a bookmark; place: is a saved search
            nodes[row_id] = {"name": title or "", "url": url}
    for row_id, _kind, parent, _title, _url, guid in rows:
        if row_id in nodes and parent in nodes and guid not in roots:
            nodes[parent]["children"].append(nodes[row_id])
    kids = lambda guid: roots.get(guid, {}).get("children", [])
    return {"roots": {"bookmark_bar": {"children": kids(TOOLBAR)}, "other": {"children": kids(MENU) + kids(UNFILED)},
                      "synced": {"children": kids(MOBILE)}}}
