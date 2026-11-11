#!/bin/sh
# Flameshot region selector on the focused monitor; result goes to the clipboard.
# Flameshot's screen numbers match Hyprland's monitor ids.
pgrep -x flameshot >/dev/null || { flameshot >/dev/null 2>&1 & sleep 0.6; }
n=$(hyprctl monitors -j | jq -r '.[] | select(.focused) | .id')
exec flameshot screen -n "${n:-0}" -e -c
