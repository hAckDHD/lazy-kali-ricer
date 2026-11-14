"""The terminal banner: which art, which facts, which colours, and the line in ~/.zshrc and ~/.bashrc that shows it.

Art is text. Built-in art lives in art/, yours in ~/.config/lazy-kali-ricer/art/. Both may use the neofetch
markers ${c1} (primary colour) and ${c2} (accent colour), or carry their own ANSI colours. A picture is turned
into coloured half-block characters, so it shows in every terminal that has 24-bit colour.
"""
import json
import os
import re
import shutil
import unicodedata
from html import escape

from . import core, fetch

BUILTIN = os.path.join(core.REPO, "art")
OWN_ART = os.path.join(core.OWN, "art")
DIR = os.path.join(core.OWN, "terminal")  # what a new terminal reads: init.sh, fetch, banner.json
PICTURE, NO_ART, OWN_PREFIX = "picture", "", "own:"
MAX_LINES, MAX_ART_BYTES = 60, 200_000
BEGIN, END = "# >>> lazy-kali-ricer terminal >>>", "# <<< lazy-kali-ricer terminal <<<"
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESCAPES = re.compile(r"\x1b(?!\[[0-9;]*m)(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)?|.)?")
BASIC = ["#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
         "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff"]


def defaults():
    theme = core.THEMES[core.DEFAULT_THEME]
    return dict(term_banner=False, term_art="pentagram", term_picture="", term_picture_width=36,
                term_facts=["title", "os", "kernel", "uptime", "shell", "terminal", "memory", "disk", "colours"],
                term_theme_colours=True, term_primary=theme["primary"], term_accent=theme["accent"],
                term_colours=False, term_background="#000000")


def colours(s):
    """(primary, accent) of the banner."""
    return (s["primary"], s["accent"]) if s["term_theme_colours"] else (s["term_primary"], s["term_accent"])


# ---------- the art list ----------
def arts():
    """[(id, name)]: built-in art (pentagram first), then yours."""
    def names(folder):
        try:
            return sorted(n[:-4] for n in os.listdir(folder) if n.endswith(".txt"))
        except OSError:
            return []

    built = sorted(names(BUILTIN), key=lambda n: (n != "pentagram", n))
    return [(n, n.replace("-", " ").capitalize()) for n in built] + [(OWN_PREFIX + n, "%s (yours)" % n) for n in names(OWN_ART)]


def art_path(art_id):
    if art_id.startswith(OWN_PREFIX):
        return os.path.join(OWN_ART, os.path.basename(art_id[len(OWN_PREFIX):]) + ".txt")
    return os.path.join(BUILTIN, os.path.basename(art_id) + ".txt")


