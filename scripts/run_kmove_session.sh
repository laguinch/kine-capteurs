#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DURATION="${1:-30}"
CSV_NAME="${2:-kmove_test.csv}"
REFERENCE_DURATION="${3:-2}"
CONTROL_FILE="${4:-$PROJECT_DIR/storage/raw_data/kmove_worker_control.json}"
BLUETOOTH_SERVICE_NAME="${KINE_BLUETOOTH_SERVICE_NAME:-kine-capteurs-bluetooth}"
UPDATE_MARKER="$PROJECT_DIR/storage/raw_data/update_in_progress"
SESSION_LOCK="/run/lock/kine-capteurs-hci-session.lock"

if [[ ! "$DURATION" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Durée K-Move invalide." >&2
  exit 2
fi
if [[ ! "$REFERENCE_DURATION" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  echo "Durée de référence invalide." >&2
  exit 2
fi
if [[ "$CSV_NAME" != "$(basename "$CSV_NAME")" || "$CSV_NAME" != *.csv ]]; then
  echo "Nom CSV K-Move invalide." >&2
  exit 2
fi

exec 9>"$SESSION_LOCK"
if ! flock -n 9; then
  echo "Un autre capteur Kinvent utilise déjà le dongle Bluetooth." >&2
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
systemctl kill --kill-who=all --signal=SIGKILL \
  "$BLUETOOTH_SERVICE_NAME" 2>/dev/null || true
pkill -f "$PROJECT_DIR/scripts/kinvent_dual_hci.py" || true

# Le service systemd peut être arrêté alors que son ancien canal HCI_USER est
# encore en cours de fermeture. Attendre sa disparition évite que la première
# commande HCI Reset du K-Move parte dans un contrôleur encore occupé.
for _ in 1 2 3 4 5; do
  if ! pgrep -f "$PROJECT_DIR/scripts/[k]invent_dual_hci.py" >/dev/null; then
    break
  fi
  sleep 1
done

if command -v hciconfig >/dev/null 2>&1; then
  for controller_path in /sys/class/bluetooth/hci*; do
    [[ -e "$controller_path" ]] || continue
    hciconfig "${controller_path##*/}" down || true
  done
fi
sleep 1

"$PROJECT_DIR/.venv/bin/python" -u \
  "$PROJECT_DIR/scripts/kinvent_kmove_hci.py" \
  --adapter hci0 \
  --duration "$DURATION" \
  --reference-duration "$REFERENCE_DURATION" \
  --control-file "$CONTROL_FILE" \
  --csv "$PROJECT_DIR/storage/raw_data/$CSV_NAME"
