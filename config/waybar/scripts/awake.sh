#!/usr/bin/env bash
# Keep-awake ("caffeine") pill for Waybar.
#   awake.sh          -> JSON {text,tooltip,class} and holds/releases the inhibitor to match
#   awake.sh toggle   -> manual keep-awake on/off (left click; resets at logout)
#   awake.sh auto     -> turn "stay awake while plugged in" on/off (right click; remembered)
#
# Awake = manual ON, or (auto ON and a charger is connected). A charger counts even when the
# battery is full and no longer charging: any Mains/USB power supply reporting online=1.
# While awake, a `systemd-inhibit --what=idle:sleep` lock is held; hypridle honours it, so no
# dimming, locking, screen-off or suspend. Lid-close behaviour is left to logind.
set -uo pipefail

run_dir="${XDG_RUNTIME_DIR:-/tmp}"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/lazy-kali-ricer"
manual_flag="$run_dir/lkr-awake-manual"
auto_off_flag="$state_dir/awake-on-ac-off"
who="lkr-awake"

refresh() { pkill -RTMIN+9 -x waybar 2>/dev/null || true; }

case "${1:-}" in
    toggle)
        if [ -e "$manual_flag" ]; then rm -f "$manual_flag"; else : > "$manual_flag"; fi
        "$0" >/dev/null; refresh; exit 0 ;;   # apply the inhibitor change right away
    auto)
        mkdir -p "$state_dir"
        if [ -e "$auto_off_flag" ]; then rm -f "$auto_off_flag"; else : > "$auto_off_flag"; fi
        "$0" >/dev/null; refresh; exit 0 ;;
esac

has_battery=0; on_ac=0
for ps in /sys/class/power_supply/*; do
    [ -r "$ps/type" ] || continue
    case "$(cat "$ps/type")" in
        Battery) has_battery=1 ;;
        Mains|USB|USB_C|USB_PD) [ "$(cat "$ps/online" 2>/dev/null)" = 1 ] && on_ac=1 ;;
    esac
done

manual=0; [ -e "$manual_flag" ] && manual=1
auto=1;   [ -e "$auto_off_flag" ] && auto=0
ac_awake=0; [ "$has_battery" = 1 ] && [ "$auto" = 1 ] && [ "$on_ac" = 1 ] && ac_awake=1

want=0; { [ "$manual" = 1 ] || [ "$ac_awake" = 1 ]; } && want=1

pids=$(pgrep -u "$(id -u)" -f "systemd-inhibit --who=$who " || true)
if [ "$want" = 1 ] && [ -z "$pids" ]; then
    setsid -f systemd-inhibit --who="$who" --what=idle:sleep --mode=block \
        --why="Keep awake (Waybar)" sleep infinity </dev/null >/dev/null 2>&1
elif [ "$want" = 0 ] && [ -n "$pids" ]; then
    kill $pids 2>/dev/null
fi

auto_line="Stay awake on charger: $([ "$auto" = 1 ] && echo on || echo off) (right-click)"
[ "$has_battery" = 1 ] || auto_line="No battery: manual mode only"
power_line="Power: $([ "$on_ac" = 1 ] && echo "charger connected" || echo "on battery")"

if [ "$manual" = 1 ]; then
    text="<span color='#ffe14d'></span>  <span color='__LKR_PRIMARY__'>ON</span>"
    class=on; head="Keep awake: ON (click to turn off)"
elif [ "$ac_awake" = 1 ]; then
    text="<span color='__LKR_ACCENT__'></span>  <span color='__LKR_PRIMARY__'></span>"
    class=ac; head="Keep awake: automatic, charger connected (click to force on)"
else
    text="<span color='#6f8378'></span>"
    class=off; head="Keep awake: off — normal dim/lock/suspend (click to turn on)"
fi

printf '{"text":"%s","tooltip":"%s\\n%s\\n%s","class":"%s"}\n' \
    "$text" "$head" "$power_line" "$auto_line" "$class"
