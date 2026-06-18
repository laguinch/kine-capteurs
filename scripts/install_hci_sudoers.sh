#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="${KINE_SERVICE_USER:-${SUDO_USER:-$USER}}"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
HCI_SCRIPT="$PROJECT_DIR/scripts/kinvent_dual_hci.py"
SUDOERS_FILE="/etc/sudoers.d/kine-capteurs-hci"
TEMP_FILE="$(mktemp)"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Environnement Python introuvable: $PYTHON_BIN" >&2
  exit 1
fi

cat >"$TEMP_FILE" <<EOF
$SERVICE_USER ALL=(root) NOPASSWD: $PYTHON_BIN -u $HCI_SCRIPT *
EOF

chmod 0440 "$TEMP_FILE"
visudo -cf "$TEMP_FILE"
install -o root -g root -m 0440 "$TEMP_FILE" "$SUDOERS_FILE"
rm -f "$TEMP_FILE"

echo "Autorisation HCI installée pour $SERVICE_USER."
echo "Fichier: $SUDOERS_FILE"
