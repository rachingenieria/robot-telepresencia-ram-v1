#!/bin/bash
set -e

cd /home/jetson/robot/telepresence
export IDLE_ASSISTANT="${IDLE_ASSISTANT:-1}"
export IDLE_ASSISTANT_AUTOSTART="${IDLE_ASSISTANT_AUTOSTART:-1}"
export TELEPRESENCE_RESTART="${TELEPRESENCE_RESTART:-1}"
LOG_FILE="/tmp/telepresence_desktop_launch.log"

echo "[INFO] Launching telepresence from desktop..."
echo "[INFO] Log file: $LOG_FILE"

set +e
/home/jetson/robot/telepresence/start_telepresence.sh 2>&1 | tee "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}
set -e

if [ "$EXIT_CODE" -ne 0 ]; then
  echo
  echo "[ERROR] Telepresence failed to start (exit $EXIT_CODE)."
  echo "[ERROR] The full log was saved to: $LOG_FILE"
  echo "[INFO] Press Enter to close this window."
  read -r _
fi

exit "$EXIT_CODE"
