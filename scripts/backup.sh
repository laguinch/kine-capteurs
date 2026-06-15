#!/usr/bin/env bash
set -e
mkdir -p backups
cp storage/kine.db "backups/kine-$(date +%Y%m%d-%H%M%S).db"
