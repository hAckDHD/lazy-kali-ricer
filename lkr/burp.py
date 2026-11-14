"""Burp Suite: one proxy port of your choice, set in Burp and handed to FoxyProxy in your browsers,
which are also told to trust Burp's certificate so HTTPS sites open through it."""
import base64
import json
import os
import re
import shutil
import ssl
import subprocess
import urllib.request

from . import core, firefox, kit

DEFAULT_PORT = 8080
DIR = os.path.join(core.OWN, "burp")
PROJECT = os.path.join(DIR, "burp-project.json")  # Burp reads it with --config-file
IMPORT = os.path.join(DIR, "foxyproxy-burp.json")  # FoxyProxy reads it with Options > Import
MENU_ENTRY = "kali-burpsuite.desktop"  # same name as Kali's menu entry, so ours takes its place
SYSTEM_LAUNCHER = os.path.join("/usr/share/applications", MENU_ENTRY)
LAUNCHER = os.path.join(core.LAUNCHERS, MENU_ENTRY)
OURS = "X-Lazy-Kali-Ricer=true"  # marks the menu entry as written by us

FOXYPROXY = "gcknhkkoolaabfmlnjonogaaifnjlfnp"  # Chrome Web Store id; also in kit.EXTENSIONS

# Burp makes its own certificate authority on first start and keeps it, private key included, in its
# Java preferences as a PKCS#12 store with this fixed password. Only the certificate is ever taken out.
BURP_PREFS = os.path.join(core.HOME, ".java", ".userPrefs", "burp", "prefs.xml")
BURP_STORE_PASSWORD = "/burp/media/ps.p12"
CERT = os.path.join(DIR, "burp-ca.pem")  # your copy of the certificate, for anything else that should trust Burp
CERT_POLICY = "lazy-kali-ricer-burp.json"  # Chromium-family policy file holding the certificate


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
    return kit.installed_browsers()


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


# ---------- Burp's certificate ----------
def _der(pem):
    """The certificate's bytes, or None when the text is not a certificate."""
    try:
        return ssl.PEM_cert_to_DER_cert(pem.strip())
    except (ValueError, TypeError):
        return None


def ca_certificate(number):
    """Burp's CA certificate as PEM text: from Burp's saved preferences, or, failing that, from a Burp
    running on the port. "" when Burp has never been started on this system."""
    try:
        with open(BURP_PREFS) as f:
            store = re.search(r'key="caCert" value="([^"]+)"', f.read())
        if store and shutil.which("openssl"):  # the store goes in on stdin: its private key never touches a file
            done = subprocess.run(["openssl", "pkcs12", "-nokeys", "-passin", "pass:" + BURP_STORE_PASSWORD],
                                  input=base64.b64decode(store.group(1)), capture_output=True)
            found = re.search(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", done.stdout.decode(), re.S)
            if found and _der(found.group()):
                return found.group() + "\n"
    except (OSError, ValueError):
        pass
    try:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never through a proxy
        with direct.open("http://127.0.0.1:%d/cert" % number, timeout=4) as reply:
            pem = ssl.DER_cert_to_PEM_cert(reply.read(20000))
        return pem if _der(pem) else ""
    except (OSError, ValueError):
        return ""


# ---------- browsers: FoxyProxy and the certificate ----------
def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _chromium_files(browser, pem):
    """[(content, path)] for a Chromium-family browser: its extension list (our policy file) with
    FoxyProxy in it, what is listed stays; and, with a certificate, the policy file that trusts it."""
    folder = kit.BROWSERS[browser][3]
    policy = _read(os.path.join(folder, kit.POLICY_NAME))
    if not isinstance(policy.get("ExtensionSettings"), dict):
        policy["ExtensionSettings"] = {}
    policy["ExtensionSettings"].setdefault(FOXYPROXY, {"installation_mode": "normal_installed", "update_url": kit.WEBSTORE})
    files = [(policy, os.path.join(folder, kit.POLICY_NAME))]
    if pem:
        files.append(({"CACertificates": [base64.b64encode(_der(pem)).decode()]}, os.path.join(folder, CERT_POLICY)))
    return files


def _firefox_files(pem):
    """[(content, path)] for Firefox: its policy file with FoxyProxy on the list (what is listed stays)
    and, with a certificate, a line that installs it, plus the certificate itself. None when the policy
    file in /etc is not valid JSON."""
    data = firefox.load()
    if data is None:
        return None
    firefox.add_addons(data, [FOXYPROXY])
    files = [(data, firefox.POLICY)]
    if pem:
        firefox.set_cert(data)
        files.append((pem, firefox.CERT))
    return files


def _same(content, path):
    """True when the file already holds this content."""
    return content == (_text(path) if isinstance(content, str) else _read(path))


def cert_policy_files():
    """[(browser, path)] for every certificate policy file we wrote for a Chromium-family browser."""
    paths = [(name, os.path.join(b[3], CERT_POLICY)) for name, b in kit.BROWSERS.items()]
    return [(name, path) for name, path in paths if os.path.isfile(path)]


def setup_browsers(names, pem=""):
    """Tell each of the browsers to install FoxyProxy and, given Burp's certificate, to trust it. Both
    take effect at the browser's next start. A browser that is set already is skipped; the admin password
    is asked only when something is to be written. Returns (worked, log lines)."""
    files, log, new = [], [], []
    what = "FoxyProxy and Burp's certificate" if pem else "FoxyProxy"
    for name in names:
        wanted = _firefox_files(pem) if name == kit.FIREFOX else _chromium_files(name, pem)
        if wanted is None:
            log.append("Firefox: %s is not valid JSON, left alone. Fix or delete it and try again." % firefox.POLICY)
            continue
        wanted = [(content, path) for content, path in wanted if not _same(content, path)]
        if wanted:
            files += wanted
            new.append(name)
        else:
            log.append("%s: %s set already." % (name, what))
    if not files:
        return True, log
    if not core.root_files(files):
        return False, log + ["%s NOT set in %s (cancelled or failed)." % (what, ", ".join(new))]
    return True, log + ["%s set in %s: restart the browser." % (what, ", ".join(new))]


def setup(number, names, trust=True):
    """The whole job: Burp on the port, FoxyProxy in the browsers, Burp's certificate trusted there
    (trust=False leaves the certificate out), the import file. Returns (worked, log lines)."""
    log, pem = write_configs(number), ""
    if trust and names:
        pem = ca_certificate(number)
        if pem:
            with open(CERT, "w") as f:
                f.write(pem)
        else:
            log.append("Burp's certificate: not found. Burp makes it the first time it starts: start Burp once, "
                       "then set up again. Until then HTTPS sites show a warning through Burp.")
    ok, more = setup_browsers(names, pem) if names else (True, [])
    log += more
    log.append("Last step, once per browser: FoxyProxy > Options > Import > choose %s > Save. Then pick Burp in "
               "FoxyProxy's menu to send the browser through Burp, Disable to stop."
               % IMPORT.replace(core.HOME, "~"))
    return ok, log
