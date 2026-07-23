#!/usr/bin/env bash
set -u
set -o pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="/tmp/kplates-bumble-diagnostic-$STAMP.log"
PCAP="/tmp/kplates-bumble-diagnostic-$STAMP.pcap"
CSV_PREFIX="storage/raw_data/kplates_bumble_diagnostic_$STAMP"
USBMON_IFACE="${USBMON_IFACE:-usbmon1}"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
TRANSPORT="${KINE_BUMBLE_TRANSPORT:-usb:0}"

mkdir -p storage/raw_data

MON_PID=""
SERVICE_WAS_ACTIVE="unknown"

log() {
  echo "$@" | tee -a "$LOG"
}

run_step() {
  local name="$1"
  shift
  log ""
  log "===== $name ====="
  "$@" 2>&1 | tee -a "$LOG"
  local code=${PIPESTATUS[0]}
  log "===== $name: code=$code ====="
  return 0
}

cleanup() {
  if [[ -n "$MON_PID" ]]; then
    sudo kill "$MON_PID" >/dev/null 2>&1 || true
  fi
  if [[ "$SERVICE_WAS_ACTIVE" == "active" ]]; then
    sudo systemctl restart kine-capteurs-bluetooth >/dev/null 2>&1 || true
  fi
  sudo chmod a+r "$LOG" "$PCAP" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "Diagnostic Bumble K-Force Plates"
log "Projet: $PROJECT_DIR"
log "Transport Bumble: $TRANSPORT"
log "Interface capture USB: $USBMON_IFACE"
log "Log: $LOG"
log "Capture: $PCAP"

log ""
log "===== environnement ====="
{
  date
  lsusb
  hciconfig -a 2>/dev/null || true
  grep -E 'KINE_BLUETOOTH_BACKEND|KINE_BUMBLE_TRANSPORT|KINE_KPLATES_BACKEND|KINE_HCI_ADAPTER' .env 2>/dev/null || true
  grep -E 'CONFIG_BT_MAX_CONN|CONFIG_BT_CTLR_RX_BUFFERS|CONFIG_BT_BUF_ACL_TX_COUNT|CONFIG_BT_BUF_EVT_RX_COUNT|CONFIG_BT_BUF_ACL_RX_COUNT_EXTRA' \
    "$HOME/zephyrproject-4.4/build/zephyr/.config" \
    "$HOME/zephyrproject/build/zephyr/.config" 2>/dev/null || true
} 2>&1 | tee -a "$LOG"

if systemctl is-active --quiet kine-capteurs-bluetooth; then
  SERVICE_WAS_ACTIVE="active"
  log ""
  log "Arrêt temporaire du service Bluetooth pour libérer le dongle."
  sudo systemctl stop kine-capteurs-bluetooth 2>&1 | tee -a "$LOG"
else
  SERVICE_WAS_ACTIVE="inactive"
fi

log ""
log "Activation usbmon et capture USB."
sudo modprobe usbmon 2>&1 | tee -a "$LOG"
sudo tcpdump -i "$USBMON_IFACE" -w "$PCAP" >/tmp/kplates-bumble-diagnostic-tcpdump-$STAMP.log 2>&1 &
MON_PID=$!
sleep 1

COMMON_ARGS=(
  --transport "$TRANSPORT"
  --tare-duration 2
  --calibration-file storage/raw_data/kplates_calibration.json
)

run_step "droite seule" \
  sudo "$PYTHON_BIN" -u scripts/kinvent_kplates_bumble.py \
    "${COMMON_ARGS[@]}" \
    --sides right \
    --duration 10 \
    --csv "${CSV_PREFIX}_right.csv"

run_step "gauche seule" \
  sudo "$PYTHON_BIN" -u scripts/kinvent_kplates_bumble.py \
    "${COMMON_ARGS[@]}" \
    --sides left \
    --duration 10 \
    --csv "${CSV_PREFIX}_left.csv"

run_step "double connexion seule" \
  sudo "$PYTHON_BIN" -u scripts/kinvent_kplates_bumble.py \
    "${COMMON_ARGS[@]}" \
    --sides both \
    --diagnostic connect-only \
    --duration 20 \
    --csv "${CSV_PREFIX}_connect_only.csv"

run_step "double flux officiel" \
  sudo "$PYTHON_BIN" -u scripts/kinvent_kplates_bumble.py \
    "${COMMON_ARGS[@]}" \
    --sides both \
    --duration 30 \
    --hold-after 30 \
    --csv "${CSV_PREFIX}_both_stream.csv"

log ""
log "===== résumé utile ====="
grep -E 'Ordre|Connexion plateforme|Pré-vol|Découverte|Lecture modèle|Flux .*démarré|Déconnexion Bumble|COMMAND_DISALLOWED|MEMORY_CAPACITY|GATT|Paires synchronisées|Acquisition double terminée|code=' "$LOG" || true

log ""
log "===== fichiers ====="
ls -lh "$LOG" "$PCAP" "${CSV_PREFIX}"*.csv 2>/dev/null | tee -a "$LOG" || true

