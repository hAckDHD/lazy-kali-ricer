-- lazy-kali-ricer: Hyprland Lua config. Wiki: https://wiki.hypr.land/configuring/
-- This file is the same for everyone. Your choices (apps, theme, wallpaper, clipboard)
-- live in settings.lua next to it, written by the `lazy-kali-ricer` GUI. Edit there, or
-- re-run the GUI; edits to this file are overwritten on the next Apply.

local cfg = os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config")
local S = dofile(cfg .. "/hypr/settings.lua")
local apps = S.apps
local winctl = "~/.config/hypr/scripts/winctl.py"
local menus  = "~/.config/hypr/scripts/menus.py"

------------------
---- MONITORS ----
------------------
-- Every monitor at its preferred mode, placed automatically. For a fixed layout add
-- lines like: hl.monitor({ output = "DP-1", mode = "1920x1080@60", position = "0x0", scale = 1 })
-- (names from `hyprctl monitors`).
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })

--------------------------------------
---- HARDWARE DETECTION (portable) ----
--------------------------------------
-- Runs when the config is parsed, so the same file works on any machine:
-- NVIDIA vs AMD/Intel, laptop vs desktop. No per-host editing needed.

local function read_cmd(cmd)
    local p = io.popen(cmd .. " 2>/dev/null")
    if not p then return "" end
    local out = p:read("*a") or ""
    p:close()
    return out
end

-- GPU. Only graphics devices are read, so an AMD CPU's chipset does not count as an AMD GPU.
--   NVIDIA alone, proprietary driver  -> NVIDIA env + cursor/sync fixes
--   NVIDIA + Intel/AMD (hybrid laptop) -> nothing forced: the desktop runs on the integrated GPU
--   NVIDIA on nouveau                  -> nothing forced: Mesa handles it like AMD/Intel
--   AMD                                -> VA-API through radeonsi
--   Intel                              -> works out of the box (libva picks iHD or i965 itself)
local gpus = read_cmd("lspci -nn | grep -iE 'vga|3d controller|display controller'"):lower()
local has_nvidia = gpus:find("nvidia") ~= nil
local has_amd    = gpus:find("amd/ati") ~= nil or gpus:find("advanced micro devices") ~= nil
local has_intel  = gpus:find("intel") ~= nil
local nvidia_driver = read_cmd("ls -d /sys/module/nvidia") ~= ""

