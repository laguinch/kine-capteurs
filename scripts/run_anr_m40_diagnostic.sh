#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BLUETOOTH_SERVICE_NAME="${KINE_BLUETOOTH_SERVICE_NAME:-kine-capteurs-bluetooth}"
UPDATE_MARKER="$PROJECT_DIR/storage/raw_data/update_in_progress"

restore_kinvent() {
  systemctl stop bluetooth.service 2>/dev/null || true
  systemctl mask bluetooth.service 2>/dev/null || true
  if command -v hciconfig >/dev/null 2>&1; then
    for controller_path in /sys/class/bluetooth/hci*; do
      [[ -e "$controller_path" ]] || continue
      hciconfig "${controller_path##*/}" down || true
    done
  fi
  if [[ ! -e "$UPDATE_MARKER" ]]; then
    systemctl restart "$BLUETOOTH_SERVICE_NAME" || true
  fi
}
trap restore_kinvent EXIT INT TERM

cd "$PROJECT_DIR"
systemctl kill --kill-who=all --signal=SIGKILL \
  "$BLUETOOTH_SERVICE_NAME" 2>/dev/null || true
timeout 10s systemctl stop "$BLUETOOTH_SERVICE_NAME" 2>/dev/null || true
pkill -KILL -f "$PROJECT_DIR/scripts/[k]invent_dual_hci.py" || true
pkill -KILL -f "$PROJECT_DIR/scripts/[k]invent_kpush_hci.py" || true

systemctl unmask bluetooth.service
systemctl start bluetooth.service

"$PROJECT_DIR/.venv/bin/python" -u \
  "$PROJECT_DIR/scripts/anr_m40_diagnostic.py" "$@"
