#!/usr/bin/env bash
set -euo pipefail

SUDOERS_FILE="/etc/sudoers.d/kine-capteurs-hci"

if [[ -f "$SUDOERS_FILE" ]]; then
  rm -f "$SUDOERS_FILE"
  echo "Ancienne autorisation HCI supprimée: $SUDOERS_FILE"
else
  echo "Aucune autorisation HCI ancienne à supprimer."
fi

echo "Le service Bluetooth utilise uniquement Bumble/nRF52840."
