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
AUTO_CONFIRM="${KPLATES_BUMBLE_DIAGNOSTIC_AUTO:-0}"

mkdir -p storage/raw_data

MON_PID=""
SERVICE_WAS_ACTIVE="unknown"

log() {
  echo "$@" | tee -a "$LOG"
}

confirm_phase() {
  local title="$1"
  shift
  log ""
  log "================================================================"
  log "PHASE: $title"
  log "================================================================"
  while (($#)); do
    log "$1"
    shift
  done
  log "----------------------------------------------------------------"
  if [[ "$AUTO_CONFIRM" == "1" ]]; then
    log "Mode automatique: validation passée."
    return 0
  fi
  read -r -p "Quand c'est prêt, appuie sur Entrée pour lancer cette phase (Ctrl+C pour arrêter). "
  log "Validation utilisateur: $title"
}

run_step() {
  local name="$1"
  shift
  log ""
  log "===== $name ====="
  "$@" 2>&1 | tee -a "$LOG"
  local code=${PIPESTATUS[0]}
  log "===== $name: code=$code ====="
  return "$code"
}

run_required_step() {
  local name="$1"
  shift
  run_step "$name" "$@"
  local code=$?
  if [[ "$code" -eq 0 ]]; then
    return 0
  fi

  log ""
  log "ARRÊT DU DIAGNOSTIC"
  log "La phase « $name » a échoué avec le code $code."
  log "Les phases suivantes ne seraient pas interprétables, donc elles ne sont pas lancées."
  log "Dans ce cas, inutile de monter sur les plateformes: le flux de mesures n'a pas démarré."
  exit "$code"
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

confirm_phase \
  "Préparation générale" \
  "1. Allume les deux plateformes." \
  "2. Pose-les au sol, proches du dongle nRF52840." \
  "3. Ne monte pas dessus pour l'instant." \
  "4. Ne lance aucun jeu ni test depuis l'interface web pendant ce diagnostic."

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
  confirm_phase \
    "Libération du dongle Bluetooth" \
    "Le service Bluetooth normal va être arrêté temporairement." \
    "C'est nécessaire pour que Bumble prenne le contrôle exclusif du dongle." \
    "À la fin du diagnostic, le service sera redémarré automatiquement."
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

confirm_phase \
  "1/4 — Plateforme droite seule" \
  "Objectif: vérifier que la plateforme droite fonctionne seule avec Bumble." \
  "Plateformes: droite allumée, gauche allumée possible mais inutilisée." \
  "Action physique: ne monte pas dessus, laisse les plateformes vides." \
  "Durée prévue: environ 10 secondes."
run_required_step "droite seule" \
  sudo "$PYTHON_BIN" -u scripts/kinvent_kplates_bumble.py \
    "${COMMON_ARGS[@]}" \
    --sides right \
    --duration 10 \
    --csv "${CSV_PREFIX}_right.csv"

confirm_phase \
  "Vérification LED après droite seule" \
  "Regarde la plateforme droite avant de continuer." \
  "Si elle semble encore connectée ou dans un état anormal, attends quelques secondes." \
  "Si elle ne revient pas en état disponible, éteins/rallume la plateforme puis attends son clignotement normal."

confirm_phase \
  "2/4 — Plateforme gauche seule" \
  "Objectif: vérifier que la plateforme gauche fonctionne seule avec Bumble." \
  "Action physique: ne monte pas dessus, laisse les plateformes vides." \
  "Durée prévue: environ 10 secondes."
run_required_step "gauche seule" \
  sudo "$PYTHON_BIN" -u scripts/kinvent_kplates_bumble.py \
    "${COMMON_ARGS[@]}" \
    --sides left \
    --duration 10 \
    --csv "${CSV_PREFIX}_left.csv"

confirm_phase \
  "Vérification LED après gauche seule" \
  "Regarde la plateforme gauche avant de continuer." \
  "Si elle semble encore connectée ou dans un état anormal, attends quelques secondes." \
  "Si elle ne revient pas en état disponible, éteins/rallume la plateforme puis attends son clignotement normal."

confirm_phase \
  "3/4 — Double connexion sans flux" \
  "Objectif: vérifier que Bumble tient deux connexions BLE simultanées sans mesures." \
  "Action physique: ne monte pas dessus." \
  "À surveiller: les deux plateformes doivent passer/connecter normalement." \
  "Durée prévue: environ 20 secondes."
run_required_step "double connexion seule" \
  sudo "$PYTHON_BIN" -u scripts/kinvent_kplates_bumble.py \
    "${COMMON_ARGS[@]}" \
    --sides both \
    --diagnostic connect-only \
    --duration 20 \
    --csv "${CSV_PREFIX}_connect_only.csv"

confirm_phase \
  "4/4 — Double flux officiel avec appuis" \
  "Objectif: vérifier que Bumble tient les deux plateformes avec le flux de mesures officiel." \
  "Début de phase: laisse les plateformes vides pendant la connexion et la tare." \
  "Quand tu vois « Flux droite démarré » et « Flux gauche démarré », monte calmement dessus." \
  "Ensuite: reste stable 5 secondes, puis transfère doucement le poids gauche/droite." \
  "Durée prévue: 30 secondes de flux + 30 secondes de maintien Bluetooth."
run_required_step "double flux officiel" \
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
