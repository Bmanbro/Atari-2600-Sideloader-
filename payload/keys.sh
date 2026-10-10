#!/bin/sh
# Supply live console switch values to the sideload-only emulator copies.
# Never write to a GPIO, serial port, or any /sys file.
PATH=/bin:/sbin:/usr/bin:/usr/sbin
LC_ALL=C
export PATH LC_ALL
KEYS=/sys/devices/platform/gpio-keys-polled/keys
RUN=/tmp/atari-sideload
SOURCE=$1
last=
while :; do
    if ! raw=$(cat "$KEYS"); then
        break
    fi
    case "$raw" in *[!0-9]*|'') break ;; esac
    [ "$raw" -le 65535 ] || break
    # Removing the prepared drive lets the core's normal removal handling exit.
    [ -f "$SOURCE/manifest.txt" ] && [ -f "$SOURCE/game.rom" ] || break
    value=$((raw | 128))
    if [ "$value" != "$last" ]; then
        printf '%s\n' "$value" > "$RUN/keys.new" || break
        mv -f "$RUN/keys.new" "$RUN/keys" || break
        last=$value
    fi
    sleep 0.02
done
# Clear only the synthetic cartridge bit, retaining the last valid switches.
case "$last" in *[!0-9]*|'') last=0 ;; esac
printf '%s\n' "$((last & ~128))" > "$RUN/keys.new" &&
    mv -f "$RUN/keys.new" "$RUN/keys"
