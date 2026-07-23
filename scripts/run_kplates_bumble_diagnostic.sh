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
RUN_SINGLE_PHASES="${KPLATES_BUMBLE_DIAGNOSTIC_SINGLE:-0}"

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

require_nrf_ready() {
  log ""
  log "===== vérification dongle nRF52840 ====="

  if ! lsusb | grep -q '2fe3:000b'; then
    log "ERREUR: dongle nRF52840 Zephyr HCI USB introuvable dans lsusb."
    log "Action: débranche/rebranche le dongle nRF52840, attends 5 secondes, puis relance ce diagnostic."
    exit 1
  fi

  local hci_output
  hci_output="$(hciconfig -a 2>/dev/null || true)"
  log "$hci_output"

  if echo "$hci_output" | grep -q 'ACL MTU: 0:0'; then
    log "ERREUR: le dongle nRF52840 est visible en USB mais son contrôleur HCI n'est pas initialisé correctement."
    log "Indice: hciconfig affiche « ACL MTU: 0:0 », état typique après un blocage USB/HCI."
    log "Action: débranche/rebranche physiquement le dongle nRF52840, attends 5 secondes, puis relance ce diagnostic sans refaire update.sh."
    exit 1
  fi

  log "Dongle nRF52840 visible et contrôleur HCI initialisé."
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
log "Phases seules droite/gauche: $([[ "$RUN_SINGLE_PHASES" == "1" ]] && echo "activées" || echo "ignorées par défaut")"
log "Log: $LOG"
log "Capture: $PCAP"

confirm_phase \
  "Préparation générale" \
  "1. Ferme complètement l'application Kinvent Android." \
  "2. Coupe le Bluetooth du téléphone Android pendant tout le diagnostic." \
  "3. Pose les plateformes au sol, proches du dongle nRF52840." \
  "4. Ne monte pas dessus pour l'instant." \
  "5. Ne lance aucun jeu ni test depuis l'interface web pendant ce diagnostic."

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

require_nrf_ready

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

if [[ "$RUN_SINGLE_PHASES" == "1" ]]; then
  confirm_phase \
    "1/4 — Plateforme droite seule" \
    "Objectif: vérifier que la plateforme droite fonctionne seule avec Bumble." \
    "Plateformes: droite allumée, gauche complètement éteinte." \
    "Important: si la gauche est allumée, cette phase n'est pas interprétable." \
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
    "Si elle ne revient pas en état disponible, éteins-la puis attends son arrêt complet." \
    "Prépare ensuite la phase gauche: droite éteinte, gauche allumée."

  confirm_phase \
    "2/4 — Plateforme gauche seule" \
    "Objectif: vérifier que la plateforme gauche fonctionne seule avec Bumble." \
    "Plateformes: gauche allumée, droite complètement éteinte." \
    "Important: si la droite est allumée, cette phase n'est pas interprétable." \
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
    "Si elle ne revient pas en état disponible, éteins-la puis attends son arrêt complet." \
    "Prépare ensuite les phases doubles: rallume les deux plateformes et attends leur clignotement disponible."
else
  log ""
  log "Phases droite seule/gauche seule ignorées."
  log "Raison: le problème à diagnostiquer concerne les deux plateformes allumées ensemble."
fi

confirm_phase \
  "1/2 — Double connexion sans flux" \
  "Objectif: vérifier que Bumble tient deux connexions BLE simultanées sans mesures." \
  "Plateformes: droite ET gauche allumées, disponibles, application Android fermée." \
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
  "2/2 — Double flux officiel avec appuis" \
  "Objectif: vérifier que Bumble tient les deux plateformes avec le flux de mesures officiel." \
  "Plateformes: droite ET gauche allumées, disponibles, application Android fermée." \
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
