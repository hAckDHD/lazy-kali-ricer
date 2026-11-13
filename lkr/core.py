"""Settings, themes, app detection, config rendering and debug helpers (no GUI code)."""
import colorsys
import configparser
import getpass
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
TREE = os.path.join(REPO, "config")
HOME = os.path.expanduser("~")
CFG = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
OWN = os.path.join(CFG, "lazy-kali-ricer")
SETTINGS = os.path.join(OWN, "settings.json")
MANIFEST = os.path.join(OWN, "manifest.json")  # files we wrote; anything else gets backed up first
USER_THEMES = os.path.join(OWN, "themes.json")  # themes saved from the GUI: {name: theme}
PACKAGES = os.path.join(OWN, "installed-packages")  # packages install.sh added, one per line
COMMAND = os.path.join(HOME, ".local", "bin", "lazy-kali-ricer")
CACHE = os.path.join(HOME, ".cache", "lazy-kali-ricer")
DEBUG_LOG = os.path.join(CACHE, "hyprland.log")
THUMBS = os.path.join(HOME, ".cache", "cliphist-thumbs")

EFFECTS = {
    "static": "Static (one colour)",
    "gradient": "Gradient (primary into accent)",
    "spin": "Spinning gradient",
    "chasing": "Chasing light",
    "dual": "Dual chase (primary + accent)",
    "rgb": "RGB rainbow",
}

# Each theme is colours and style only. Wallpaper is a matching solid colour; pick an
# image in the GUI if you want one.
THEMES = {
    "hAckDHD's theme": dict(primary="#39ff14", accent="#c77dff", inactive="#2ea043", border_size=3, effect="chasing",
                            speed=5, rounding=8, gaps_in=5, gaps_out=12, glow=True, wallpaper_color="#0a0f0b"),
    "Kali Dragon": dict(primary="#2777ff", accent="#00e5ff", inactive="#1f3a66", border_size=2, effect="gradient",
                        speed=5, rounding=10, gaps_in=5, gaps_out=12, glow=True, wallpaper_color="#0b1220"),
    "Synthwave": dict(primary="#ff2bd6", accent="#00f0ff", inactive="#5b2a86", border_size=3, effect="dual",
                      speed=6, rounding=12, gaps_in=6, gaps_out=14, glow=True, wallpaper_color="#140a1f"),
    "Blood Moon": dict(primary="#ff3b3b", accent="#ff9f1c", inactive="#5c1a1a", border_size=3, effect="spin",
                       speed=4, rounding=4, gaps_in=4, gaps_out=10, glow=True, wallpaper_color="#120808"),
    "RGB Rig": dict(primary="#00ffd0", accent="#ff00aa", inactive="#333a44", border_size=4, effect="rgb",
                    speed=7, rounding=10, gaps_in=5, gaps_out=12, glow=False, wallpaper_color="#0c0f12"),
    "Ghost": dict(primary="#e6e6e6", accent="#9aa5b1", inactive="#3a3f48", border_size=1, effect="static",
                  speed=5, rounding=0, gaps_in=2, gaps_out=4, glow=False, wallpaper_color="#101214"),
}
DEFAULT_THEME = "hAckDHD's theme"
CUSTOM = "Custom"



