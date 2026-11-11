# lazy-kali-ricer

Rice Hyprland on Kali from a GUI. Pick your apps, a theme, a wallpaper and clipboard
options, press **Apply**. No config files to edit.

- **Finds your programs.** Terminal, browser, file manager, music player: pick from what is installed.
- **Themes.** Six ready ones, or build your own: outline colour, thickness and animated effects.
- **Your wallpaper.** Any image, or a plain colour. The lock screen matches.
- **Adapts to the machine.** Laptop or desktop, AMD or Intel CPU, AMD, NVIDIA or Intel graphics.
- **Safe to try.** Existing configs are backed up before anything is written.
- **Shortcut list.** Opens at login until you tell it not to; Super+K any time.

It sets up Hyprland (Lua config), Waybar, wofi, mako, hyprlock and, on laptops, hypridle.

## Install

```bash
git clone <repo-url> lazy-kali-ricer
cd lazy-kali-ricer
./install.sh
```

`install.sh` installs the packages with apt, adds the `lazy-kali-ricer` command to
`~/.local/bin`, and opens the GUI. Nothing in `~/.config` changes until you press Apply.
Any config file that was already there is moved to `<name>.bak-<timestamp>` first.

Needs a Hyprland build with Lua config support (Kali's `hyprland` package has it).

## The GUI

| Tab | What you choose |
|-----|-----------------|
| **Apps** | Terminal, browser, file manager, music, calculator, task manager. The lists show what is installed on your system (Flatpaks included). You can type a custom command or pick None. Also the screenshot tool (Flameshot or grim + slurp), keyboard layout, and whether the shortcut list opens at login. |
| **Theme** | A ready theme, or Custom: primary, accent and unfocused outline colours, outline thickness, outline effect and speed, corner rounding, gaps, glow. A preview shows the result. |
| **Wallpaper** | A solid colour or any image of yours. Also used on the lock screen. |
| **Clipboard** | Clipboard history on/off, how many entries, images or text only, and the privacy options below. |
| **Debug** | Logging switch and a button that saves a report. |

### Themes

`hAckDHD's theme` (neon green + purple, chasing light), `Kali Dragon`, `Synthwave`,
`Blood Moon`, `RGB Rig`, `Ghost`. Themes are colours and style only; they ship no images.
Add your own in `THEMES` in `lkr/core.py`.

### Outline effects

| Effect | What the focused window's outline does |
|--------|-----------------------------------------|
| Static | One colour |
| Gradient | Primary fades into accent |
| Spinning gradient | The same gradient, rotating |
| Chasing light | One band of light runs around the edge |
| Dual chase | A primary and an accent band chase each other |
| RGB rainbow | The full spectrum, rotating |

### Clipboard privacy

History stays on your computer, in `~/.cache/cliphist`. Nothing is sent anywhere.
By default everything you copy is kept, passwords included, up to 200 entries.

Optional, in the Clipboard tab:

- never store copies that a password manager marks as sensitive;
- wipe the history at every login;
- "Wipe history now" clears it at once.

## Hardware

Detected when Hyprland starts, so one install works if you move the disk or change the GPU.
The GUI shows what it found at the bottom of the window.

| Hardware | What happens |
|----------|--------------|
| **Laptop** (battery present) | Battery in the bar, brightness keys, touchpad tap and natural scroll, hypridle: dim, lock, screen off, suspend. |
| **Desktop** | No idle manager, no battery item. Lock with Super+L. |
| **AMD CPU** | Bar temperature from `k10temp` / `zenpower`. |
| **Intel CPU** | Bar temperature from `coretemp`. Other CPUs fall back to the kernel thermal zones. |
| **AMD graphics** | Video acceleration through `radeonsi`. Nothing else needed. |
| **Intel graphics** | Works as is. |
| **NVIDIA, proprietary driver** | Sets the NVIDIA Wayland environment, software cursor and explicit sync. Install the driver yourself first. |
| **NVIDIA, nouveau driver** | Treated like AMD/Intel; no NVIDIA settings forced. |
| **NVIDIA + Intel/AMD** (hybrid laptop) | Desktop runs on the integrated GPU; nothing forced. |

## Command line

```
lazy-kali-ricer                 open the GUI
lazy-kali-ricer apply           write the configs from the saved settings
lazy-kali-ricer debug on        mirror the Hyprland log to ~/.cache/lazy-kali-ricer/hyprland.log
lazy-kali-ricer debug off       stop mirroring
lazy-kali-ricer debug report    save ~/.cache/lazy-kali-ricer/debug-report.txt
```

## Something broke?

1. `lazy-kali-ricer debug on`
2. Reproduce the problem (log out and back in if Hyprland itself fails).
3. `lazy-kali-ricer debug report`
4. Attach the report to an issue, or paste it to an AI assistant.

The report holds versions, your settings, Hyprland config errors and the last 300 log
lines. Your home path, user name and host name are replaced. Read it before you share it.

## Keys

**Super+K** lists every keybinding. The list also opens at each login; choose
"Don't show this list on next login" at its top, or untick the box in the Apps tab. The main ones:

| Keys | Action |
|------|--------|
| Super+Space | App launcher |
| Super+Enter | Terminal |
| Super+E / Super+F / Super+S | Browser / file manager / music |
| Super+V | Clipboard history, images shown as thumbnails |
| Print, Super+Shift+S | Screenshot a region to the clipboard |
| Alt+Tab | Window picker with app icons |
| Super+M | Minimize; the bar counts minimized windows, click the count to restore one |
| Super+O | Float / un-float the window |
| Super+T | Tighten / loosen gaps |
| Super+W | Close window |
| Super+L | Lock |

## Where things live

| Path | What |
|------|------|
| `lkr/core.py` | Themes, app detection, config rendering, debug report |
| `lkr/gui.py` | The GTK window |
| `config/` | Config files, copied to `~/.config` on Apply with the theme colours filled in |
| `~/.config/lazy-kali-ricer/settings.json` | Your choices |
| `~/.config/hypr/settings.lua` | The same choices, as read by `hyprland.lua` |

Monitors are placed automatically. For a fixed layout add `hl.monitor` lines to
`~/.config/hypr/hyprland.lua`; note that Apply rewrites that file.

## Licence

MIT. See [LICENSE](LICENSE).
