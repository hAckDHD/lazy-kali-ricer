#!/bin/sh
# Popup menu (wofi) to choose the default audio OUTPUT (sink) or INPUT (source).
#   audio-menu.sh sink    -> pick output   (bind to the volume module)
#   audio-menu.sh source  -> pick input    (bind to the mic module)
kind="${1:-sink}"
case "$kind" in
    source) hdr="Sources:"; nexthdr="Filters:"; prompt="Input"; icon="";;
    *)      hdr="Sinks:";   nexthdr="Sources:"; prompt="Output"; icon="";;
esac

# Pull the device lines under the right header from the Audio section of wpctl status.
entries=$(wpctl status | awk -v s="$hdr" -v e="$nexthdr" '
    index($0, s) { g=1; next }
    index($0, e) { g=0 }
    g && /[0-9]+\./ { print }')

[ -z "$entries" ] && { notify-send "Audio" "No $prompt devices found"; exit 0; }

# "<id>: <name>", marking the current default with a dot.
menu=$(printf '%s\n' "$entries" | sed -E 's/^([^0-9*]*)(\*)?[[:space:]]*([0-9]+)\.[[:space:]]*/\3: /; s/[[:space:]]*\[vol.*$//; s/[[:space:]]+$//')

choice=$(printf '%s\n' "$menu" | wofi --dmenu --prompt "$prompt" --location=top_right --xoffset=-24 --yoffset=6 --width=560 --height=280)
[ -z "$choice" ] && exit 0
id=${choice%%:*}
case "$id" in
    *[!0-9]*|'') exit 0 ;;
esac
wpctl set-default "$id" && notify-send -u low "Audio $prompt" "$(printf '%s' "$choice" | cut -d: -f2- | sed 's/^ //')"
