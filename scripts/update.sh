#!/usr/bin/env bash
set -e
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart kine-capteurs || true
