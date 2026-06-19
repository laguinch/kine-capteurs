# Kine Capteurs

Projet local pour capteurs Kinvent / ANR, patients, évaluations, exercices, graphiques et rapports PDF.

## Important

Les données patients ne doivent jamais être envoyées sur GitHub.
La base réelle et les exports restent uniquement sur le serveur du cabinet.

## Lancement local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Diagnostic Bluetooth

Sur le serveur, depuis le dossier du projet avec l'environnement Python actif :

```bash
python scripts/ble_diagnostic.py --scan
```

Pour lister les services d'un capteur :

```bash
python scripts/ble_diagnostic.py --address "ADRESSE_DU_CAPTEUR" --services
```

Pour ecouter une caracteristique en direct et enregistrer les donnees brutes :

```bash
python scripts/ble_diagnostic.py \
  --address "ADRESSE_DU_CAPTEUR" \
  --notify "UUID_DE_LA_CHARACTERISTIQUE" \
  --duration 30 \
  --csv storage/raw_data/ble_capture.csv
```

## Double plateforme sans BlueZ

Le pilote HCI direct synchronise les mesures gauche/droite avec une tolérance
temporelle configurable :

```bash
sudo .venv/bin/python scripts/kinvent_dual_hci.py \
  --adapter hci1 \
  --duration 30 \
  --tare-duration 2 \
  --sync-tolerance-ms 20 \
  --csv storage/raw_data/kplates_dual_synced.csv
```

Le CSV contient une ligne par paire synchronisée, l'écart temporel, le poids de
chaque plateforme, l'asymétrie et le centre de pression global.

## API K-Force Plates

- `POST /api/kplates/dual/start`
- `GET /api/kplates/dual/status`
- `GET /api/kplates/dual/latest`
- `POST /api/kplates/dual/stop`
- `GET /api/kplates/dual/download`

## Connexion Bluetooth permanente

Le serveur installe deux services distincts :

- `kine-capteurs.service` pour l'interface et l'API ;
- `kine-capteurs-bluetooth.service` pour la connexion permanente aux capteurs.

Le service Bluetooth se connecte aux deux plateformes au démarrage du serveur
et conserve les flux actifs entre les tests. Démarrer ou arrêter un test ne
coupe donc plus les plateformes : seule la création du fichier CSV est pilotée
par l'interface. Les boutons « Connecter les capteurs » et « Déconnecter les
capteurs » permettent de couper les liaisons hors séance pour préserver les
batteries, sans arrêter le service système.

Le processus HCI nécessite les droits d'accès au contrôleur Bluetooth brut.
La variable `KINE_HCI_COMMAND_PREFIX` permet de définir un préfixe de lancement
fourni par le service système, par exemple `sudo -n` après configuration dédiée.

## Installation et mise à jour du serveur

Après le premier clonage du dépôt dans `/opt/kine-capteurs-staging`, une seule
commande installe les dépendances, crée la configuration locale si nécessaire,
vérifie les tests, configure les droits HCI et démarre le service :

```bash
cd /opt/kine-capteurs-staging
bash scripts/update.sh
```

La même commande est utilisée pour toutes les mises à jour suivantes. Les
données présentes dans `storage/` et le fichier `.env` sont ignorés par Git et
ne sont ni remplacés ni envoyés sur GitHub.

L'interface est ensuite disponible sur `http://ADRESSE_DU_SERVEUR:8000/`.
