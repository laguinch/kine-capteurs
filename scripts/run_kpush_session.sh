#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DURATION="${1:-30}"
CSV_NAME="${2:-kpush_test.csv}"
TARE_DURATION="${3:-2}"
CONTROL_FILE="${4:-$PROJECT_DIR/storage/raw_data/kpush_worker_control.json}"
BLUETOOTH_SERVICE_NAME="${KINE_BLUETOOTH_SERVICE_NAME:-kine-capteurs-bluetooth}"
UPDATE_MARKER="$PROJECT_DIR/storage/raw_data/update_in_progress"
SESSION_LOCK="/run/lock/kine-capteurs-hci-session.lock"
KPLATES_STATE="$PROJECT_DIR/storage/raw_data/kplates_worker_state.json"

if [[ ! "$DURATION" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Durée K-Push invalide." >&2
  exit 2
fi
if [[ ! "$TARE_DURATION" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Durée de tare invalide." >&2
  exit 2
fi
if [[ "$CSV_NAME" != "$(basename "$CSV_NAME")" || "$CSV_NAME" != *.csv ]]; then
  echo "Nom CSV K-Push invalide." >&2
  exit 2
fi

exec 9>"$SESSION_LOCK"
if ! flock -n 9; then
  echo "Un autre capteur Kinvent utilise déjà le dongle Bluetooth." >&2
  exit 1
fi

if systemctl is-active --quiet "$BLUETOOTH_SERVICE_NAME" \
  && [[ -f "$KPLATES_STATE" ]] \
  && ! grep -Eq '"phase": "(disconnected|error)"' "$KPLATES_STATE"; then
  echo "Les K-Force Plates sont encore connectées. Cliquez d'abord sur « Déconnecter les capteurs » depuis leur page." >&2
  exit 1
fi

restore_plates() {
  if [[ -e "$UPDATE_MARKER" ]]; then
    return
  fi
  systemctl restart "$BLUETOOTH_SERVICE_NAME" || true
}
trap restore_plates EXIT INT TERM

cd "$PROJECT_DIR"
systemctl stop "$BLUETOOTH_SERVICE_NAME"
pkill -f "$PROJECT_DIR/scripts/kinvent_dual_hci.py" || true
if command -v hciconfig >/dev/null 2>&1; then
  for controller_path in /sys/class/bluetooth/hci*; do
    [[ -e "$controller_path" ]] || continue
    hciconfig "${controller_path##*/}" down || true
  done
fi

"$PROJECT_DIR/.venv/bin/python" -u \
  "$PROJECT_DIR/scripts/kinvent_kpush_hci.py" \
  --adapter hci0 \
  --duration "$DURATION" \
  --tare-duration "$TARE_DURATION" \
  --control-file "$CONTROL_FILE" \
  --csv "$PROJECT_DIR/storage/raw_data/$CSV_NAME"
