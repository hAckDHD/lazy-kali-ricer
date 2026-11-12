#!/bin/sh
# Flameshot region selector on the monitor the mouse is on; result goes to the clipboard.
# Flameshot numbers screens in the order Hyprland lists them, which is not the monitor id
# (ids get reused when a screen is unplugged and plugged back in). So: find the monitor under
# the cursor and pass its position in the list. Falls back to the focused monitor, then 0.
pgrep -x flameshot >/dev/null || { flameshot >/dev/null 2>&1 & sleep 0.6; }
n=$(hyprctl monitors -j | jq --argjson c "$(hyprctl cursorpos -j 2>/dev/null || echo null)" '
    def under: (if .transform % 2 == 1 then [.height, .width] else [.width, .height] end) as [$w, $h]
        | $c != null and $c.x >= .x and $c.x < .x + $w / .scale and $c.y >= .y and $c.y < .y + $h / .scale;
    (map(under) | index(true)) // (map(.focused) | index(true)) // 0')
exec flameshot screen -n "${n:-0}" -e -c
