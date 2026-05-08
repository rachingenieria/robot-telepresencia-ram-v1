#!/bin/bash
set -e

cd /home/jetson/robot/telepresence

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

PYTHON_BIN="/home/jetson/robot/telepresence/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

EXTRA_ARGS=()
CERT_FILE="/home/jetson/robot/telepresence/certs/cert.pem"
KEY_FILE="/home/jetson/robot/telepresence/certs/key.pem"
SERIAL_PORT="/dev/ttyTHS1"
DEFAULT_ASSISTANT_SPEAKER="front:CARD=tegrasndt210ref,DEV=0"

if [ "${IDLE_ASISTANT:-0}" = "1" ] && [ "${IDLE_ASSISTANT:-0}" != "1" ]; then
  echo "[WARN] IDLE_ASISTANT detected; using it as IDLE_ASSISTANT."
  IDLE_ASSISTANT=1
fi

if [ "${IDLE_ASSISTANT:-0}" = "1" ]; then
  ASSISTANT_SPEAKER="${ASSISTANT_SPEAKER_DEVICE:-$DEFAULT_ASSISTANT_SPEAKER}"
  EXTRA_ARGS+=(
    --idle-assistant
    --assistant-root /home/jetson/robot/be-more-agent
    --assistant-mic-device plughw:CARD=camera,DEV=0
    --assistant-speaker-device "$ASSISTANT_SPEAKER"
    --assistant-model "${ASSISTANT_MODEL:-gemma3:1b}"
  )
  if [ "${IDLE_ASSISTANT_AUTOSTART:-0}" = "1" ]; then
    EXTRA_ARGS+=(--idle-assistant-autostart)
  fi
fi

SCHEME="http"
if [ "${TELEPRESENCE_HTTP:-0}" = "1" ]; then
  echo "[WARN] TELEPRESENCE_HTTP=1 set; starting without TLS."
elif [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
  SCHEME="https"
  EXTRA_ARGS+=(
    --https
    --cert-file "$CERT_FILE"
    --key-file "$KEY_FILE"
  )
else
  echo "[WARN] HTTPS cert/key not found; starting without TLS."
fi

echo "[INFO] Starting telepresence over ${SCHEME^^}."
echo "[INFO] Robot display: ${SCHEME}://127.0.0.1:8088/robot-display"
echo "[INFO] Status:        ${SCHEME}://127.0.0.1:8088/api/status"
if [ "${IDLE_ASSISTANT:-0}" = "1" ]; then
  echo "[INFO] Idle assistant speaker: ${ASSISTANT_SPEAKER}"
fi

if [ "${TELEPRESENCE_RESTART:-0}" = "1" ]; then
  echo "[INFO] Restart mode enabled. Closing previous telepresence instances on port 8088."
  sudo pkill -f "/home/jetson/robot/telepresence/.venv/bin/python server.py" >/dev/null 2>&1 || true
  sleep 1
fi

COMMAND=("$PYTHON_BIN")
if [ "${TELEPRESENCE_NO_SUDO:-0}" != "1" ] && [ -e "$SERIAL_PORT" ] && { [ ! -r "$SERIAL_PORT" ] || [ ! -w "$SERIAL_PORT" ]; }; then
  echo "[INFO] Serial port $SERIAL_PORT needs elevated permissions; starting server with sudo."
  COMMAND=(sudo -E "$PYTHON_BIN")
fi

"${COMMAND[@]}" server.py \
  --host 0.0.0.0 \
  --port 8088 \
  --serial-port "$SERIAL_PORT" \
  --front-camera /dev/video1 \
  --down-camera /dev/video0 \
  "${EXTRA_ARGS[@]}"
