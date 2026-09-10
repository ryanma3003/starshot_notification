#!/usr/bin/env bash
# Starts a virtual screen, exposes it over noVNC, then runs the watcher against it.
set -euo pipefail

if [ -z "${VNC_PASSWORD:-}" ]; then
    echo "FATAL: VNC_PASSWORD is not set." >&2
    echo "This container holds a live, authenticated Apple session. Refusing to" >&2
    echo "start an unauthenticated VNC server. Set VNC_PASSWORD in your .env." >&2
    exit 1
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT

echo "Starting Xvfb on ${DISPLAY} (${SCREEN_GEOMETRY})"
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_GEOMETRY}" -nolisten tcp &
for _ in $(seq 1 30); do
    xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 && break
    sleep 0.5
done

fluxbox >/dev/null 2>&1 &   # a window manager, so Chromium gets a usable frame

mkdir -p /root/.vnc
x11vnc -storepasswd "${VNC_PASSWORD}" /root/.vnc/passwd >/dev/null 2>&1
echo "Starting x11vnc on :5900"
x11vnc -display "${DISPLAY}" -rfbauth /root/.vnc/passwd -rfbport 5900 \
       -forever -shared -noxdamage -quiet &

echo "Starting noVNC on :6080"
websockify --web=/usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &

echo
echo "  noVNC ready. Tunnel to it, then open http://localhost:6080/vnc.html"
echo "    ssh -N -L 6080:localhost:6080 you@this-server"
echo

# Headed, because a human has to complete AppleConnect sign-in in this window.
export HEADLESS=false
exec python watch.py
