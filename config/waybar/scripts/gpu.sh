#!/usr/bin/env bash
# GPU temperature and VRAM usage for Waybar — portable across NVIDIA / AMD / Intel.
#   gpu.sh temp  -> whole degrees C as plain text
#   gpu.sh vram  -> JSON {text,tooltip}; text is used-VRAM percent
# Sources, in order: nvidia-smi -> amdgpu sysfs -> lm-sensors (temp only).
# When no GPU can be queried the output is empty, so Waybar hides the pill —
# machines with only shared-memory integrated graphics show nothing instead of "--".
set -euo pipefail

mode="${1:-temp}"

emit_temp() {   # prints whole degrees, or nothing when the value is not a number
    case "${1:-}" in ''|*[!0-9.]*) return 0 ;; esac
    printf '%.0f\n' "$1"
}

emit_vram() {   # $1 used bytes, $2 total bytes -> JSON for a return-type:json module
    case "${1:-}" in ''|*[!0-9]*) printf '{"text":""}\n'; return ;; esac
    case "${2:-}" in ''|*[!0-9]*) printf '{"text":""}\n'; return ;; esac
    [ "$2" -gt 0 ] || { printf '{"text":""}\n'; return; }
    awk -v u="$1" -v t="$2" 'BEGIN{
        printf "{\"text\":\"%.0f\",\"tooltip\":\"VRAM %.1f / %.1f GiB\"}\n",
               u*100/t, u/1073741824, t/1073741824 }'
}

# ---------- NVIDIA ----------
if command -v nvidia-smi >/dev/null 2>&1; then
    line=$(nvidia-smi --query-gpu=temperature.gpu,memory.used,memory.total \
           --format=csv,noheader,nounits 2>/dev/null | head -n1 || true)
    if [ -n "$line" ]; then
        IFS=', ' read -r ntemp nused ntotal <<<"$line"
        nused=$(printf '%s' "${nused:-}"  | tr -dc '0-9')
        ntotal=$(printf '%s' "${ntotal:-}" | tr -dc '0-9')
        case "$mode" in
            temp) emit_temp "$ntemp"; exit 0 ;;
            vram) emit_vram "$(( ${nused:-0} * 1048576 ))" "$(( ${ntotal:-0} * 1048576 ))"; exit 0 ;;  # MiB -> bytes
        esac
    fi
fi

# ---------- AMD (amdgpu sysfs) ----------
for dev in /sys/class/drm/card*/device; do
    [ -r "$dev/mem_info_vram_total" ] || continue       # amdgpu-only files; presence confirms the driver
    case "$mode" in
        vram)
            emit_vram "$(cat "$dev/mem_info_vram_used" 2>/dev/null || true)" \
                      "$(cat "$dev/mem_info_vram_total" 2>/dev/null || true)"
            exit 0 ;;
        temp)
            for h in "$dev"/hwmon/hwmon*/; do
                [ -r "$h/temp1_input" ] || continue      # temp1 is the GPU edge sensor
                emit_temp "$(( $(cat "$h/temp1_input") / 1000 ))"
                exit 0
            done ;;
    esac
done

# ---------- lm-sensors fallback (GPU temp only; covers Intel i915/xe and others) ----------
if [ "$mode" = temp ] && command -v sensors >/dev/null 2>&1; then
    t=$(sensors -u 2>/dev/null | awk '
        /^amdgpu-|^nouveau-|^i915-|^xe-|^intel-/   { chip=1 }
        /^[a-z].*-/ && !/^amdgpu-|^nouveau-|^i915-|^xe-|^intel-/ { chip=0 }
        chip && /_input:/ { if (prev ~ /edge|temp1|Package|GPU/) { print $2; exit } }
        { prev=$1 }')
    emit_temp "$t"
    exit 0
fi

# ---------- nothing to report ----------
[ "$mode" = vram ] && printf '{"text":""}\n'
exit 0
