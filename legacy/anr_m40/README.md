# ANR M40 legacy BlueZ

Ce dossier contient le module autonome récupéré depuis `/home/yann/m40` sur le
serveur. C'est la version qui fonctionnait avec l'ANR M40 via Bleak/BlueZ et
une page web indépendante sur le port 8090.

Elle sert de référence de secours et de comparaison avec l'intégration actuelle
du projet.

## Lancer ce module sur le serveur

Arrêter d'abord les services du projet principal, puis réactiver BlueZ :

```bash
sudo systemctl stop kine-capteurs
sudo systemctl stop kine-capteurs-bluetooth
sudo systemctl unmask bluetooth.service 2>/dev/null || true
sudo systemctl enable --now bluetooth.service
```

Puis lancer le module :

```bash
cd /opt/kine-capteurs-staging
source .venv/bin/activate
python legacy/anr_m40/web_m40.py
```

La page est disponible sur :

```text
http://10.0.0.28:8090
```

## Important

Ce module utilise BlueZ. Il ne doit pas tourner en même temps que le gestionnaire
Bluetooth principal du projet, qui garde la main sur le dongle pour les capteurs
Kinvent.