def user_themes():
    try:
        with open(USER_THEMES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def all_themes():
    """Built-in themes, then the ones saved from the GUI."""
    return {**user_themes(), **THEMES}


def save_theme(name, s):
    """Keep the current look under a name. s=None deletes that saved theme."""
    themes = user_themes()
    if s is None:
        themes.pop(name, None)
    else:
        themes[name] = {key: s[key] for key in THEMES[DEFAULT_THEME]}
    os.makedirs(OWN, exist_ok=True)
    with open(USER_THEMES, "w") as f:
        json.dump(themes, f, indent=2)


# role -> (label, keys shown in the GUI, .desktop categories that qualify)
ROLES = {
    "terminal": ("Terminal", "Super+Enter", {"TerminalEmulator"}),
    "browser": ("Browser", "Super+E", {"WebBrowser"}),
    "files": ("File manager", "Super+F", {"FileManager"}),
    "music": ("Music / media", "Super+S", {"Music", "Player"}),
    "calculator": ("Calculator", "Calculator key", {"Calculator"}),
    "monitor": ("Task manager", "Ctrl+Shift+Esc", {"Monitor"}),
}
# Common apps per role, as (name, apt package). The package name is also the command.
CATALOG = {
    "terminal": [("Kitty", "kitty"), ("Alacritty", "alacritty"), ("Foot", "foot"), ("QTerminal", "qterminal"),
                 ("Xfce Terminal", "xfce4-terminal"), ("Konsole", "konsole")],
    "browser": [("Firefox ESR", "firefox-esr"), ("Chromium", "chromium"), ("Brave", "brave-browser"),
                ("Tor Browser", "torbrowser-launcher")],
    "files": [("Thunar", "thunar"), ("Nemo", "nemo"), ("PCManFM", "pcmanfm"), ("Dolphin", "dolphin"), ("Files (Nautilus)", "nautilus")],
    "music": [("VLC", "vlc"), ("mpv", "mpv"), ("Rhythmbox", "rhythmbox"), ("Audacious", "audacious"), ("Strawberry", "strawberry"),
              ("Spotify", "spotify-client"), ("Amazon Music (web app)", "web:amazon-music"),
              ("YouTube Music (web app)", "web:youtube-music")],
    "calculator": [("Galculator", "galculator"), ("GNOME Calculator", "gnome-calculator"), ("Qalculate!", "qalculate-gtk"),
                   ("MATE Calculator", "mate-calc")],
    "monitor": [("Xfce Task Manager", "xfce4-taskmanager"), ("GNOME System Monitor", "gnome-system-monitor"),
                ("MATE System Monitor", "mate-system-monitor"), ("LXTask", "lxtask")],
}
# More to install from the Get apps tab, without a keyboard shortcut of their own. heading -> [(name, apt package)]
EXTRAS = {
    "Code editor": [("VS Code (Microsoft)", "code"), ("VSCodium (no Microsoft telemetry or branding)", "codium")],
    "Notes and text editors": [("Obsidian", "obsidian"), ("Sublime Text", "sublime-text"), ("Joplin", "joplin"),
                               ("CherryTree", "cherrytree")],
    "Screenshots and recording": [("Flameshot", "flameshot"), ("grim (plain capture)", "grim"), ("slurp (region picker)", "slurp"),
                                  ("Swappy (annotate)", "swappy"), ("Ksnip", "ksnip"), ("OBS Studio", "obs-studio"),
                                  ("wf-recorder", "wf-recorder")],
    # Discord has one official app. Vesktop and WebCord are open-source apps that load the real discord.com;
    # nothing here is a look-alike site or a relay.
    "Chat": [("Discord (official)", "discord"), ("Vesktop (Discord with Vencord built in)", "vesktop"),
             ("WebCord (Discord, privacy-hardened)", "webcord"), ("Signal", "signal-desktop"),
             ("Element (Matrix)", "element-desktop"), ("Nheko (Matrix)", "nheko"),
             ("WasIstLos (WhatsApp, unofficial)", "wasistlos"), ("Telegram", "telegram-desktop")],
    "Everyday": [("LibreOffice", "libreoffice"), ("GIMP", "gimp"), ("Thunderbird", "thunderbird"), ("KeePassXC", "keepassxc"),
                 ("qBittorrent", "qbittorrent"), ("Remmina (remote desktop)", "remmina"), ("btop", "btop"),
                 ("fastfetch", "fastfetch"), ("Timeshift (system snapshots)", "timeshift"), ("GParted", "gparted")],
    "Wordlists": [("SecLists", "seclists")],
}
# Services with no Linux app of their own: the real site in its own window, opened by a browser you have.
# "web:<key>" in the catalog -> (name, address, icon)
WEBAPPS = {
    "amazon-music": ("Amazon Music", "https://music.amazon.com/", "amazon"),
    "youtube-music": ("YouTube Music", "https://music.youtube.com/", "youtube"),
}
WEBAPP_BROWSERS = ["brave-browser", "chromium", "google-chrome"]  # the ones with an --app window mode
LAUNCHERS = os.path.join(HOME, ".local", "share", "applications")
# Icon-theme names to try for a package, best first, when the package name itself is not one.
ICONS = {"spotify-client": "spotify-client spotify", "web:amazon-music": "amazon-music amazon multimedia-audio-player",
         "web:youtube-music": "youtube-music youtube multimedia-audio-player",
         "firefox-esr": "firefox", "torbrowser-launcher": "torbrowser", "code": "vscode visual-studio-code",
         "codium": "vscodium visual-studio-code", "xfce4-terminal": "org.xfce.terminal",
         "xfce4-taskmanager": "org.xfce.taskmanager", "nautilus": "org.gnome.Nautilus",
         "gnome-calculator": "org.gnome.Calculator", "qalculate-gtk": "qalculate",
         "gnome-system-monitor": "org.gnome.SystemMonitor", "mate-system-monitor": "utilities-system-monitor",
         "mate-calc": "accessories-calculator", "obs-studio": "com.obsproject.Studio obs",
         "wasistlos": "com.github.xeco23.WasIstLos whatsapp", "telegram-desktop": "telegram",
         "libreoffice": "libreoffice-startcenter", "remmina": "org.remmina.Remmina", "grim": "applets-screenshooter",
         "slurp": "applets-screenshooter", "swappy": "swappy applets-screenshooter", "wf-recorder": "media-record",
         "seclists": "text-x-generic", "konsole": "utilities-terminal", "foot": "foot utilities-terminal",
         "dolphin": "system-file-manager", "pcmanfm": "system-file-manager", "thunar": "org.xfce.thunar",
         "strawberry": "strawberry multimedia-audio-player", "joplin": "joplin accessories-text-editor",
         "vesktop": "vesktop discord", "webcord": "webcord discord", "nheko": "nheko internet-chat",
         "btop": "btop utilities-system-monitor", "fastfetch": "utilities-terminal"}
# Apps whose maker publishes a .deb but no apt repository: package -> download address,
# or "github:owner/repo" for the newest release there.
DEBS = {
    "discord": "https://discord.com/api/download?platform=linux&format=deb",
    "vesktop": "github:Vencord/Vesktop",
    "webcord": "github:SpacingBat3/WebCord",
}
VENCORD_CLI = "https://github.com/Vencord/Installer/releases/latest/download/VencordInstallerCli-linux"
DISCORD_DIR = "/usr/share/discord/resources"
# Packages that live in their maker's own apt repository, added when you install them:
# package -> (signing key URL, key file, sources line, sources file, extra root step or "")
REPOS = {
    "brave-browser": ("https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg",
                      "/usr/share/keyrings/brave-browser-archive-keyring.gpg",
                      "https://brave-browser-apt-release.s3.brave.com/ stable main",
                      "/etc/apt/sources.list.d/brave-browser-release.list", ""),
    # The package would otherwise add a second, clashing entry for the same repository.
    "code": ("https://packages.microsoft.com/keys/microsoft.asc", "/usr/share/keyrings/microsoft-vscode.asc",
             "https://packages.microsoft.com/repos/code stable main", "/etc/apt/sources.list.d/microsoft-vscode.list",
             "echo code code/add-microsoft-repo boolean false | debconf-set-selections"),
    "codium": ("https://gitlab.com/paulcarroty/vscodium-deb-rpm-repo/raw/master/pub.gpg",
               "/usr/share/keyrings/vscodium-archive-keyring.asc",
               "https://download.vscodium.com/debs vscodium main", "/etc/apt/sources.list.d/vscodium.list", ""),
    "sublime-text": ("https://download.sublimetext.com/sublimehq-pub.gpg", "/usr/share/keyrings/sublimehq-pub.asc",
                     "https://download.sublimetext.com/ apt/stable/", "/etc/apt/sources.list.d/sublime-text.list", ""),
    "signal-desktop": ("https://updates.signal.org/desktop/apt/keys.asc", "/usr/share/keyrings/signal-desktop-keyring.asc",
                       "https://updates.signal.org/desktop/apt xenial main", "/etc/apt/sources.list.d/signal-desktop.list", ""),
    "element-desktop": ("https://packages.element.io/debian/element-io-archive-keyring.gpg",
                        "/usr/share/keyrings/element-io-archive-keyring.gpg",
                        "https://packages.element.io/debian/ default main", "/etc/apt/sources.list.d/element-io.list", ""),
    # Spotify signs with a new key about once a year; when installs start failing, update the key id here.
    "spotify-client": ("https://download.spotify.com/debian/pubkey_5384CE82BA52C83A.gpg",
                       "/usr/share/keyrings/spotify-5384CE82BA52C83A.gpg",
                       "https://repository.spotify.com stable non-free", "/etc/apt/sources.list.d/spotify.list", ""),
    "gh": ("https://cli.github.com/packages/githubcli-archive-keyring.gpg",
           "/usr/share/keyrings/githubcli-archive-keyring.gpg",
           "https://cli.github.com/packages stable main", "/etc/apt/sources.list.d/github-cli.list", ""),
}
NEEDED = ["Hyprland", "waybar", "wofi", "mako", "swaybg", "hyprlock", "cliphist", "wl-paste", "grim", "slurp", "jq"]


# ---------- installed programs ----------
def _clean_exec(cmdline):
    """Desktop-entry Exec line without field codes (%U, @@u ...)."""
    try:
        words = shlex.split(cmdline)
    except ValueError:
        return ""
    return shlex.join(w for w in words if not w.startswith("%") and w not in ("@@", "@@u"))


def detect_apps():
    """{role: [(name, command), ...]} for every installed app with a launcher entry."""
    from gi.repository import Gio

    found = {role: [] for role in ROLES}
    for info in Gio.AppInfo.get_all():
        if info.get_nodisplay():
            continue
        cats = set((info.get_categories() or "").split(";"))
        cmd = _clean_exec(info.get_commandline() or "")
        if not cmd or cmd.startswith("pkexec"):
            continue
        for role, (_label, _keys, want) in ROLES.items():
            if cats & want and cmd not in [c for _n, c in found[role]]:
                found[role].append((info.get_name(), cmd))
    for role, common in CATALOG.items():  # installed common apps whose launcher entry lacks the category
        known = {os.path.basename(shlex.split(c)[0]) for _n, c in found[role]}
        found[role] += [(name, cmd) for name, cmd in common if cmd not in known and shutil.which(cmd)]
    for apps in found.values():
        apps.sort(key=lambda a: a[0].lower())
    music = {c for i in Gio.AppInfo.get_all() if "Music" in (i.get_categories() or "").split(";")
             for c in [_clean_exec(i.get_commandline() or "")]}
    found["music"].sort(key=lambda a: a[1] not in music)  # music apps before general media players
    return found


def _system_default(role):
    """Command of the system's default app for a role, when the system has one."""
    from gi.repository import Gio

    if role == "terminal":
        path = shutil.which("x-terminal-emulator")
        return os.path.basename(os.path.realpath(path)) if path else ""
    mime = {"browser": "x-scheme-handler/http", "files": "inode/directory"}.get(role)
    info = Gio.AppInfo.get_default_for_type(mime, False) if mime else None
    return _clean_exec(info.get_commandline() or "") if info else ""


def default_apps(found):
    apps = {}
    for role, choices in found.items():
        cmds = [c for _n, c in choices]
        default = _system_default(role)
        match = [c for c in cmds if c == default or os.path.basename(shlex.split(c)[0]) == default]
        match = match or [c for c in cmds if c in dict(CATALOG[role]).values()]  # else a well-known app
        apps[role] = (match or cmds or [""])[0]
    apps["screenshot"] = "flameshot" if shutil.which("flameshot") else "grim"
    return apps


def detect_kb_layout():
    try:
        with open("/etc/default/keyboard") as f:
            for line in f:
                if line.startswith("XKBLAYOUT="):
                    return line.split("=", 1)[1].strip().strip('"') or "us"
    except OSError:
        pass
    return "us"


def hardware():
    """One line: CPU vendor, graphics vendors, laptop or desktop."""
    cpu = _quiet("sh", "-c", "grep -m1 vendor_id /proc/cpuinfo")
    cpu = "AMD" if "AMD" in cpu else "Intel" if "Intel" in cpu else "unknown"
    lspci = _quiet("sh", "-c", "lspci | grep -iE 'vga|3d controller|display controller'").lower()
    gpus = [name for name, mark in (("NVIDIA", "nvidia"), ("AMD", "amd"), ("AMD", "advanced micro"), ("Intel", "intel"))
            if mark in lspci]
    gpus = list(dict.fromkeys(gpus)) or ["unknown"]
    if "NVIDIA" in gpus:
        gpus[gpus.index("NVIDIA")] = "NVIDIA (%s driver)" % ("proprietary" if os.path.isdir("/sys/module/nvidia") else "nouveau")
    laptop = any(n.startswith("BAT") for n in os.listdir("/sys/class/power_supply")) if os.path.isdir("/sys/class/power_supply") else False
    return "%s CPU, %s graphics, %s" % (cpu, " + ".join(gpus), "laptop" if laptop else "desktop")


def package_state(packages):
    """{package: "installed" | "available" | "missing"}. "available" includes packages whose
    repository gets added on install (REPOS) or that are downloaded from their maker (DEBS)."""
    web = [p for p in packages if p.startswith("web:")]
    packages = [p for p in packages if p not in web]
    state, name = dict.fromkeys(packages, "missing"), None
    state.update((p, "installed" if os.path.exists(_launcher(p[4:])) else "available") for p in web)
    for line in _quiet("apt-cache", "policy", *packages).splitlines():
        if not line.startswith(" "):
            name = line.rstrip(":")
        elif line.strip().startswith("Installed:") and "(none)" not in line:
            state[name] = "installed"
        elif line.strip().startswith("Candidate:") and "(none)" not in line and state.get(name) == "missing":
            state[name] = "available"
    for pkg in packages:
        if state[pkg] == "missing" and (pkg in REPOS or pkg in DEBS):
            state[pkg] = "available"
    return state


def catalog_state():
    """package_state() for every catalog package."""
    return package_state([pkg for common in list(CATALOG.values()) + list(EXTRAS.values()) for _name, pkg in common])


DIALOGS = False  # the GUI sets this: ask for the password in a dialog even when started from a terminal
AGENT = "/usr/libexec/hyprpolkitagent"


def _password_dialog():
    """True when a polkit agent is there to show a password dialog; starts ours if none is running."""
    names = "hyprpolkitagent|polkit-gnome-au|polkit-kde-auth|polkit-mate-auth|lxpolkit|xfce-polkit|lxqt-policykit"
    running = lambda: subprocess.run(["pgrep", "-f", names], capture_output=True).returncode == 0
    if not running() and os.path.exists(AGENT) and os.environ.get("WAYLAND_DISPLAY"):
        _spawn(AGENT)
        time.sleep(1.5)
    return running()


def _root(script):
    """Run a shell script as root. The GUI asks in a password dialog; a terminal command asks with sudo."""
    tty = sys.stdin.isatty()
    tool = "pkexec" if (DIALOGS and _password_dialog()) or not tty else "sudo"
    return subprocess.run([tool, "sh", "-c", script]).returncode == 0


def _apt(action, packages):
    """apt-get install/remove with an admin prompt."""
    return _root("apt-get %s -y %s" % (action, shlex.join(packages)))


def _launcher(key):
    return os.path.join(LAUNCHERS, "lazy-kali-ricer-%s.desktop" % key)


def add_webapp(key):
    """Give a web-only service its own launcher: the real site in a window of its own. Returns True when
    done, False when no browser that can do it is installed."""
    name, url, icon = WEBAPPS[key]
    browser = next((b for b in WEBAPP_BROWSERS if shutil.which(b)), "")
    if not browser:
        return False
    os.makedirs(LAUNCHERS, exist_ok=True)
    with open(_launcher(key), "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=%s\nComment=%s in its own window\n"
                "Exec=%s --app=%s\nIcon=%s\nCategories=AudioVideo;Audio;Music;Player;\n"
                % (name, url.split("/")[2], browser, url, icon))
    return True


def _fetch(url, suffix=""):
    """Download to a temporary file anyone can read (apt reads it as its own user). Returns the path."""
    import tempfile
    import urllib.request

    ask = urllib.request.Request(url, headers={"User-Agent": "lazy-kali-ricer"})
    with urllib.request.urlopen(ask, timeout=60) as reply, tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(reply, tmp)
    os.chmod(tmp.name, 0o644)
    return tmp.name


def _no_candidate(pkg):
    """True when apt has nothing to install under this name."""
    return "Candidate: (none)" in (_quiet("apt-cache", "policy", pkg) or "Candidate: (none)")


def _deb_url(pkg, arch):
    source = DEBS[pkg]
    if not source.startswith("github:"):
        return source
    listing = _fetch("https://api.github.com/repos/%s/releases/latest" % source[7:])
    try:
        with open(listing) as f:
            assets = json.load(f).get("assets", [])
    finally:
        os.remove(listing)
    for asset in assets:
        if asset["name"].endswith("%s.deb" % arch):
            return asset["browser_download_url"]
    raise OSError("%s has no %s package" % (pkg, arch))


def install_packages(packages):
    """Install apps picked in the GUI and remember them, so uninstall can offer to remove them.
    Adds the maker's apt repository (REPOS) or downloads the maker's .deb (DEBS) where apt has none."""
    arch = _quiet("dpkg", "--print-architecture").strip() or "amd64"
    steps, temps, targets = [], [], []
    try:
        for pkg in packages:
            if pkg in DEBS and _no_candidate(pkg):
                temps.append(_fetch(_deb_url(pkg, arch), ".deb"))
                targets.append(temps[-1])
                continue
            targets.append(pkg)
            if pkg in REPOS and _no_candidate(pkg):
                key_url, key_file, source, list_file, extra = REPOS[pkg]
                temps.append(_fetch(key_url))
                line = "deb [arch=%s signed-by=%s] %s" % (arch, key_file, source)
                steps += ["install -D -m 644 %s %s" % (shlex.quote(temps[-1]), shlex.quote(key_file)),
                          "echo %s > %s" % (shlex.quote(line), shlex.quote(list_file))] + ([extra] if extra else [])
        if steps:
            steps.append("apt-get update")
        ok = _root(" && ".join(steps + ["DEBIAN_FRONTEND=noninteractive apt-get install -y %s" % shlex.join(targets)]))
    except (OSError, ValueError):  # a download failed
        ok = False
    for path in temps:
        os.remove(path)
    if not ok:
        return False
    os.makedirs(OWN, exist_ok=True)
    with open(PACKAGES, "a") as f:
        f.write("".join("\n%s" % p for p in packages))
    return True


def _discord_resources():
    """Folder holding Discord's app.asar: in your home for current Discord (it downloads itself there on
    first start), under /usr/share for older packages. "" when there is none yet."""
    import glob

    mine = sorted(glob.glob(os.path.join(CFG, "discord", "app-*", "resources")), key=os.path.getmtime)
    return mine[-1] if mine else DISCORD_DIR if os.path.isdir(DISCORD_DIR) else ""


def vencord_state():
    """"" = Discord not installed, "unopened" = installed but never started (nothing to patch yet),
    "plain" = ready for Vencord, "patched" = Vencord is in."""
    if not shutil.which("discord"):
        return ""
    where = _discord_resources()
    if not where:
        return "unopened"
    return "patched" if os.path.exists(os.path.join(where, "_app.asar")) else "plain"


def discord_running():
    return subprocess.run(["pgrep", "-x", "Discord"], capture_output=True).returncode == 0


def vencord(action="install"):
    """Add Vencord to the official Discord app (or "uninstall" it) with Vencord's own installer.
    Discord must be closed. A Discord update can undo it; then run this again."""
    try:
        cli = _fetch(VENCORD_CLI)
    except OSError:
        return False
    os.chmod(cli, 0o755)
    try:
        if _discord_resources().startswith(HOME):
            return subprocess.run([cli, "-" + action, "-branch", "stable"], capture_output=True).returncode == 0
        # Older Discord lives in /usr/share: the installer needs root, but keeps Vencord's files in your home.
        env = "env SUDO_USER=%s SUDO_UID=%d SUDO_GID=%d HOME=%s XDG_CONFIG_HOME=%s" % (
            shlex.quote(getpass.getuser()), os.getuid(), os.getgid(), shlex.quote(HOME), shlex.quote(CFG))
        return _root("%s %s -%s -branch stable" % (env, shlex.quote(cli), action))
    finally:
        os.remove(cli)


def current_binds():
    """Every shortcut Hyprland has now, as (keys, what it does), in config order."""
    try:
        binds = json.loads(_quiet("hyprctl", "binds", "-j") or "[]")
    except ValueError:
        return []
    rows = []
    for b in binds:
        if b.get("description"):
            key = {"mouse:272": "Left-drag", "mouse:273": "Right-drag"}.get(b["key"], b["key"])
            mods = [name for bit, name in ((64, "SUPER"), (4, "CTRL"), (8, "ALT"), (1, "SHIFT")) if b["modmask"] & bit]
            rows.append((" + ".join(mods + [key]), b["description"]))
    return rows


def live_monitors():
    """Connected screens as Hyprland has them now: position, mode, and size in layout units."""
    try:
        found = json.loads(_quiet("hyprctl", "monitors", "-j") or "[]")
    except ValueError:
        return []
    screens = []
    for m in found:
        w, h = m["width"] / m["scale"], m["height"] / m["scale"]
        if m.get("transform", 0) % 2:  # rotated a quarter turn
            w, h = h, w
        modes = list(dict.fromkeys(mode.replace("Hz", "") for mode in m.get("availableModes", [])))
        screens.append(dict(name=m["name"], x=m["x"], y=m["y"], w=round(w), h=round(h), scale=m["scale"],
                            transform=m.get("transform", 0), modes=modes,
                            mode="%dx%d@%.2f" % (m["width"], m["height"], m["refreshRate"])))
    return screens


# ---------- settings ----------
def defaults():
    # shortcuts: [{"keys": "SUPER + b", "cmd": "...", "desc": "..."}]
    # monitors: [] = place automatically, else [{"name", "mode", "x", "y", "scale", "transform"}]
    s = dict(THEMES[DEFAULT_THEME], theme=DEFAULT_THEME, wallpaper_mode="color", wallpaper_image="",
             wallpaper_dir="", wallpaper_minutes=10,
             clip_enabled=True, clip_max=200, clip_images=True, clip_skip_sensitive=False, clip_wipe_login=False,
             keys_on_login=True, shortcuts=[], monitors=[], debug=False, kb_layout=detect_kb_layout())
    s["apps"] = default_apps(detect_apps())
    return s


def load():
    s = defaults()
    try:
        with open(SETTINGS) as f:
            saved = json.load(f)
        s["apps"].update(saved.pop("apps", {}))
        s.update(saved)
    except (OSError, ValueError):
        pass
    return s


def save(s):
    os.makedirs(OWN, exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump(s, f, indent=2)


# ---------- colours ----------
def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgba(hex_colour, alpha="ff"):
    return "rgba(%s%s)" % (hex_colour.lstrip("#"), alpha)


def effect_colours(s):
    """Hex colours of the focused-window border gradient, in order."""
    p, a = s["primary"], s["accent"]
    dark = lambda c: "#%02x%02x%02x" % tuple(int(v * 0.25) for v in _rgb(c))
    if s["effect"] in ("gradient", "spin"):
        return [p, a]
    if s["effect"] == "chasing":
        return [p, dark(p), dark(p), p]
    if s["effect"] == "dual":
        return [p, dark(p), a, dark(a)]
    if s["effect"] == "rgb":
        return ["#%02x%02x%02x" % tuple(int(v * 255) for v in colorsys.hsv_to_rgb(i / 8, 1, 1)) for i in range(9)]
    return [p]


# ---------- commands started with the session ----------
def _rotate_cmd(s):
    """Command that shows the next image of the rotation folder; "" when rotation is off."""
    if s["wallpaper_mode"] != "rotate" or not os.path.isdir(s["wallpaper_dir"]):
        return ""
    return "%s %s" % (shlex.quote(os.path.join(CFG, "hypr", "scripts", "wallpaper-rotate.sh")), shlex.quote(s["wallpaper_dir"]))


def wallpaper_cmds(s):
    if _rotate_cmd(s):  # with 0 minutes it shows one image and waits for the key
        return ["%s %d" % (_rotate_cmd(s), int(s["wallpaper_minutes"]) * 60)]
    if s["wallpaper_mode"] == "image" and os.path.isfile(s["wallpaper_image"]):
        return ["swaybg -i %s -m fill" % shlex.quote(s["wallpaper_image"])]
    return ["swaybg -c %s" % shlex.quote(s["wallpaper_color"])]


def clipboard_cmds(s):
    """Clipboard history watchers. Everything copied is kept by default; skipping
    password-manager entries and wiping at login are opt-in."""
    if not s["clip_enabled"]:
        return []
    store = "cliphist -max-items %d store" % s["clip_max"]
    if s["clip_skip_sensitive"]:  # wl-paste sets CLIPBOARD_STATE=sensitive for password-manager copies
        store = "sh -c %s" % shlex.quote('[ "$CLIPBOARD_STATE" = sensitive ] || ' + store)
    cmds = ["wl-paste --type text --watch " + store]
    if s["clip_images"]:
        cmds.append("wl-paste --type image --watch " + store)
    if s["clip_wipe_login"]:
        cmds.insert(0, 'cliphist wipe; rm -rf "$HOME/.cache/cliphist-thumbs"')
    return cmds


def debug_cmds(s):
    if not s["debug"]:
        return []
    return ['mkdir -p %s && tail -n +1 -F "$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/hyprland.log" > %s'
            % (shlex.quote(CACHE), shlex.quote(DEBUG_LOG))]


# ---------- rendering ----------
def _lua(value, indent=1):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # JSON string escapes are valid Lua
    pad = "    " * indent
    if isinstance(value, dict):
        rows = ["%s%s = %s," % (pad, k, _lua(v, indent + 1)) for k, v in value.items()]
    else:
        rows = ["%s%s," % (pad, _lua(v, indent + 1)) for v in value]
    return "{\n%s\n%s}" % ("\n".join(rows), "    " * (indent - 1))


def settings_lua(s):
    speed = max(1, min(10, int(s["speed"])))
    border = {
        "size": int(s["border_size"]),
        "colors": [_rgba(c) for c in effect_colours(s)],
        "inactive": _rgba(s["inactive"], "cc"),
        "loop": s["effect"] in ("spin", "chasing", "dual", "rgb"),
        "speed": 130 - 12 * speed,  # deciseconds per turn; higher is slower
    }
    table = {
        "apps": s["apps"],
        "kb_layout": s["kb_layout"],
        "border": border,
        "rounding": int(s["rounding"]),
        "gaps_in": int(s["gaps_in"]),
        "gaps_out": int(s["gaps_out"]),
        "glow": {"enabled": bool(s["glow"]), "color": _rgba(s["primary"], "66")},
        "clipboard": bool(s["clip_enabled"]),
        "wallpaper_next": _rotate_cmd(s),
        "monitors": [{"output": m["name"], "mode": m["mode"], "position": "%dx%d" % (m["x"], m["y"]),
                      "scale": m["scale"], "transform": m.get("transform", 0)} for m in s["monitors"]],
        "shortcuts": [{"keys": sc["keys"], "cmd": sc["cmd"], "desc": sc.get("desc") or sc["cmd"]}
                      for sc in s["shortcuts"] if sc.get("keys") and sc.get("cmd")],
        "autostart": wallpaper_cmds(s) + clipboard_cmds(s) + debug_cmds(s),
    }
    return "-- Written by lazy-kali-ricer. Change these in the GUI (or here, then `hyprctl reload`).\nreturn %s\n" % _lua(table)


def hyprlock_conf(s):
    if s["wallpaper_mode"] == "image" and os.path.isfile(s["wallpaper_image"]):
        background = "path = %s\n    blur_passes = 2" % s["wallpaper_image"]
    elif _rotate_cmd(s):  # whatever image is up at that moment, blurred
        background = "path = screenshot\n    blur_passes = 3"
    else:
        background = "color = rgb(%s)" % s["wallpaper_color"].lstrip("#")
    return """# Written by lazy-kali-ricer.
background {
    %s
}

input-field {
    size = 300, 50
    position = 0, -80
    halign = center
    valign = center
    outline_thickness = %d
    outer_color = rgb(%s)
    inner_color = rgb(15181c)
    font_color = rgb(d7e9df)
    check_color = rgb(%s)
    fail_color = rgb(ff5370)
}
""" % (background, max(1, int(s["border_size"])), s["primary"].lstrip("#"), s["accent"].lstrip("#"))


def _manifest():
    """{config file we wrote: backup of what was there before, or None}"""
    try:
        with open(MANIFEST) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(path, text, mode, managed, stamp):
    """Write a config file. A file we did not write before is moved to *.bak-<stamp> first."""
    if path not in managed:
        managed[path] = None
        if os.path.lexists(path):
            managed[path] = "%s.bak-%s" % (path, stamp)
            os.rename(path, managed[path])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)
    return managed[path]


def _flameshot(s):
    """Wayland-safe capture settings and theme colour, keeping the user's other options.
    Returns the file's path when it did not exist before (then uninstall may delete it)."""
    path = os.path.join(CFG, "flameshot", "flameshot.ini")
    created = not os.path.exists(path)
    ini = configparser.RawConfigParser()
    ini.optionxform = str
    ini.read(path)
    if not ini.has_section("General"):
        ini.add_section("General")
    for key, value in (("useX11LegacyScreenshot", "false"), ("captureActiveMonitor", "false"), ("showHelp", "false"),
                       ("showStartupLaunchMessage", "false"), ("showDesktopNotification", "false"),
                       ("uiColor", s["primary"]), ("contrastUiColor", "#000000")):
        ini.set("General", key, value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        ini.write(f, space_around_delimiters=False)
    return path if created else None


def apply(s):
    """Save settings, write every config file, reload the running session. Returns log lines."""
    save(s)
    managed = _manifest()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tokens = {
        "__LKR_PRIMARY_RGB__": "%d, %d, %d" % _rgb(s["primary"]),
        "__LKR_ACCENT_RGB__": "%d, %d, %d" % _rgb(s["accent"]),
        "__LKR_PRIMARY__": s["primary"],
        "__LKR_ACCENT__": s["accent"],
    }
    files = []  # (destination, text, mode)
    for root, _dirs, names in os.walk(TREE):
        for name in names:
            src = os.path.join(root, name)
            with open(src) as f:
                text = f.read()
            for token, value in tokens.items():
                text = text.replace(token, value)
            files.append((os.path.join(CFG, os.path.relpath(src, TREE)), text, os.stat(src).st_mode & 0o777))
    files.append((os.path.join(CFG, "hypr", "settings.lua"), settings_lua(s), 0o644))
    files.append((os.path.join(CFG, "hypr", "hyprlock.conf"), hyprlock_conf(s), 0o644))

    log = []
    for path, text, mode in files:
        first = path not in managed
        backup = _write(path, text, mode, managed, stamp)
        if first and backup:
            log.append("Backed up %s" % backup.replace(HOME, "~"))
    old_bar = os.path.join(CFG, "waybar", "config")  # old-style file confuses waybar if both exist
    if os.path.lexists(old_bar):
        managed[old_bar] = "%s.bak-%s" % (old_bar, stamp)  # so uninstall puts it back
        os.rename(old_bar, managed[old_bar])
    if s["apps"]["screenshot"] == "flameshot":
        managed.update(dict.fromkeys(filter(None, [_flameshot(s)])))
    with open(MANIFEST, "w") as f:
        json.dump(managed, f, indent=2, sort_keys=True)
    log.append("Wrote %d config files to %s" % (len(files), CFG.replace(HOME, "~")))

    missing = [b for b in NEEDED + (["flameshot"] if s["apps"]["screenshot"] == "flameshot" else []) if not shutil.which(b)]
    if missing:
        log.append("Not installed: %s. Run ./install.sh" % ", ".join(missing))
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        reload_session(s)
        log.append("Reloaded Hyprland, bar, wallpaper and clipboard.")
    else:
        log.append("Log in to a Hyprland session to see it.")
    return log


def _spawn(cmd):
    subprocess.Popen(["sh", "-c", cmd], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _quiet(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def reload_session(s):
    _quiet("hyprctl", "reload")
    _quiet("makoctl", "reload")
    _quiet("pkill", "-f", "wallpaper-rotate.sh")
    for name in ("waybar", "swaybg"):
        _quiet("pkill", "-x", name)
    _quiet("pkill", "-f", "wl-paste .*--watch")
    time.sleep(0.5)
    for cmd in ["waybar"] + wallpaper_cmds(s) + [c for c in clipboard_cmds(s) if c.startswith("wl-paste")]:
        _spawn(cmd)


def wipe_clipboard():
    _quiet("cliphist", "wipe")
    shutil.rmtree(THUMBS, ignore_errors=True)


# ---------- uninstall ----------
def uninstall_items():
    """Everything uninstall can undo, one dict per step: kind, target, label, on (default).
    Config files and our own data are on; packages and clipboard history are off,
    because other programs or you may still want them."""
    short = lambda p: p.replace(HOME, "~")
    items = []
    for path, backup in _manifest().items():
        if backup and os.path.lexists(backup):
            label = "Restore %s  (from %s)" % (short(path), os.path.basename(backup))
        elif os.path.lexists(path):
            label = "Delete %s" % short(path)
        else:
            continue
        items.append(dict(kind="file", target=path, label=label, on=True))
    if os.path.islink(COMMAND) and os.path.realpath(COMMAND).startswith(REPO):
        items.append(dict(kind="path", target=COMMAND, label="Delete the command %s" % short(COMMAND), on=True))
    if os.path.isdir(CACHE):
        items.append(dict(kind="path", target=CACHE, label="Delete debug logs and reports in %s" % short(CACHE), on=True))
    if os.path.isdir(OWN):
        items.append(dict(kind="path", target=OWN, label="Delete saved settings and themes in %s" % short(OWN), on=True))
    for key, (name, _url, _icon) in WEBAPPS.items():
        if os.path.exists(_launcher(key)):
            items.append(dict(kind="path", target=_launcher(key), label="Delete the %s launcher" % name, on=True))
    if os.path.isdir(os.path.join(HOME, ".cache", "cliphist")):
        items.append(dict(kind="clipboard", target="", label="Wipe clipboard history", on=False))
    from . import kit

    for browser, path in kit.policy_files():
        items.append(dict(kind="policy", target=path, on=True,
                          label="Stop installing extensions into %s  (delete %s; asks for your password)" % (browser, path)))
    try:
        with open(PACKAGES) as f:
            packages = f.read().split()
    except OSError:
        packages = []
    for name in packages:
        if _quiet("dpkg-query", "-W", "-f=${Status}", name).endswith(" installed"):
            items.append(dict(kind="package", target=name, label="Remove package %s  (installed by lazy-kali-ricer)" % name, on=False))
    return items


def uninstall(items):
    """Carry out the chosen steps. Returns log lines."""
    log, managed_gone, packages = [], set(), [i["target"] for i in items if i["kind"] == "package"]
    if packages:  # first: it needs the admin password and the package list file
        ok = _apt("remove", packages)
        log.append(("Removed packages: %s" if ok else "Packages NOT removed (cancelled or failed): %s") % " ".join(packages))
        if ok:
            with open(PACKAGES) as f:
                left = [p for p in f.read().split() if p not in packages]
            with open(PACKAGES, "w") as f:
                f.write("\n".join(left))
    policies = [i["target"] for i in items if i["kind"] == "policy"]
    if policies:
        ok = _root("rm -f %s" % shlex.join(policies))
        log.append(("Removed extension lists: %s" if ok else "Extension lists NOT removed (cancelled or failed): %s") % " ".join(policies))
    for item in sorted(items, key=lambda i: i["target"] == OWN):  # settings folder last: it holds the manifest
        kind, target = item["kind"], item["target"]
        if kind == "file":
            backup = _manifest().get(target)
            if os.path.lexists(target):
                os.remove(target)
            if backup and os.path.lexists(backup):
                os.rename(backup, target)
            managed_gone.add(target)
        elif kind == "path":
            shutil.rmtree(target) if os.path.isdir(target) and not os.path.islink(target) else os.remove(target)
        elif kind == "clipboard":
            wipe_clipboard()
        if kind not in ("package", "policy"):
            log.append("Done: %s" % item["label"])
    if managed_gone and os.path.isfile(MANIFEST):  # some files kept: remember only those
        kept = {p: b for p, b in _manifest().items() if p not in managed_gone}
        with open(MANIFEST, "w") as f:
            json.dump(kept, f, indent=2, sort_keys=True)
    if managed_gone:
        log.append("Log out and back in to finish.")
    return log


# ---------- updating ----------
VERSIONS = os.path.join(OWN, "previous-versions")  # the version before each update, one commit per line, newest last


def _git(*args):
    """(worked, output) of a git command in the tool's own folder."""
    try:
        done = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return done.returncode == 0, (done.stdout + done.stderr).strip()


def version(commit="HEAD"):
    """One line for a version: short id, date, what changed. "" when the tool was not installed with git."""
    ok, out = _git("log", "-1", "--format=%h  %cs  %s", commit)
    return out if ok else ""


def previous_versions():
    try:
        with open(VERSIONS) as f:
            return f.read().split()
    except OSError:
        return []


def _hand_edited():
    """Why git must not move the files right now, or "" when it may."""
    if not version():
        return "This copy was not installed with git clone, so it cannot update itself. Download it again instead."
    if _git("status", "--porcelain", "--untracked-files=no")[1]:
        return "Files in %s were changed by hand. Undo or commit those changes first." % REPO.replace(HOME, "~")
    return ""


def update_check():
    """(number of newer versions, text listing them). -1 when GitHub could not be asked."""
    if not version():
        return -1, _hand_edited()
    if not _git("fetch", "--quiet")[0]:
        return -1, "Could not reach GitHub. Are you online?"
    ok, news = _git("log", "--format=%cs  %s", "HEAD..@{u}")
    if not ok:
        return -1, "This copy does not follow a branch on GitHub."
    lines = news.splitlines()
    return len(lines), "\n".join(lines[:8] + ["… and %d more" % (len(lines) - 8)] * (len(lines) > 8))


def update():
    """Move to the newest version on GitHub, remembering the current one so revert() can go back.
    Returns (changed, message). After a change, call reapply() and restart the GUI."""
    count, news = update_check()
    if count < 0:
        return False, news
    if count == 0:
        return False, "Already the newest version: %s" % version()
    problem = _hand_edited()
    if problem:
        return False, problem
    before = _git("rev-parse", "HEAD")[1]
    ok, out = _git("merge", "--ff-only", "@{u}")
    if not ok:
        return False, "Could not update: this copy has its own commits that GitHub does not.\n%s" % out.splitlines()[-1]
    os.makedirs(OWN, exist_ok=True)
    with open(VERSIONS, "a") as f:
        f.write(before + "\n")
    return True, "Updated to %s" % version()


def revert():
    """Go back to the version in use before the last update. Each call goes one update further back.
    Returns (changed, message). After a change, call reapply() and restart the GUI."""
    history = previous_versions()
    if not history:
        return False, "No earlier version to go back to: nothing has been updated from here yet."
    problem = _hand_edited()
    if problem:
        return False, problem
    if _git("log", "--format=%h", "@{u}..HEAD")[1]:
        return False, "This copy has commits of its own that are not on GitHub. Going back would drop them; use git yourself."
    ok, out = _git("reset", "--hard", history[-1])
    if not ok:
        return False, "Could not go back:\n%s" % out
    with open(VERSIONS, "w") as f:
        f.write("".join(h + "\n" for h in history[:-1]))
    return True, "Back on %s" % version()


def reapply():
    """Write the configs again with the code now on disk (not the code this process loaded). Returns its log."""
    done = subprocess.run([sys.executable, os.path.join(REPO, "lazy-kali-ricer"), "apply"], capture_output=True, text=True)
    return done.stdout.strip() or done.stderr.strip()


# ---------- debugging ----------
def set_debug(on):
    """Mirror the Hyprland log to a file that survives a crash or reboot."""
    s = load()
    s["debug"] = on
    save(s)
    lua = os.path.join(CFG, "hypr", "settings.lua")
    if os.path.exists(lua):
        with open(lua, "w") as f:
            f.write(settings_lua(s))
    _quiet("pkill", "-f", "tail -n \\+1 -F .*hyprland.log")
    if on and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        _spawn(debug_cmds(s)[0])
    return s


def debug_report():
    """Collect versions, settings, config errors and the log tail into one text file.
    Home path, user name and host name are replaced so the file is safe to share."""
    s = load()
    parts = [("lazy-kali-ricer debug report", time.strftime("%Y-%m-%d %H:%M:%S"))]
    parts.append(("os", _quiet("sh", "-c", ". /etc/os-release; echo \"$PRETTY_NAME\"; uname -r")))
    parts.append(("hyprland", _quiet("Hyprland", "--version")))
    parts.append(("hardware", hardware() + "\n" + _quiet("sh", "-c", "lspci | grep -iE 'vga|3d|display'")))
    parts.append(("tools", "\n".join("%s: %s" % (b, shutil.which(b) or "MISSING") for b in NEEDED + ["flameshot"])))
    parts.append(("settings.json", json.dumps(s, indent=2)))
    parts.append(("settings.lua", _quiet("cat", os.path.join(CFG, "hypr", "settings.lua"))))
    parts.append(("config errors", _quiet("hyprctl", "configerrors") or "(Hyprland not running)"))
    parts.append(("monitors", _quiet("sh", "-c", "hyprctl monitors | grep -E '^Monitor|^\\s+[0-9]+x[0-9]+@'")))
    live = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "hypr", os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "-"), "hyprland.log")
    for path in (DEBUG_LOG, live):
        if os.path.isfile(path):
            parts.append(("log tail: " + os.path.basename(os.path.dirname(path)), _quiet("tail", "-n", "300", path)))
            break
    else:
        parts.append(("log", "No log found. Run `lazy-kali-ricer debug on`, reproduce the problem, then report again."))
    text = "\n".join("===== %s =====\n%s\n" % (title, body.strip()) for title, body in parts)
    text = text.replace(HOME, "~")
    for secret, shown in ((getpass.getuser(), "USER"), (socket.gethostname(), "HOST")):
        if secret != "kali":  # stock Kali name: not personal, and part of version strings
            text = re.sub(r"\b%s\b" % re.escape(secret), shown, text)
    out = os.path.join(CACHE, "debug-report.txt")
    os.makedirs(CACHE, exist_ok=True)
    with open(out, "w") as f:
        f.write(text)
    return out
