#!/bin/sh
# Let the stock cartridge reader complete before offering internal games.
PATH=/bin:/sbin:/usr/bin:/usr/sbin
LC_ALL=C
export PATH LC_ALL

RUN=/tmp/atari-sideload
PAYLOAD=/oem/sideload
STOCK=/oem/vendor/bin/dmenu2.bin
mkdir -p "$RUN" || exec "$STOCK"
# Preserve the launcher's single sideload attempt per power cycle.
if [ -e "$RUN/attempted" ]; then
    exec "$STOCK"
fi

LOG="$RUN/cartridge.log"
if mkdir -p /userdata/sideload && [ -w /userdata/sideload ]; then
    LOG=/userdata/sideload/last-cartridge.log
fi
exec >"$LOG" 2>&1
echo "Sideload 0.3.6: starting the stock cartridge reader first"
cd /oem/vendor/bin || exec "$STOCK"

# After the stock no-cart reply or the observed 55 AA 02 FF EA E8 reply
# with physical cartridge status clear, release display/UART and exit 42.
# Successful cartridge reads still create /tmp/.next and return status 0.
# The firmware's own stdbuf library makes diagnostics visible even if the
# frontend stays on its no-cartridge/error screen until power-off.
if [ -r /usr/libexec/coreutils/libstdbuf.so ]; then
    LD_PRELOAD=/usr/libexec/coreutils/libstdbuf.so _STDBUF_O=0 _STDBUF_E=0 \
        "$PAYLOAD/cart-first.bin"
else
    "$PAYLOAD/cart-first.bin"
fi
status=$?
echo "Cartridge frontend returned status $status"
sync
case "$status" in
    0)
        # S50launcher's original scheduler executes /tmp/.next, including
        # the original cartridge ROM, emulator core and configuration.
        exit 0
        ;;
    42)
        echo "Reader accepted a no-game reply; opening the sideload launcher"
        sync
        /bin/sh "$PAYLOAD/launch.sh"
        echo "Sideload launcher returned; resuming the original cartridge frontend"
        ;;
    *)
        # A failed reader, missing executable or signal is not an empty slot.
        echo "Cartridge frontend failed; resuming the original cartridge frontend"
        ;;
esac
sync
exec "$STOCK"
