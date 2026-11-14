"""Burp Suite: one proxy port of your choice, set in Burp and handed to FoxyProxy in your browsers."""
import json
import os
import shlex
import shutil
import tempfile

from . import core, kit

DEFAULT_PORT = 8080
DIR = os.path.join(core.OWN, "burp")
PROJECT = os.path.join(DIR, "burp-project.json")  # Burp reads it with --config-file
IMPORT = os.path.join(DIR, "foxyproxy-burp.json")  # FoxyProxy reads it with Options > Import
MENU_ENTRY = "kali-burpsuite.desktop"  # same name as Kali's menu entry, so ours takes its place
SYSTEM_LAUNCHER = os.path.join("/usr/share/applications", MENU_ENTRY)
LAUNCHER = os.path.join(core.LAUNCHERS, MENU_ENTRY)
OURS = "X-Lazy-Kali-Ricer=true"  # marks the menu entry as written by us

FOXYPROXY = "gcknhkkoolaabfmlnjonogaaifnjlfnp"  # Chrome Web Store id; also in kit.EXTENSIONS
FOXYPROXY_FIREFOX = "foxyproxy@eric.h.jung"
FOXYPROXY_XPI = "https://addons.mozilla.org/firefox/downloads/latest/foxyproxy-standard/latest.xpi"

# Firefox has one policy file, not a folder of them. Once the one in /etc exists, Firefox stops reading
# the one shipped next to the program (Kali's home page and so on), so ours starts as a copy of that.
FIREFOX = "Firefox"
FIREFOX_COMMANDS = ("firefox-esr", "firefox")
FIREFOX_POLICY = "/etc/firefox/policies/policies.json"
FIREFOX_SHIPPED = ("/usr/lib/firefox-esr/distribution/policies.json", "/usr/lib/firefox/distribution/policies.json")


def valid_port(value):
    """The port as a number when it is one a normal user's Burp can listen on (1024-65535), else None."""
    try:
        port = int(str(value).strip())
    except ValueError:
        return None
    return port if 1024 <= port <= 65535 else None


def port(s):
    return valid_port(s.get("burp_port")) or DEFAULT_PORT


def set_port(number):
    """Remember the port without applying anything else."""
    s = core.load()
    s["burp_port"] = number
    core.save(s)


def browsers():
    """Installed browsers FoxyProxy can be added to."""
    return kit.installed_browsers() + [FIREFOX] * any(shutil.which(c) for c in FIREFOX_COMMANDS)


# ---------- Burp ----------
def project_options(number):
    """Burp project options with a single proxy listener: this machine only, on the port."""
    return {"proxy": {"request_listeners": [{
        "certificate_mode": "per_host", "listen_mode": "loopback_only", "listener_port": number, "running": True}]}}


def foxyproxy_options(number):
    """A FoxyProxy settings file (its own export format) with one proxy, Burp. Mode "disable": importing
    it does not send traffic anywhere until you pick Burp in FoxyProxy's menu."""
    return {"mode": "disable", "sync": False, "autoBackup": False, "passthrough": "", "theme": "",
            "container": {}, "commands": {},
            "data": [{"active": True, "title": "Burp", "type": "http", "hostname": "127.0.0.1", "port": str(number),
                      "username": "", "password": "", "cc": "", "city": "", "color": "#ff6633", "pac": "",
                      "pacString": "", "proxyDNS": True, "include": [], "exclude": [], "tabProxy": []}]}


def _launcher_text():
    """Kali's Burp menu entry with our Exec line; a plain one when Kali's is not there."""
    run = "Exec=burpsuite --config-file=%s" % ('"%s"' % PROJECT if " " in PROJECT else PROJECT)
    try:
        with open(SYSTEM_LAUNCHER) as f:
            lines = [line.rstrip("\n") for line in f]
    except OSError:
        lines = ["[Desktop Entry]", "Type=Application", "Name=burpsuite", "Icon=kali-burpsuite", "Terminal=false",
                 "Categories=kali-web-vulnerability-scanning;", "StartupWMClass=burp-StartBurp", "Exec=burpsuite"]
    lines = [run if line.startswith("Exec=") else line for line in lines]
    return "\n".join(lines + [OURS]) + "\n"


def our_launcher():
    """True when the Burp menu entry in ~/.local/share/applications is the one we wrote."""
    try:
        with open(LAUNCHER) as f:
            return OURS in f.read().splitlines()
    except OSError:
        return False


def write_configs(number):
    """Write Burp's project options, the FoxyProxy import file and the menu entry that starts Burp with
    those options. No password needed. Returns log lines."""
    os.makedirs(DIR, exist_ok=True)
    for path, data in ((PROJECT, project_options(number)), (IMPORT, foxyproxy_options(number))):
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    short = lambda p: p.replace(core.HOME, "~")
    if not shutil.which("burpsuite"):
        return ["Burp Suite is not installed (package: burpsuite). Its settings are ready in %s." % short(PROJECT)]
    if os.path.exists(LAUNCHER) and not our_launcher():
        return ["Burp: you have your own menu entry (%s), left alone. Start Burp with: burpsuite --config-file=%s"
                % (short(LAUNCHER), short(PROJECT))]
    os.makedirs(core.LAUNCHERS, exist_ok=True)
    with open(LAUNCHER, "w") as f:
        f.write(_launcher_text())
    return ["Burp: started from the menu, its proxy now listens on 127.0.0.1:%d. Restart Burp if it is open. "
            "From a terminal: burpsuite --config-file=%s" % (number, short(PROJECT))]


