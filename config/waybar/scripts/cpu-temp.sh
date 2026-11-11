#!/usr/bin/env bash
# Print CPU temperature in whole degrees C, portable across AMD / Intel / NVIDIA boxes.
# Order: lm-sensors common CPU labels -> hwmon -> thermal_zone. Falls back to "--".
set -euo pipefail

read_sensors() {
    command -v sensors >/dev/null 2>&1 || return 1
    sensors -u 2>/dev/null | awk '
        /^k10temp-|^coretemp-|^zenpower-/ { chip=1 }
        /^[a-z].*-/ && !/^k10temp-|^coretemp-|^zenpower-/ { chip=0 }
        chip && /_input:/ {
            label=prev
            if (label ~ /Tctl|Tdie|Package|Core 0|edge/) { print $2; exit }
        }
        { prev=$1 }'
}

t=$(read_sensors || true)
if [ -z "${t:-}" ]; then
    for z in /sys/class/thermal/thermal_zone*/; do
        [ -r "$z/type" ] || continue
        case "$(cat "$z/type")" in
            *x86_pkg*|*cpu*|*CPU*|*k10*|*coretemp*|*acpitz*)
                t=$(( $(cat "$z/temp") / 1000 )); break ;;
        esac
    done
fi

if [ -n "${t:-}" ]; then
    printf '%.0f\n' "$t"
else
    echo "--"
fi
