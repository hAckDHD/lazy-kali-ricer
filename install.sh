#!/usr/bin/env bash
# lazy-kali-ricer: install the packages, add the `lazy-kali-ricer` command, open the GUI.
# Nothing in ~/.config changes until you press Apply in the GUI. Safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf '\033[1;35m::\033[0m %s\n' "$*"; }

PKGS="hyprland hyprlock waybar mako-notifier wofi swaybg cliphist wl-clipboard grim slurp jq flameshot git
      pavucontrol lm-sensors bluez network-manager libnotify-bin python3-gi python3-gi-cairo gir1.2-gtk-3.0
      xdg-desktop-portal-hyprland fonts-font-awesome fonts-hack"
# Laptop (battery present): idle manager and backlight keys.
ls -d /sys/class/power_supply/BAT* >/dev/null 2>&1 && PKGS="$PKGS hypridle brightnessctl"

if command -v apt >/dev/null; then
    say "Installing packages (sudo may ask for your password)…"
    before="$(dpkg-query -W -f='${Package}\n' | sort -u)"
    sudo apt update
    # shellcheck disable=SC2086
    sudo apt install -y $PKGS
    sudo apt install -y hyprpolkitagent || say "hyprpolkitagent not in your repos; skipped (only needed for admin prompts)."
    # Remember what this run added, so `lazy-kali-ricer uninstall` can offer to remove it.
    OWN="${XDG_CONFIG_HOME:-$HOME/.config}/lazy-kali-ricer"
    mkdir -p "$OWN"
    comm -13 <(echo "$before") <(dpkg-query -W -f='${Package}\n' | sort -u) >> "$OWN/installed-packages"
    sort -u -o "$OWN/installed-packages" "$OWN/installed-packages"
else
    say "apt not found. Install these yourself, then re-run: $PKGS"
fi

mkdir -p "$HOME/.local/bin"
ln -sf "$REPO/lazy-kali-ricer" "$HOME/.local/bin/lazy-kali-ricer"
say "Command added: ~/.local/bin/lazy-kali-ricer"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "Add ~/.local/bin to your PATH to run it by name." ;; esac

if [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
    say "Opening the GUI. Pick your apps and theme, then press Apply."
    exec "$REPO/lazy-kali-ricer"
fi
say "No desktop session here. Log in graphically and run: lazy-kali-ricer"
