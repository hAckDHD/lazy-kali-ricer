#!/usr/bin/python3 -SE
"""The banner a terminal shows when it opens: art on the left, facts about this system on the right.

Apply copies this file to ~/.config/lazy-kali-ricer/terminal/fetch and writes banner.json next to it
(art already coloured and padded, which facts to show, the two colours). It runs on every new terminal,
so it reads /proc instead of starting other programs and imports as little as it can.
"""
import os
import sys

# key -> (label in the banner, name in the GUI). The order here is the order in the banner.
FACTS = {
    "title": ("", "user@host"),
    "os": ("OS", "System"),
    "kernel": ("Kernel", "Kernel"),
    "uptime": ("Uptime", "Uptime"),
    "packages": ("Packages", "Number of packages"),
    "shell": ("Shell", "Shell"),
    "terminal": ("Term", "Terminal"),
    "cpu": ("CPU", "Processor"),
    "memory": ("Memory", "Memory"),
    "disk": ("Disk", "Disk"),
    "ip": ("IP", "Local IP address"),
    "vpn": ("VPN", "VPN IP address (tun/wg), when connected"),
    "colours": ("", "Colour blocks"),
}
RESET = "\x1b[0m"
SHELLS = ("zsh", "bash", "sh", "dash", "fish", "ksh", "sudo", "su", "login", "tmux", "screen", "python3", "fetch")


def fg(colour, layer=38):
    h = colour.lstrip("#")
    return "\x1b[%d;2;%d;%d;%dm" % (layer, int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _field(text, name, sep):
    """Value of the first line starting with name in a "name<sep>value" file."""
    for line in text.splitlines():
        if line.startswith(name):
            return line.split(sep, 1)[1].strip().strip('"')
    return ""


def _process(pid):
    """(name, parent pid) of a process."""
    stat = _read("/proc/%d/stat" % pid)
    if not stat:
        return "", 0
    return stat[stat.index("(") + 1:stat.rindex(")")], int(stat[stat.rindex(")") + 2:].split()[1])


def _os():
    return _field(_read("/etc/os-release"), "PRETTY_NAME=", "=")


def _uptime():
    try:
        minutes = int(float(_read("/proc/uptime").split()[0])) // 60
    except (IndexError, ValueError):
        return ""
    parts = [(minutes // 1440, "day"), (minutes // 60 % 24, "hour"), (minutes % 60, "min")]
    return ", ".join("%d %s%s" % (n, unit, "s" * (n != 1)) for n, unit in parts if n) or "0 mins"


def _packages():
    try:
        return "%d (dpkg)" % sum(name.endswith(".list") for name in os.listdir("/var/lib/dpkg/info"))
    except OSError:
        return ""


def _shell():
    return os.environ.get("LKR_SHELL") or _process(os.getppid())[0]


def _terminal():
    name, pid = os.environ.get("TERM_PROGRAM", ""), os.getppid()
    while not name and pid > 1:
        comm, pid = _process(pid)
        if comm and not comm.startswith(SHELLS):
            name = comm
    return name or os.environ.get("TERM", "")


def _cpu():
    info = _read("/proc/cpuinfo")
    name = _field(info, "model name", ":")
    return "%s (%d)" % (name, info.count("\nprocessor") + info.startswith("processor")) if name else ""


def _memory():
    info = _read("/proc/meminfo")
    try:
        total, free = (int(_field(info, key, ":").split()[0]) // 1024 for key in ("MemTotal", "MemAvailable"))
    except (IndexError, ValueError):
        return ""
    return "%d MiB / %d MiB" % (total - free, total)


def _disk():
    try:
        d = os.statvfs("/")
    except OSError:
        return ""
    used, free = (d.f_blocks - d.f_bfree) * d.f_frsize, d.f_bavail * d.f_frsize
    gib = lambda n: "%.0fG" % (n / 2 ** 30)
    return "%s / %s (%d%%)" % (gib(used), gib(d.f_blocks * d.f_frsize), round(100 * used / ((used + free) or 1)))


def _ip():
    import socket

    try:  # which address would reach the network; nothing is sent
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""


def _vpn():
    import fcntl
    import socket
    import struct

    found = []
    try:
        names = sorted(n for n in os.listdir("/sys/class/net") if n.startswith(("tun", "wg", "tailscale")))
    except OSError:
        return ""
    for name in names:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))  # SIOCGIFADDR
            found.append("%s (%s)" % (socket.inet_ntoa(packed[20:24]), name))
        except OSError:
            pass
    return ", ".join(found)


VALUES = {"os": _os, "kernel": lambda: os.uname().release, "uptime": _uptime, "packages": _packages, "shell": _shell,
          "terminal": _terminal, "cpu": _cpu, "memory": _memory, "disk": _disk, "ip": _ip, "vpn": _vpn}


def facts(cfg):
    """The right-hand block: a list of (width on screen, text with colours)."""
    p, a = fg(cfg["primary"]), fg(cfg["accent"])
    wanted = [key for key in FACTS if key in cfg["facts"]]
    pad = max([len(FACTS[key][0]) for key in wanted] + [0]) + 2
    out = []
    for key in wanted:
        if key == "title":
            user = os.environ.get("USER") or os.environ.get("LOGNAME") or str(os.getuid())
            host = os.uname().nodename
            out.append((len(user) + 1 + len(host), "\x1b[1m%s%s%s@%s%s%s" % (p, user, a, p, host, RESET)))
            out.append((len(user) + 1 + len(host), a + "─" * (len(user) + 1 + len(host)) + RESET))
        elif key == "colours":
            blocks = "".join("\x1b[4%dm   " % n for n in range(8)) + fg(cfg["accent"], 48) + "   " + fg(cfg["primary"], 48) + "   "
            out += [(0, ""), (30, blocks + RESET)]
        else:
            value = VALUES[key]()
            if value:
                out.append((pad + len(value), "%s%s%s%s%s" % (a, FACTS[key][0].ljust(pad), p, value, RESET)))
    return out


def lines(cfg, columns=10 ** 6):
    """The whole banner as lines. Art and facts side by side when the terminal is wide enough,
    else stacked, else the facts alone."""
    art, width, info = cfg["art"], cfg["width"], facts(cfg)
    if width + 1 > columns:
        art = []
    if art and info and 1 + width + 3 + max(n for n, _ in info) <= columns:
        top = max(0, (len(art) - len(info)) // 2)  # centre the facts against the art
        rows = []
        for i in range(max(len(art), top + len(info))):
            left = art[i] if i < len(art) else " " * width
            right = info[i - top][1] if 0 <= i - top < len(info) else ""
            rows.append((" %s   %s" % (left, right)).rstrip())
        return rows
    return [" " + row for row in art] + [""] * bool(art and info) + [" " + text for _, text in info]


def main():
    import json

    try:
        with open(os.path.join(os.path.dirname(os.path.realpath(__file__)), "banner.json")) as f:
            cfg = json.load(f)
        try:
            columns = os.get_terminal_size(sys.stdout.fileno()).columns
        except OSError:
            columns = 80
        sys.stdout.write("\n" + "\n".join(lines(cfg, columns)) + "\n\n")
    except Exception:  # a broken banner must never get in the way of opening a terminal
        pass


if __name__ == "__main__":
    main()
