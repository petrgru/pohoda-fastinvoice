#! /usr/bin/env bash

# Builds and runs NAPS2 with the ESCL scan-settings fix (cyanfish/naps2#976), so that
# scanner sharing also works for clients that follow the eSCL namespace. Without the
# fix, creating a scan job on a shared profile fails with 400.
#
#   scripts/naps2-patched-build.sh          build and run the patched NAPS2
#   scripts/naps2-patched-build.sh --stop   stop it and start the packaged NAPS2 again
#
# Only the build directory is touched - profiles and sharing settings in
# ~/.config/naps2 (or %AppData%\NAPS2 on Windows) are used as they are.

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAPS2_REPO="https://github.com/cyanfish/naps2"
NAPS2_DIR="${NAPS2_DIR:-$ROOT/../naps2}"
PR_REF="pull/976/head"

if [ "${1:-}" = "--stop" ] || [ "${1:-}" = "stop" ]; then
    pkill -f "$NAPS2_DIR/NAPS2.App.Gtk/bin" 2>/dev/null || true
    if command -v naps2 >/dev/null; then
        DISPLAY="${DISPLAY:-:0}" setsid nohup naps2 >/dev/null 2>&1 &
    fi
    echo "Stopped the patched NAPS2, the packaged one is running again."
    exit 0
fi

if ! command -v dotnet >/dev/null; then
    echo "dotnet SDK (10 or 11) is required:"
    echo "  curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh"
    echo "  bash /tmp/dotnet-install.sh --channel 11.0 --install-dir ~/.dotnet"
    echo "  export PATH=\"\$HOME/.dotnet:\$PATH\""
    exit 1
fi

if [ -d "$NAPS2_DIR/.git" ]; then
    echo "Using the existing checkout in $NAPS2_DIR"
    git -C "$NAPS2_DIR" clean -fdq NAPS2.App.Gtk/bin NAPS2.App.Gtk/obj 2>/dev/null || true
else
    echo "Cloning NAPS2 to $NAPS2_DIR"
    git clone --filter=blob:none "$NAPS2_REPO" "$NAPS2_DIR"
fi

# The fix as a pull request; once it is merged, master is enough on its own.
if git -C "$NAPS2_DIR" fetch "$NAPS2_REPO" "$PR_REF" 2>/dev/null; then
    git -C "$NAPS2_DIR" checkout -q -b escl-scanjob-namespace FETCH_HEAD
else
    echo "Could not fetch the pull request, assuming it is merged into master."
    git -C "$NAPS2_DIR" checkout -q master
    git -C "$NAPS2_DIR" pull --ff-only
fi

echo "Building the Linux app (this can take a few minutes)"
cd "$NAPS2_DIR"
dotnet publish NAPS2.App.Gtk -c Release -r linux-x64 --nologo -v q

BIN="$(find "$NAPS2_DIR/NAPS2.App.Gtk/bin/Release" -type f -name naps2 -path '*linux*' | head -1)"
if [ -z "$BIN" ]; then
    echo "The build did not produce an executable." >&2
    exit 1
fi

# The packaged NAPS2 would keep the shared profile on port 9801 to itself.
pkill -x naps2 2>/dev/null || true
sleep 2
DISPLAY="${DISPLAY:-:0}" setsid nohup "$BIN" >/tmp/naps2-patched.log 2>&1 &
sleep 15

# Any shared profile is enough for this check; delete the job it creates.
SETTINGS='<?xml version="1.0"?><scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011-05-03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"><pwg:Version>2.6</pwg:Version><scan:XResolution>150</scan:XResolution><scan:YResolution>150</scan:YResolution><scan:ColorMode>BlackAndWhite1</scan:ColorMode><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat></scan:ScanSettings>'
for port in $(seq 9801 9850); do
    response="$(curl -s -m 5 -D - -o /dev/null -X POST "http://localhost:$port/eSCL/ScanJobs" \
        -H "Content-Type: text/xml" --data "$SETTINGS" 2>/dev/null || true)"
    case "$response" in
    *201*)
        location="$(printf '%s' "$response" | tr -d '\r' | grep -i '^location:' | cut -d' ' -f2)"
        [ -n "$location" ] && curl -s -m 5 -o /dev/null -X DELETE "$location"
        echo "OK - the patched NAPS2 accepts scan settings on port $port."
        echo "Log: /tmp/naps2-patched.log, revert: scripts/naps2-patched-build.sh --stop"
        exit 0
        ;;
    esac
done

echo "No shared profile answered on ports 9801-9850 - share one in NAPS2 and run this again." >&2
exit 1