# ---------- FoxyProxy ----------
def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _chromium_policy(browser):
    """The browser's extension list (our policy file) with FoxyProxy in it; what is listed stays."""
    policy = _read(os.path.join(kit.BROWSERS[browser][3], kit.POLICY_NAME))
    wanted = policy.get("ExtensionSettings")
    policy["ExtensionSettings"] = wanted = wanted if isinstance(wanted, dict) else {}
    wanted.setdefault(FOXYPROXY, {"installation_mode": "normal_installed", "update_url": kit.WEBSTORE})
    return policy


def _firefox_policy():
    """Firefox's policy file with FoxyProxy in it. It starts from the file in /etc when there is one,
    else from the one shipped with Firefox. Returns None when the file in /etc is there but unreadable
    as JSON: better to leave it alone than to replace someone's work."""
    if os.path.exists(FIREFOX_POLICY):
        with open(FIREFOX_POLICY) as f:
            try:
                data = json.load(f)
            except ValueError:
                return None
        if not isinstance(data, dict):
            return None
    else:
        data = next((d for d in map(_read, FIREFOX_SHIPPED) if d), {})
    policies = data.get("policies")
    data["policies"] = policies = policies if isinstance(policies, dict) else {}
    wanted = policies.get("ExtensionSettings")
    policies["ExtensionSettings"] = wanted = wanted if isinstance(wanted, dict) else {}
    wanted.setdefault(FOXYPROXY_FIREFOX, {"installation_mode": "normal_installed", "install_url": FOXYPROXY_XPI})
    return data


def firefox_has_foxyproxy():
    settings = (_read(FIREFOX_POLICY).get("policies") or {}).get("ExtensionSettings") or {}
    return FOXYPROXY_FIREFOX in settings


def has_foxyproxy(browser):
    """True when the browser is already told to install FoxyProxy."""
    if browser == FIREFOX:
        return firefox_has_foxyproxy()
    return FOXYPROXY in (_read(os.path.join(kit.BROWSERS[browser][3], kit.POLICY_NAME)).get("ExtensionSettings") or {})


def _install(files):
    """Put [(data, destination)] in place as root, in one password prompt. Returns True when it worked."""
    made = []
    try:
        for data, dest in files:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
                json.dump(data, tmp, indent=2)
            made.append((tmp.name, dest))
        return core._root(" && ".join("install -D -m 644 %s %s" % (shlex.quote(a), shlex.quote(b)) for a, b in made))
    finally:
        for path, _dest in made:
            os.remove(path)


def add_foxyproxy(names):
    """Tell each of the browsers to install FoxyProxy (it does so itself on its next start). Browsers
    that are told already are skipped; the admin password is asked only when something is to be written.
    Returns (worked, log lines)."""
    files, log, new = [], [], []
    for name in names:
        if has_foxyproxy(name):
            log.append("%s: FoxyProxy is on its list already." % name)
        elif name == FIREFOX:
            policy = _firefox_policy()
            if policy is None:
                log.append("Firefox: %s is not valid JSON, left alone. Fix or delete it and try again." % FIREFOX_POLICY)
            else:
                files.append((policy, FIREFOX_POLICY))
                new.append(name)
        else:
            files.append((_chromium_policy(name), os.path.join(kit.BROWSERS[name][3], kit.POLICY_NAME)))
            new.append(name)
    if not files:
        return True, log
    if not _install(files):
        return False, log + ["FoxyProxy NOT added to %s (cancelled or failed)." % ", ".join(new)]
    return True, log + ["FoxyProxy added to %s: restart the browser to get it." % ", ".join(new)]


def setup(number, names):
    """The whole job: Burp on the port, FoxyProxy in the browsers, the import file. Returns (worked, log lines)."""
    log = write_configs(number)
    ok, more = add_foxyproxy(names) if names else (True, [])
    log += more
    log.append("Last step, once per browser: FoxyProxy > Options > Import > choose %s > Save. Then pick Burp in "
               "FoxyProxy's menu to send the browser through Burp, Disable to stop."
               % IMPORT.replace(core.HOME, "~"))
    return ok, log


# ---------- undo ----------
def remove_firefox():
    """Take FoxyProxy off Firefox's policy file again. The file goes when nothing of yours is left in it:
    Firefox then reads the one shipped with it, as before. Needs the admin password. Returns True when done."""
    data = _read(FIREFOX_POLICY)
    policies = data.get("policies") if isinstance(data.get("policies"), dict) else {}
    wanted = policies.get("ExtensionSettings") if isinstance(policies.get("ExtensionSettings"), dict) else {}
    if FOXYPROXY_FIREFOX not in wanted:
        return True
    del wanted[FOXYPROXY_FIREFOX]
    if not wanted:
        del policies["ExtensionSettings"]
    shipped = next((d for d in map(_read, FIREFOX_SHIPPED) if d), {})
    if not policies or data == shipped:
        return core._root("rm -f %s" % shlex.quote(FIREFOX_POLICY))
    return _install([(data, FIREFOX_POLICY)])
