#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${KINE_SERVICE_NAME:-kine-capteurs}"
SERVICE_USER="${KINE_SERVICE_USER:-${SUDO_USER:-$USER}}"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
UVICORN_BIN="$PROJECT_DIR/.venv/bin/uvicorn"
UNIT_FILE="/etc/systemd/system/$SERVICE_NAME.service"
TEMP_UNIT="$(mktemp)"

cleanup() {
  rm -f "$TEMP_UNIT"
}
trap cleanup EXIT

cd "$PROJECT_DIR"

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

echo "Installation de l'autorisation Bluetooth..."
sudo KINE_SERVICE_USER="$SERVICE_USER" bash scripts/install_hci_sudoers.sh

cat >"$TEMP_UNIT" <<EOF
[Unit]
Description=Kine Capteurs
After=network.target

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

sudo install -o root -g root -m 0644 "$TEMP_UNIT" "$UNIT_FILE"
sudo systemctl daemon-reload

echo "Libération du contrôleur Bluetooth..."
sudo systemctl mask --now bluetooth.service
if command -v hciconfig >/dev/null 2>&1; then
  sudo hciconfig hci1 down
fi

echo "Démarrage de $SERVICE_NAME..."
sudo systemctl enable --now "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo
echo "Kine Capteurs est à jour."
echo "Interface: http://$(hostname -I | awk '{print $1}'):8000/"
echo "État: sudo systemctl status $SERVICE_NAME"
