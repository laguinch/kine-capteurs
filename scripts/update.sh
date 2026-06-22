#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${KINE_SERVICE_NAME:-kine-capteurs}"
BLUETOOTH_SERVICE_NAME="${KINE_BLUETOOTH_SERVICE_NAME:-kine-capteurs-bluetooth}"
SERVICE_USER="${KINE_SERVICE_USER:-${SUDO_USER:-$USER}}"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
UVICORN_BIN="$PROJECT_DIR/.venv/bin/uvicorn"
UNIT_FILE="/etc/systemd/system/$SERVICE_NAME.service"
BLUETOOTH_UNIT_FILE="/etc/systemd/system/$BLUETOOTH_SERVICE_NAME.service"
TEMP_UNIT="$(mktemp)"
TEMP_BLUETOOTH_UNIT="$(mktemp)"
UPDATE_MARKER="$PROJECT_DIR/storage/raw_data/update_in_progress"

cleanup() {
  rm -f "$TEMP_UNIT" "$TEMP_BLUETOOTH_UNIT"
  rm -f "$UPDATE_MARKER"
}
trap cleanup EXIT

cd "$PROJECT_DIR"
mkdir -p storage/raw_data
touch "$UPDATE_MARKER"

echo "Mise à jour du code..."
git pull --ff-only

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Création de l'environnement Python..."
  python3 -m venv .venv
fi

echo "Installation des dépendances..."
"$PYTHON_BIN" -m pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 0600 .env
  echo "Configuration .env créée."
fi

echo "Vérification du projet..."
"$PYTHON_BIN" -m unittest discover -s tests_library -p 'test*.py'
chmod 0755 scripts/run_kpush_session.sh
chmod 0755 scripts/run_anr_m40_diagnostic.sh
chmod 0755 scripts/run_kpull_diagnostic.sh
chmod 0755 scripts/run_kpull_session.sh
chmod 0755 scripts/run_kmove_diagnostic.sh
chmod 0755 scripts/run_kmove_session.sh

cat >"$TEMP_UNIT" <<EOF
[Unit]
Description=Kine Capteurs
After=network.target $BLUETOOTH_SERVICE_NAME.service
Wants=$BLUETOOTH_SERVICE_NAME.service

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=-$PROJECT_DIR/.env
ExecStart=$UVICORN_BIN app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >"$TEMP_BLUETOOTH_UNIT" <<EOF
[Unit]
Description=Kine Capteurs - connexion Bluetooth permanente
After=network.target
Before=$SERVICE_NAME.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_BIN -u $PROJECT_DIR/scripts/kinvent_dual_hci.py --adapter hci1 --tare-duration 2 --calibration-file $PROJECT_DIR/storage/raw_data/kplates_calibration.json --sync-tolerance-ms 20 --control-file $PROJECT_DIR/storage/raw_data/kplates_worker_control.json --state-file $PROJECT_DIR/storage/raw_data/kplates_worker_state.json
Restart=always
RestartSec=10
KillMode=control-group
TimeoutStopSec=5
StandardOutput=append:$PROJECT_DIR/storage/raw_data/kplates_worker.log
StandardError=append:$PROJECT_DIR/storage/raw_data/kplates_worker.log

[Install]
WantedBy=multi-user.target
EOF

sudo install -o root -g root -m 0644 "$TEMP_UNIT" "$UNIT_FILE"
sudo install -o root -g root -m 0644 \
  "$TEMP_BLUETOOTH_UNIT" "$BLUETOOTH_UNIT_FILE"
sudo systemctl daemon-reload
sudo KINE_SERVICE_USER="$SERVICE_USER" bash scripts/install_hci_sudoers.sh

echo "Libération du contrôleur Bluetooth..."
sudo systemctl stop bluetooth.service || true
sudo systemctl mask bluetooth.service || true
if command -v hciconfig >/dev/null 2>&1; then
  controller_found=false
  for controller_path in /sys/class/bluetooth/hci*; do
    [[ -e "$controller_path" ]] || continue
    controller="${controller_path##*/}"
    controller_found=true
    echo "Mise hors ligne de $controller..."
    sudo hciconfig "$controller" down || true
  done
  if [[ "$controller_found" == false ]]; then
    echo "Aucun contrôleur Bluetooth externe actuellement visible."
  fi
fi

echo "Démarrage des services Kine Capteurs..."
echo "Arrêt des anciens processus Bluetooth persistants..."
sudo pkill -TERM -f "$PROJECT_DIR/scripts/[r]un_kpush_session.sh" || true
sudo pkill -TERM -f "$PROJECT_DIR/scripts/[k]invent_kpush_hci.py" || true
sudo pkill -TERM -f "$PROJECT_DIR/scripts/[r]un_kpull_session.sh" || true
sudo pkill -TERM -f "$PROJECT_DIR/scripts/[k]invent_kpull_hci.py" || true
sudo pkill -TERM -f "$PROJECT_DIR/scripts/[r]un_kmove_session.sh" || true
sudo pkill -TERM -f "$PROJECT_DIR/scripts/[k]invent_kmove_hci.py" || true
sleep 1
sudo pkill -KILL -f "$PROJECT_DIR/scripts/[r]un_kpush_session.sh" || true
sudo pkill -KILL -f "$PROJECT_DIR/scripts/[k]invent_kpush_hci.py" || true
sudo pkill -KILL -f "$PROJECT_DIR/scripts/[r]un_kpull_session.sh" || true
sudo pkill -KILL -f "$PROJECT_DIR/scripts/[k]invent_kpull_hci.py" || true
sudo pkill -KILL -f "$PROJECT_DIR/scripts/[r]un_kmove_session.sh" || true
sudo pkill -KILL -f "$PROJECT_DIR/scripts/[k]invent_kmove_hci.py" || true
sudo systemctl kill --kill-who=all --signal=SIGKILL \
  "$BLUETOOTH_SERVICE_NAME" 2>/dev/null || true
sudo timeout 10s systemctl stop "$BLUETOOTH_SERVICE_NAME" 2>/dev/null || true
sudo pkill -KILL -f "$PROJECT_DIR/scripts/[k]invent_dual_hci.py" || true
rm -f \
  storage/raw_data/kplates_worker_state.json \
  storage/raw_data/kplates_worker_control.json
sudo systemctl enable "$BLUETOOTH_SERVICE_NAME"
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$BLUETOOTH_SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo
echo "Kine Capteurs est à jour."
echo "Interface: http://$(hostname -I | awk '{print $1}'):8000/"
echo "État: sudo systemctl status $SERVICE_NAME"
echo "Bluetooth: sudo systemctl status $BLUETOOTH_SERVICE_NAME"