def add_art(name, text):
    """Keep text as art of your own. Returns (id, "") or ("", why not)."""
    name = re.sub(r"[^\w -]", "", name).strip()[:40]
    if not name:
        return "", "Give it a name: letters, digits, spaces."
    if not text.strip():
        return "", "There is no art in that."
    if len(text.encode()) > MAX_ART_BYTES:
        return "", "That is too big for a banner (over %d KB)." % (MAX_ART_BYTES // 1000)
    os.makedirs(OWN_ART, exist_ok=True)
    with open(os.path.join(OWN_ART, name + ".txt"), "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    return OWN_PREFIX + name, ""


def add_art_file(path):
    try:
        if os.path.getsize(path) > MAX_ART_BYTES:
            return "", "That file is too big for a banner (over %d KB)." % (MAX_ART_BYTES // 1000)
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return "", "Could not read it: %s" % e
    return add_art(os.path.splitext(os.path.basename(path))[0], text)


def delete_art(art_id):
    if art_id.startswith(OWN_PREFIX) and os.path.isfile(art_path(art_id)):
        os.remove(art_path(art_id))


# ---------- art to coloured lines ----------
def _width(text):
    """Columns a line takes on screen."""
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in SGR.sub("", text))


def _pad(lines):
    """Same width for every line, colours switched off at the end. Returns (lines, width)."""
    width = max([_width(line) for line in lines] + [0])
    return [line + fetch.RESET + " " * (width - _width(line)) for line in lines], width


def render_text(text, primary, accent):
    """Text art to lines. ${c1} is the primary colour, ${c2} the accent, ${c3} white; text without any
    colour is all primary. Colour codes already in the text are kept; every other escape sequence is
    removed, so art from the internet cannot do anything to your terminal but draw."""
    text = OTHER_ESCAPES.sub("", text).expandtabs()
    text = "".join(c for c in text if c in "\n\x1b" or unicodedata.category(c) != "Cc")
    palette = [fetch.fg(primary), fetch.fg(accent), fetch.fg("#ffffff")]
    text = re.sub(r"\$\{c(\d)\}", lambda m: palette[(int(m.group(1)) - 1) % 3], text)
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not SGR.sub("", lines[0]).strip():
        lines.pop(0)
    while lines and not SGR.sub("", lines[-1]).strip():
        lines.pop()
    out, colour = [], palette[0]
    for line in lines[:MAX_LINES]:
        out.append(colour + line)
        codes = SGR.findall(line)
        if codes:  # a colour runs on into the next line, as it would on screen
            colour = palette[0] if codes[-1] in ("", "0") else "\x1b[%sm" % codes[-1]
    return _pad(out)


def render_picture(path, columns):
    """A picture as half-block characters: each one is two pixels, the top in the text colour and the
    bottom in the background colour. Transparent parts stay empty."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pix = GdkPixbuf.Pixbuf.new_from_file(path)
    pix = pix.apply_embedded_orientation() or pix
    rows = max(1, round(columns * pix.get_height() / pix.get_width() / 2))  # a character is twice as tall as wide
    if rows > MAX_LINES:
        rows, columns = MAX_LINES, max(1, round(columns * MAX_LINES / rows))
    pix = pix.scale_simple(columns, rows * 2, GdkPixbuf.InterpType.HYPER)
    data, step, stride, alpha = pix.get_pixels(), pix.get_n_channels(), pix.get_rowstride(), pix.get_has_alpha()

    def pixel(x, y):
        at = y * stride + x * step
        if alpha and data[at + 3] < 128:
            return None
        return "%d;%d;%d" % (data[at], data[at + 1], data[at + 2])

    lines = []
    for row in range(rows):
        line, last = "", (None, None)
        for x in range(columns):
            top, bottom = pixel(x, row * 2), pixel(x, row * 2 + 1)
            char, ink, paper = ("▀", top, bottom) if top else ("▄", bottom, None) if bottom else (" ", None, None)
            if (ink, paper) != last:
                line += "\x1b[0%s%sm" % (";38;2;" + ink if ink else "", ";48;2;" + paper if paper else "")
                last = (ink, paper)
            line += char
        lines.append(line)
    return _pad(lines)


def art_lines(s):
    """(lines, width, problem) for the art picked in the settings. problem is "" or one sentence."""
    art = s["term_art"]
    if art == NO_ART:
        return [], 0, ""
    if art == PICTURE:
        if not s["term_picture"]:
            return [], 0, "No picture chosen yet."
        try:
            return render_picture(s["term_picture"], int(s["term_picture_width"])) + ("",)
        except Exception as e:  # GLib.Error for an unreadable file, ImportError without GTK
            return [], 0, "Could not use the picture %s: %s" % (s["term_picture"].replace(core.HOME, "~"), e)
    try:
        with open(art_path(art), encoding="utf-8", errors="replace") as f:
            return render_text(f.read(), *colours(s)) + ("",)
    except OSError:
        return [], 0, "The art \"%s\" is gone." % art


def banner(s):
    """What fetch needs, and the problem with the art if there was one."""
    lines, width, problem = art_lines(s)
    primary, accent = colours(s)
    return dict(art=lines, width=width, facts=s["term_facts"], primary=primary, accent=accent), problem


# ---------- preview ----------
def _cube(n):
    """Colour n of the 256-colour table."""
    if n < 16:
        return BASIC[n]
    if n > 231:
        return "#%02x%02x%02x" % ((8 + (n - 232) * 10,) * 3)
    n -= 16
    return "#%02x%02x%02x" % tuple(v and 55 + v * 40 for v in (n // 36, n // 6 % 6, n % 6))


def to_markup(lines):
    """Lines with ANSI colours as Pango markup, for the preview in the GUI."""
    out = []
    for line in lines:
        ink = paper = None
        bold, at, text = False, 0, ""
        for found in list(SGR.finditer(line)) + [None]:
            chunk = line[at:found.start() if found else len(line)]
            if chunk:
                attrs = ((' foreground="%s"' % ink if ink else "") + (' background="%s"' % paper if paper else "")
                         + (' weight="bold"' if bold else ""))
                text += "<span%s>%s</span>" % (attrs, escape(chunk)) if attrs else escape(chunk)
            if not found:
                break
            at = found.end()
            codes = [int(c) if c else 0 for c in found.group(1).split(";")]
            while codes:
                c = codes.pop(0)
                if c in (38, 48) and codes:
                    kind = codes.pop(0)
                    colour = None
                    if kind == 2 and len(codes) >= 3:
                        colour = "#%02x%02x%02x" % tuple(min(255, v) for v in codes[:3])
                        del codes[:3]
                    elif kind == 5 and codes:
                        colour = _cube(min(255, codes.pop(0)))
                    ink, paper = (colour, paper) if c == 38 else (ink, colour)
                elif c == 0:
                    ink = paper = None
                    bold = False
                elif c == 1:
                    bold = True
                elif 30 <= c <= 37 or 90 <= c <= 97:
                    ink = BASIC[c - 30 if c < 90 else c - 82]
                elif 40 <= c <= 47 or 100 <= c <= 107:
                    paper = BASIC[c - 40 if c < 100 else c - 92]
                elif c in (39, 49):
                    ink, paper = (None, paper) if c == 39 else (ink, None)
        out.append(text)
    return "\n".join(out)


def preview(s):
    """(markup of the banner as a new terminal would show it, problem with the art or "")."""
    cfg, problem = banner(s)
    shown = {"LKR_SHELL": os.path.basename(os.environ.get("SHELL", "")) or "zsh",  # not this program and its parent
             "TERM_PROGRAM": os.path.basename((s["apps"].get("terminal") or "terminal").split()[0])}
    before = {key: os.environ.get(key) for key in shown}
    os.environ.update(shown)
    try:
        return to_markup(fetch.lines(cfg)), problem
    finally:
        for key, value in before.items():
            os.environ.pop(key) if value is None else os.environ.__setitem__(key, value)


# ---------- the shell side ----------
def rc_files():
    """The shell start files to hook: ~/.zshrc and ~/.bashrc when they exist, else the one for your shell."""
    zshrc = os.path.join(os.environ.get("ZDOTDIR") or core.HOME, ".zshrc")
    found = [p for p in (zshrc, os.path.join(core.HOME, ".bashrc")) if os.path.isfile(p)]
    return found or [zshrc if os.path.basename(os.environ.get("SHELL", "")) == "zsh" else os.path.join(core.HOME, ".bashrc")]


def hooked():
    """The shell start files that hold our lines."""
    files = []
    for path in rc_files():
        try:
            with open(path) as f:
                if BEGIN in f.read():
                    files.append(path)
        except OSError:
            pass
    return files


def hook(on, files=None):
    """Add or remove our three lines at the end of the shell start files. Nothing else in them changes.
    Returns the files that were changed."""
    init = os.path.join(DIR, "init.sh").replace(core.HOME, "$HOME")
    block = '%s\ncase $- in *i*) [ -r "%s" ] && . "%s" ;; esac\n%s\n' % (BEGIN, init, init, END)
    changed = []
    for path in files or rc_files():
        try:
            with open(path) as f:
                old = f.read()
        except OSError:
            old = ""
        text = re.sub(r"\n?%s\n.*?%s\n?" % (re.escape(BEGIN), re.escape(END)), "\n", old, flags=re.S)
        if on:
            text = text.rstrip("\n") + "\n\n" + block if text.strip() else block
        elif BEGIN in old:
            text = text.rstrip("\n") + "\n"
        else:
            continue
        if text != old:
            with open(path, "w") as f:  # in place: the file may be a link into a dotfiles folder
                f.write(text)
            changed.append(path)
    return changed


def init_sh(s):
    """What a new terminal runs: set its colours, show the banner."""
    lines = ["# Written by lazy-kali-ricer (Terminal tab). Read by bash and zsh when a terminal opens.",
             "[ -t 1 ] || return 0"]
    if s["term_colours"]:  # OSC 10, 11, 12: text, background, cursor
        lines.append("printf '\\033]10;%s\\007\\033]11;%s\\007\\033]12;%s\\007'" % (colours(s)[0], s["term_background"], colours(s)[1]))
    if s["term_banner"]:
        lines.append('LKR_SHELL="${ZSH_VERSION:+zsh $ZSH_VERSION}${BASH_VERSION:+bash ${BASH_VERSION%%%%[(]*}}" "%s"'
                     % os.path.join(DIR, "fetch").replace(core.HOME, "$HOME"))
    return "\n".join(lines) + "\n"


def apply(s):
    """Write the banner files and hook the shell, or unhook it when banner and colours are both off.
    Returns log lines."""
    short = lambda p: p.replace(core.HOME, "~")
    if not (s["term_banner"] or s["term_colours"]):
        shutil.rmtree(DIR, ignore_errors=True)
        return ["Terminal: removed the lines from %s" % short(p) for p in hook(False)]
    log = []
    cfg, problem = banner(s)
    os.makedirs(DIR, exist_ok=True)
    target = os.path.join(DIR, "banner.json")
    if problem and os.path.isfile(target):  # e.g. the picture was moved: keep the banner as it was
        log.append("Terminal: %s The banner stays as it was." % problem)
    else:
        if problem:
            log.append("Terminal: %s The banner shows no art." % problem)
        with open(target, "w") as f:
            json.dump(cfg, f)
    shutil.copyfile(fetch.__file__, os.path.join(DIR, "fetch"))
    os.chmod(os.path.join(DIR, "fetch"), 0o755)
    with open(os.path.join(DIR, "init.sh"), "w") as f:
        f.write(init_sh(s))
    return log + ["Terminal: added its lines to %s. Open a new terminal to see it." % short(p) for p in hook(True)]
