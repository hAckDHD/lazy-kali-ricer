#!/bin/sh
# Rotate the wallpaper through the images in a folder (alphabetical order, wraps around).
#   wallpaper-rotate.sh DIR            show the next image once (bound to Super+Shift+W)
#   wallpaper-rotate.sh DIR SECONDS    show the next image now, then again every SECONDS
dir=$1
every=${2:-0}
state="${XDG_CACHE_HOME:-$HOME/.cache}/lazy-kali-ricer/wallpaper"   # path of the image on screen
mkdir -p "$(dirname "$state")"

next() {
    current=$(cat "$state" 2>/dev/null)
    pick=$(find -L "$dir" -maxdepth 1 -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) | sort |
        awk -v cur="$current" 'NR == 1 { first = $0 } found { print; done = 1; exit } $0 == cur { found = 1 }
                               END { if (!done) print first }')
    [ -n "$pick" ] || exit 1
    printf '%s\n' "$pick" > "$state"
    old=$(pgrep -x swaybg)
    swaybg -i "$pick" -m fill >/dev/null 2>&1 &
    sleep 0.6   # new image is up before the old one goes: no flash
    [ -n "$old" ] && kill $old
}

next
[ "$every" -gt 0 ] && while sleep "$every"; do next; done
exit 0