if has_nvidia and nvidia_driver and not (has_amd or has_intel) then
    -- https://wiki.hypr.land/Nvidia/
    hl.env("LIBVA_DRIVER_NAME", "nvidia")
    hl.env("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
    hl.env("NVD_BACKEND", "direct")
    hl.env("GBM_BACKEND", "nvidia-drm")
    hl.env("ELECTRON_OZONE_PLATFORM_HINT", "auto")
    hl.config({ cursor = { no_hardware_cursors = true }, render = { explicit_sync = 2 } })
elseif has_amd and not has_nvidia and not has_intel then
    hl.env("LIBVA_DRIVER_NAME", "radeonsi")
end

-- Laptop: true when a battery is present.
local is_laptop = read_cmd("ls -d /sys/class/power_supply/BAT* 2>/dev/null"):find("BAT") ~= nil

-------------------
---- AUTOSTART ----
-------------------
hl.on("hyprland.start", function()
    hl.exec_cmd("waybar")
    hl.exec_cmd("mako")
    hl.exec_cmd("systemctl --user start hyprpolkitagent")
    -- Screenshot/screen-share portal. It must start after the session env is exported and
    -- before xdg-desktop-portal, or flameshot's capture hangs and returns an empty image.
    hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE && systemctl --user stop xdg-desktop-portal xdg-desktop-portal-hyprland; systemctl --user start xdg-desktop-portal-hyprland && systemctl --user start xdg-desktop-portal")
    if apps.screenshot == "flameshot" then hl.exec_cmd("flameshot") end   -- daemon, so the first screenshot is instant
    -- Laptops: idle manager (dims, then locks with hyprlock). Harmless to skip on desktops.
    if is_laptop then hl.exec_cmd("hypridle") end
    -- Wallpaper, clipboard watchers and (when enabled) the debug log mirror, from settings.lua.
    for _, cmd in ipairs(S.autostart) do hl.exec_cmd(cmd) end
    -- Shortcut list at login; the script shows nothing once that option is turned off.
    hl.exec_cmd(menus .. " keys login")
end)

---------------
---- INPUT ----
---------------
hl.config({
    input = {
        kb_layout    = S.kb_layout,
        follow_mouse = 1,
        -- Laptops: ignored on machines without a touchpad
        touchpad = {
            tap_to_click         = true,
            natural_scroll       = true,
            disable_while_typing = true,
        },
    },
})

-----------------------
---- LOOK & FEEL ------
-----------------------
-- All from the chosen theme. Border effects are a gradient on the focused window:
-- the moving effects (spin, chasing, rgb) rotate its angle.
local B = S.border
hl.config({
    general = {
        border_size = B.size,
        gaps_in     = S.gaps_in,
        gaps_out    = S.gaps_out,
        col = {
            inactive_border = B.inactive,
            active_border   = { colors = B.colors, angle = 45 },
        },
    },
    decoration = {
        rounding = S.rounding,
        glow = { enabled = S.glow.enabled, range = 7, render_power = 3, color = S.glow.color, color_inactive = "rgba(00000000)" },
    },
    animations = { enabled = true },
})

hl.curve("linear", { type = "bezier", points = { { 0, 0 }, { 1, 1 } } })
hl.animation({ leaf = "border", enabled = true, speed = 6, bezier = "linear" })
if B.loop then
    -- rotating angle: the gradient travels around the window edge
    hl.animation({ leaf = "borderangle", enabled = true, speed = B.speed, bezier = "linear", style = "loop" })
end

--------------------
---- FLAMESHOT -----
--------------------
hl.window_rule({
    name  = "flameshot",
    match = { class = "flameshot" },

    animation        = "fade",
    rounding         = 0,
    border_size      = 0,
    fullscreen_state = "0 0",
    move             = "0 0",   -- monitor corner; otherwise it is centered below the bar and the capture is offset
    float            = true,
    pin              = true,
})

-- Bar drop-down panels (scripts/panel.py: network, bluetooth): float under the bar's right end.
hl.window_rule({
    name  = "rice-panel",
    match = { class = "rice-panel" },

    float       = true,
    pin         = true,
    move        = "monitor_w-window_w-12 72",
    border_size = 0,
    no_shadow   = true,
    animation   = "fade",
})

-- Run a command (helper to keep the bind list short)
local function exec(cmd) return hl.dsp.exec_cmd(cmd) end

-- Every bind carries a description; Super+K lists them (scripts/menus.py keys).
local function bind(keys, action, desc, opts)
    opts = opts or {}
    opts.description = desc
    hl.bind(keys, action, opts)
end


--------------------
---- APPS ----------
--------------------
-- An app left as "None" in the GUI is an empty string here and gets no keys.
local function app(keys, cmd, desc)
    if cmd ~= "" then bind(keys, exec(cmd), desc) end
end
local menu = "wofi --show drun"
local run  = "wofi --show run"

app("SUPER + Return",        apps.terminal,   "Terminal")
app("CTRL + ALT + T",        apps.terminal,   "Terminal")
app("SUPER + E",             apps.browser,    "Browser")
app("SUPER + F",             apps.files,      "File manager")
app("CTRL + ALT + F",        apps.files,      "File manager")
app("SUPER + S",             apps.music,      "Music")
app("CTRL + SHIFT + Escape", apps.monitor,    "Task manager")
app("XF86Calculator",        apps.calculator, "Calculator")
app("XF86Explorer",          apps.files,      "File manager")
app("XF86HomePage",          apps.browser,    "Browser")
app("XF86WWW",               apps.browser,    "Browser")
bind("SUPER + Space",        exec(menu),      "App launcher")
bind("ALT + F1",             exec(menu),      "App launcher")
bind("CTRL + Escape",        exec(menu),      "App launcher")
bind("SUPER + R",            exec(run),       "Run a command")
bind("ALT + F2",             exec(run),       "Run a command")

--------------------
---- SYSTEM --------
--------------------
bind("SUPER + K",               exec(menus .. " keys"),      "Show this keybinding list")
bind("SUPER + L",               exec("hyprlock"),            "Lock screen")
bind("CTRL + ALT + L",          exec("hyprlock"),            "Lock screen")
if S.clipboard then
    bind("SUPER + V",           exec(menus .. " clipboard"), "Clipboard history (with image previews)")
    bind("SUPER + SHIFT + V",   exec(menus .. " clipboard"), "Clipboard history (with image previews)")
end
if S.wallpaper_next ~= "" then
    bind("SUPER + SHIFT + W",   exec(S.wallpaper_next),      "Next wallpaper")
end
bind("CTRL + ALT + Escape",     exec("hyprctl kill"),        "Kill a window by clicking it")
bind("CTRL + ALT + Delete",     hl.dsp.exit(),               "Log out of Hyprland")

---------------------
---- SCREENSHOTS ----
---------------------
local shots      = "~/Pictures"
local activeGeom = [[grim -g "$(hyprctl activewindow -j | jq -r '"\(.at[0]),\(.at[1]) \(.size[0])x\(.size[1])"')"]]
local stamp      = shots .. [[/Screenshot_$(date +%F_%H-%M-%S).png]]
local toClip     = [[grim -g "$(slurp)" - | wl-copy]]

-- Region to clipboard: Flameshot (editor) when chosen in the GUI, plain grim + slurp otherwise.
-- `flameshot gui` shows a monitor picker on Wayland, so the script passes the focused monitor.
local region = apps.screenshot == "flameshot" and "~/.config/hypr/scripts/flameshot-region.sh" or toClip
bind("Print",                exec(region),                                 "Screenshot region to clipboard")
bind("SUPER + SHIFT + S",    exec(region),                                 "Screenshot region to clipboard")
bind("SHIFT + Print",        exec([[grim -g "$(slurp -d)" ]] .. stamp),    "Screenshot region to a file in Pictures")
bind("CTRL + SHIFT + Print", exec(toClip),                                 "Screenshot region to clipboard (no editor)")
bind("CTRL + Print",         exec("grim - | wl-copy"),                     "Screenshot all screens to clipboard")
bind("ALT + Print",          exec(activeGeom .. " " .. stamp),             "Screenshot active window to a file")
bind("CTRL + ALT + Print",   exec(activeGeom .. " - | wl-copy"),           "Screenshot active window to clipboard")

----------------------------
---- MEDIA / EXTRA KEYS ----
----------------------------
-- Volume (PipeWire). Brightness needs brightnessctl (laptops only)
bind("XF86AudioRaiseVolume",  exec("wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+"), "Volume up",       { repeating = true, locked = true })
bind("XF86AudioLowerVolume",  exec("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"),      "Volume down",     { repeating = true, locked = true })
bind("XF86AudioMute",         exec("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"),     "Mute output",     { locked = true })
bind("XF86AudioMicMute",      exec("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"),   "Mute microphone", { locked = true })
bind("XF86MonBrightnessUp",   exec("brightnessctl set 5%+"),                          "Brightness up",   { repeating = true, locked = true })
bind("XF86MonBrightnessDown", exec("brightnessctl set 5%-"),                          "Brightness down", { repeating = true, locked = true })

--------------------
---- WINDOWS -------
--------------------
bind("ALT + F4",   hl.dsp.window.close(),                           "Close window")
bind("SUPER + W",  hl.dsp.window.close(),                           "Close window")
bind("ALT + F11",  hl.dsp.window.fullscreen({ mode = "fullscreen" }), "Fullscreen")
bind("SUPER + Up", hl.dsp.window.fullscreen({ mode = "maximized" }),  "Maximize")
bind("SUPER + O",         hl.dsp.window.float(), "Float / un-float window (free move and resize)")
bind("SUPER + SHIFT + F", hl.dsp.window.float(), "Float / un-float window")
bind("ALT + F6",   hl.dsp.window.pin(), "Pin floating window on all workspaces")
bind("ALT + F12",  hl.dsp.window.pin(), "Pin floating window on all workspaces")

-- Super+T: tighten / loosen the gaps between windows.
local tight = false
bind("SUPER + T", function()
    tight = not tight
    hl.config({ general = tight and { gaps_in = 1, gaps_out = 2 } or { gaps_in = S.gaps_in, gaps_out = S.gaps_out } })
end, "Tighten / loosen gaps between windows")

-- Alt+Tab: window picker with app icons. Lists every open window (minimized ones
-- too), previous window first, so Alt+Tab then Enter is a quick swap. Picking one
-- jumps to its workspace, or brings it back if it was minimized.
bind("ALT + Tab",         exec(winctl .. " switch"),                  "Window picker (all windows, with icons)")
bind("ALT + SHIFT + Tab", hl.dsp.focus({ last = true }),              "Jump to previous window")
bind("SUPER + Tab",       hl.dsp.window.cycle_next({ next = true }),  "Cycle windows on this workspace")
bind("SUPER + M",         exec(winctl .. " minimize"),                "Minimize window (counted in the bar)")
bind("SUPER + SHIFT + M", exec(winctl .. " switch-minimized"),        "Pick a minimized window to restore")

bind("SUPER + Left",        hl.dsp.window.move({ direction = "l" }), "Move window left")
bind("SUPER + Right",       hl.dsp.window.move({ direction = "r" }), "Move window right")
bind("CTRL + SUPER + Up",   hl.dsp.window.move({ direction = "u" }), "Move window up")
bind("CTRL + SUPER + Down", hl.dsp.window.move({ direction = "d" }), "Move window down")

bind("SUPER + SHIFT + Left",  hl.dsp.window.move({ monitor = "l" }), "Send window to the monitor on the left")
bind("SUPER + SHIFT + Right", hl.dsp.window.move({ monitor = "r" }), "Send window to the monitor on the right")
bind("SUPER + SHIFT + Up",    hl.dsp.window.move({ monitor = "u" }), "Send window to the monitor above")
bind("SUPER + SHIFT + Down",  hl.dsp.window.move({ monitor = "d" }), "Send window to the monitor below")

bind("SUPER + mouse:272", hl.dsp.window.drag(),   "Drag to move window",   { mouse = true })
bind("SUPER + mouse:273", hl.dsp.window.resize(), "Drag to resize window", { mouse = true })

-- Click anywhere outside an open menu or bar panel to dismiss it (wofi menus are layers,
-- the network/bluetooth panels are windows). Non-consuming, so the click still reaches
-- whatever is under the pointer.
hl.bind("mouse:272", function()
    local boxes = {}
    for _, l in ipairs(hl.get_layers({ namespace = "wofi" })) do
        boxes[#boxes + 1] = { x = l.x, y = l.y, w = l.w, h = l.h, pid = l.pid }
    end
    for _, w in ipairs(hl.get_windows({ class = "rice-panel" })) do
        boxes[#boxes + 1] = { x = w.at.x, y = w.at.y, w = w.size.x, h = w.size.y, pid = w.pid }
    end
    if #boxes == 0 then return end
    local c = hl.get_cursor_pos()
    if not c then return end
    for _, b in ipairs(boxes) do
        if c.x >= b.x and c.x <= b.x + b.w and c.y >= b.y and c.y <= b.y + b.h then return end
    end
    for _, b in ipairs(boxes) do hl.exec_cmd("kill " .. b.pid) end
end, { non_consuming = true })

--------------------
---- WORKSPACES ----
--------------------
bind("SUPER + D",      hl.dsp.focus({ workspace = "emptym" }), "Show desktop (empty workspace)")
bind("CTRL + ALT + D", hl.dsp.focus({ workspace = "emptym" }), "Show desktop (empty workspace)")

bind("CTRL + ALT + Left",          hl.dsp.focus({ workspace = "r-1" }),                       "Previous workspace")
bind("CTRL + ALT + Right",         hl.dsp.focus({ workspace = "r+1" }),                       "Next workspace")
bind("CTRL + ALT + Home",          hl.dsp.window.move({ workspace = "r-1", follow = true }),  "Take window to previous workspace")
bind("CTRL + ALT + End",           hl.dsp.window.move({ workspace = "r+1", follow = true }),  "Take window to next workspace")
bind("CTRL + ALT + SHIFT + Left",  hl.dsp.window.move({ workspace = "r-1", follow = true }),  "Take window to previous workspace")
bind("CTRL + ALT + SHIFT + Right", hl.dsp.window.move({ workspace = "r+1", follow = true }),  "Take window to next workspace")

-- 0 = workspace 10
for i = 1, 10 do
    local key = i % 10
    bind("CTRL + ALT + " .. key,   hl.dsp.focus({ workspace = i }),                       "Go to workspace " .. i)
    bind("CTRL + SUPER + " .. key, hl.dsp.window.move({ workspace = i, follow = false }), "Send window to workspace " .. i)
end
