#!/bin/bash
set -e

CERT_FILE="/home/jetson/robot/telepresence/certs/cert.pem"
KEY_FILE="/home/jetson/robot/telepresence/certs/key.pem"
SCHEME="http"

if [ "${TELEPRESENCE_HTTP:-0}" != "1" ] && [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
  SCHEME="https"
fi

URL="${SCHEME}://127.0.0.1:8088/robot-display"

BROWSER_BIN="$(command -v chromium-browser || command -v chromium || command -v google-chrome || command -v x-www-browser)"

if [ -z "$BROWSER_BIN" ]; then
  echo "No supported browser found."
  exit 1
fi

"$BROWSER_BIN" \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --autoplay-policy=no-user-gesture-required \
  "$URL"
