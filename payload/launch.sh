#!/bin/sh
# Invoked by boot.sh after the stock reader completes its empty-slot check.
# USB files are data only: no script or configuration from USB is executed.
PATH=/bin:/sbin:/usr/bin:/usr/sbin
LC_ALL=C
export PATH LC_ALL

RUN=/tmp/atari-sideload
PAYLOAD=/oem/sideload
mkdir -p "$RUN" || exit 1
# One attempt per power cycle. Returning 1 selects the original cartridge menu.
[ ! -e "$RUN/attempted" ] || exit 1
touch "$RUN/attempted" || exit 1
LOG="$RUN/loader.log"
# Retain the last boot's diagnostics for retrieval in system update mode.
if mkdir -p /userdata/sideload && [ -w /userdata/sideload ]; then
    LOG=/userdata/sideload/last-boot.log
fi
exec >"$LOG" 2>&1
echo "Sideload 0.3.6 startup"
fail() {
    echo "Sideload skipped: $*"
    exit 1
}

KEYS=/sys/devices/platform/gpio-keys-polled/keys
read_switches() {
    raw=$(cat "$KEYS") || fail "console switches unavailable"
    case "$raw" in *[!0-9]*|'') fail "invalid console switch value" ;; esac
    [ "$raw" -le 65535 ] || fail "console switch value out of range"
}

read_switches
[ "$((raw & 128))" -eq 0 ] || fail "physical cartridge detected; starting the stock menu"
SOURCE= selected=
internal="$PAYLOAD/internal"
if [ -f "$PAYLOAD/catalog.txt" ]; then
    [ -x "$PAYLOAD/menu" ] || fail "game selector unavailable"
    if [ -w /sys/class/drm/card0-HDMI-A-1/mode ]; then
        printf '0\n' > /sys/class/drm/card0-HDMI-A-1/mode || fail "HDMI mode setup failed"
    fi
    rm -f "$RUN/selection" "$RUN/selection.new"
    "$PAYLOAD/menu" "$PAYLOAD/catalog.txt" "$RUN/selection"
    result=$?
    echo "Selector returned status $result"
    [ "$result" -ne 2 ] || fail "cartridge detected in menu; starting the stock menu"
    [ "$result" -eq 0 ] || fail "game selector exited with status $result"
    IFS= read -r selected < "$RUN/selection" || fail "game selection missing"
    case "$selected" in *[!a-f0-9]*|'') fail "invalid selected game" ;; esac
    [ "${#selected}" -eq 64 ] || fail "invalid selected game length"
    SOURCE="$PAYLOAD/games/$selected"
elif [ -f "$internal/manifest.txt" ] && [ -f "$internal/game.rom" ]; then
    SOURCE=$internal
fi
# An installed internal game takes priority over USB; a real cartridge skips
# the internal game. Boot with an empty slot for internal-game play.
tries=0
while [ -z "$SOURCE" ] && [ "$tries" -lt 20 ]; do
    for mountpoint in /media/usb0 /media/usb1 /media/usb2 /media/usb3 \
                      /media/usb4 /media/usb5 /media/usb6 /media/usb7; do
        candidate="$mountpoint/atari-sideload"
        if [ -f "$candidate/manifest.txt" ] && [ -f "$candidate/game.rom" ]; then
            SOURCE=$candidate
            break
        fi
    done
    [ -z "$SOURCE" ] || break
    sleep 0.25
    tries=$((tries + 1))
done
[ -n "$SOURCE" ] || fail "no internal game or prepared USB drive found"
read_switches
if [ "$((raw & 128))" -ne 0 ]; then
    fail "physical cartridge detected; starting the stock menu"
fi
echo "Game source: $SOURCE; physical switches: $raw"

magic= system= digest= length=
{
    IFS= read -r magic && IFS= read -r system &&
    IFS= read -r digest && IFS= read -r length
} < "$SOURCE/manifest.txt" || fail "incomplete manifest"
[ "$magic" = "ATARI_SIDELOAD_V1" ] || fail "unknown manifest format"
case "$system" in
    2600) core="$PAYLOAD/stella_usb.so"; rom="$RUN/game.a26" ;;
    7800) core="$PAYLOAD/prosystem_usb.so"; rom="$RUN/game.a78" ;;
    *) fail "unknown console type" ;;
esac
case "$digest" in *[!a-f0-9]*|'') fail "invalid ROM checksum" ;; esac
[ "${#digest}" -eq 64 ] || fail "invalid ROM checksum length"
if [ -n "$selected" ]; then
    [ "$selected" = "$digest" ] || fail "selected game does not match its manifest"
fi
case "$length" in *[!0-9]*|'') fail "invalid ROM size" ;; esac
[ "${#length}" -le 7 ] || fail "ROM too large"
[ "$length" -ge 128 ] && [ "$length" -le 4194432 ] || fail "ROM size out of range"
[ -r "$core" ] || fail "emulator core missing"
[ "$(wc -c < "$SOURCE/game.rom")" -eq "$length" ] || fail "source ROM size mismatch"

# Run from RAM so unplugging the drive does not remove the active ROM file.
cp "$SOURCE/game.rom" "$rom" || fail "could not copy ROM to RAM"
[ "$(wc -c < "$rom")" -eq "$length" ] || fail "RAM ROM size mismatch"
actual=$(sha256sum "$rom") || fail "could not hash ROM"
[ "${actual%% *}" = "$digest" ] || fail "ROM checksum mismatch"

read_switches
# Pac-Man edition uses bit 7 for cartridge presence. Other bits remain live.
printf '%s\n' "$((raw | 128))" > "$RUN/keys" || fail "could not initialize virtual switches"

# Keep RetroArch-managed saves separate for each ROM's content hash.
# The cores' own preferences, SaveKey and high-score files remain shared.
SAVE=/userdata/sideload/$digest
mkdir -p "$SAVE/saves" "$SAVE/states" || fail "save storage unavailable"
cat > "$RUN/override.cfg" <<EOF || fail "could not create emulator configuration"
config_save_on_exit = "false"
savefile_directory = "$SAVE/saves"
savestate_directory = "$SAVE/states"
EOF
[ -s "$RUN/override.cfg" ] || fail "could not create emulator configuration"
[ -r /tmp/retroarch.cfg ] || fail "stock emulator configuration missing"

echo "Starting $system ROM ($length bytes), SHA256 $digest"
# Same display-mode setup performed by the stock cartridge frontend.
if [ -w /sys/class/drm/card0-HDMI-A-1/mode ]; then
    printf '0\n' > /sys/class/drm/card0-HDMI-A-1/mode || fail "HDMI mode setup failed"
fi
/bin/sh "$PAYLOAD/keys.sh" "$SOURCE" &
monitor=$!
cleanup() {
    kill "$monitor" 2>/dev/null
    wait "$monitor" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
cd /oem/vendor/bin || exit 1
/bin/nice -n -20 ./retroarch -c /tmp/retroarch.cfg --appendconfig "$RUN/override.cfg" \
    -L "$core" "$rom"
status=$?
echo "Emulator exited with status $status; returning to cartridges."
# The original scheduler will start the cartridge menu on this same invocation.
exit 1
