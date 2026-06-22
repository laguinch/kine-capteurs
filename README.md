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

Le service Bluetooth démarre avec le serveur en mode déconnecté. Le bouton
« Connecter les capteurs » ouvre les liaisons au début d'une séance et les
conserve entre les tests. Démarrer ou arrêter un test ne coupe donc plus les
plateformes : seule la création du fichier CSV est pilotée par l'interface.
Le bouton « Déconnecter les capteurs » ferme les liaisons en fin de séance pour
préserver les batteries, sans arrêter le service système.

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

## Interfaces web

- K-Force Plates : `/kforceplates`
- K-Push : `/kpush`
- K-Pull : `/kpull`
- K-Move : `/kmove`

Le K-Pull utilise le coefficient validé avec une charge étalon de 12 kg :
`9722.166667 comptes/kg`. Le câble doit rester complètement détendu pendant
la tare réalisée à chaque connexion.

## Diagnostic K-Move

Le K-Move transmet un quaternion, les accélérations et la batterie à 75 Hz.
Le diagnostic fixe les trois rotations à zéro pendant les deux premières
secondes, puis affiche les axes X, Y et Z en degrés :

```bash
sudo scripts/run_kmove_diagnostic.sh \
  --duration 30 \
  --csv storage/raw_data/kmove_diagnostic.csv
```

Maintenir le K-Move immobile pendant la prise de référence, puis effectuer
successivement un mouvement autour de chacun de ses trois axes.

## Premier test K-Push

Le K-Push détecté dans la capture officielle est `KFORCEMuscle03578`
(`60:8A:10:30:9B:FA`). Pour le premier essai, le pilote reste volontairement
séparé du service permanent des plateformes.

Le contrôleur HCI ne peut être utilisé que par un pilote à la fois :

```bash
cd /opt/kine-capteurs-staging
sudo systemctl stop kine-capteurs-bluetooth
sudo hciconfig hci0 down
sudo .venv/bin/python scripts/kinvent_kpush_hci.py \
  --adapter hci0 \
  --duration 30 \
  --tare-duration 2 \
  --csv storage/raw_data/kpush_test.csv
```

Pendant les deux premières secondes, ne pas exercer de pression sur le
K-Push. Pour remettre ensuite les plateformes en service :

```bash
sudo systemctl restart kine-capteurs-bluetooth
```

## Diagnostic ANR M40

Le M40 utilise le profil GATT standard. Le script suivant bascule
temporairement le dongle USB vers BlueZ, recherche le Company ID ANR `0x05DA`,
lit l'identité et la batterie, règle la couleur d'identification et enregistre
les notifications EMG à 10 Hz :

```bash
cd /opt/kine-capteurs-staging
sudo scripts/run_anr_m40_diagnostic.sh \
  --duration 30 \
  --device-id 1 \
  --csv storage/raw_data/anr_m40_test.csv
```

Le service permanent des plateformes est restauré automatiquement à la fin.

## Diagnostic K-Pull

Le K-Pull capturé est `KFORCELink02287`
(`E8:EB:1B:61:11:AF`). Le premier diagnostic conserve les comptes bruts afin de
calculer précisément l'échelle avec une charge connue :

```bash
cd /opt/kine-capteurs-staging
sudo scripts/run_kpull_diagnostic.sh \
  --duration 30 \
  --known-load-kg 20 \
  --csv storage/raw_data/kpull_calibration.csv
```

Laisser le câble sans tension pendant les deux premières secondes, puis
appliquer ou suspendre exactement la charge indiquée. Le script affiche alors
le coefficient en comptes/kg.
