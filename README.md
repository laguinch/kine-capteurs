# Kine Capteurs

Projet local pour capteurs Kinvent / ANR, patients, évaluations, exercices, graphiques et rapports PDF.

## Important

Les données patients ne doivent jamais être envoyées sur GitHub.
La base réelle et les exports restent uniquement sur le serveur du cabinet.

## Règle impérative pour le Bluetooth Kinvent

Toute modification concernant un capteur Kinvent doit reproduire strictement
le fonctionnement observé dans l'application officielle Kinvent et dans les
captures Bluetooth présentes dans `bug_report/`.

- consulter les bug-reports et les captures HCI avant de modifier un pilote ;
- utiliser uniquement les commandes, délais et transitions effectivement
  observés ;
- ne pas inventer de commande, de temporisation, de reconnexion, de relance
  ciblée ou de mécanisme de récupération ;
- si une information n'apparaît pas dans les captures, l'indiquer explicitement
  avant toute implémentation ;
- conserver un gestionnaire Bluetooth unique, propriétaire du dongle, afin de
  changer de capteur sans réinitialiser le contrôleur, comme dans l'application
  officielle ;
- protéger cette règle par des tests décrivant les séquences officielles.

Cette règle est prioritaire sur toute optimisation ou correction empirique.

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

### Mode CMJ

Le mode CMJ de la page `/kforceplates` conserve chaque mesure brute gauche et
droite sans rejeter les échantillons non appariés. Les deux flux natifs
d'environ 75 Hz sont ensuite interpolés sur une chronologie commune à 100 Hz
pour détecter le décollage, le temps de vol et l'atterrissage.

Au démarrage du test, rester debout et immobile pendant une seconde avant de
réaliser le saut.

## API K-Force Plates

- `POST /api/kplates/dual/start`
- `GET /api/kplates/dual/status`
- `GET /api/kplates/dual/latest`
- `POST /api/kplates/dual/stop`
- `GET /api/kplates/dual/download`

## Connexion Bluetooth permanente

Le serveur installe deux services distincts :

- `kine-capteurs.service` pour l'interface et l'API ;
- `kine-capteurs-bluetooth.service` pour le gestionnaire Bluetooth Kinvent
  unique.

Ce gestionnaire ouvre le dongle une seule fois et reste son unique
propriétaire. Il effectue l'unique réinitialisation HCI au démarrage du
service. Les pilotes K-Force Plates, K-Push, K-Pull et K-Move reçoivent ensuite
ce canal déjà ouvert. Changer de capteur déconnecte proprement le capteur
actuel, puis connecte le suivant sans redémarrer ni réinitialiser le dongle.

Le bouton « Déconnecter » ferme uniquement la liaison avec le capteur actif
afin de préserver sa batterie. Le gestionnaire système et le contrôleur
Bluetooth restent disponibles pour sélectionner immédiatement un autre
capteur.

Le processus HCI nécessite les droits d'accès au contrôleur Bluetooth brut.
La variable `KINE_HCI_COMMAND_PREFIX` permet de définir un préfixe de lancement
fourni par le service système, par exemple `sudo -n` après configuration dédiée.

### Backend Bumble expérimental

Le projet prépare une migration vers Bumble pour remplacer progressivement la
plomberie HCI maison. Bumble ne change pas le protocole Kinvent : les commandes
envoyées aux capteurs restent strictement celles observées dans les captures
officielles du dossier `bug_report/`.

La production reste sur le backend validé `raw-hci` tant que le nouveau dongle
n'a pas été testé. Le diagnostic Bumble permet de valider un contrôleur HCI,
par exemple le nRF52840 Dongle flashé en firmware HCI USB :

```bash
cd /opt/kine-capteurs-staging
source .venv/bin/activate
python scripts/kinvent_bumble_probe.py \
  --transport usb:0 \
  --address "ADRESSE_DU_CAPTEUR" \
  --profile force \
  --duration 30 \
  --csv storage/raw_data/bumble_probe.csv
```

Pour le K-Move, utiliser `--profile kmove`. Pour une découverte GATT sans
commande Kinvent, utiliser `--profile none`.

Après validation du diagnostic, le premier pilote Bumble complet disponible
est le K-Push :

```bash
cd /opt/kine-capteurs-staging
sudo .venv/bin/python -u scripts/kinvent_kpush_bumble.py \
  --transport usb:0 \
  --address "60:8A:10:30:9B:FA" \
  --duration 30 \
  --tare-duration 2 \
  --csv storage/raw_data/kpush_bumble_test.csv
```

Le K-Pull dispose du même pilote Bumble expérimental. Avec le coefficient
provisoire issu du test à 12 kg :

```bash
cd /opt/kine-capteurs-staging
sudo .venv/bin/python -u scripts/kinvent_kpull_bumble.py \
  --transport usb:0 \
  --address "E8:EB:1B:61:11:AF" \
  --duration 30 \
  --tare-duration 2 \
  --counts-per-kg 9722.166667 \
  --csv storage/raw_data/kpull_bumble_test.csv
```

Le K-Move peut ensuite être validé de la même façon. Pendant la référence,
maintenir le capteur immobile :

```bash
cd /opt/kine-capteurs-staging
sudo .venv/bin/python -u scripts/kinvent_kmove_bumble.py \
  --transport usb:0 \
  --address "60:8A:10:4F:BD:12" \
  --duration 30 \
  --reference-duration 2 \
  --csv storage/raw_data/kmove_bumble_test.csv
```

Les K-Force Plates peuvent être validées en double connexion Bumble avec la
même séquence officielle :

```bash
cd /opt/kine-capteurs-staging
sudo .venv/bin/python -u scripts/kinvent_kplates_bumble.py \
  --transport usb:0 \
  --duration 30 \
  --tare-duration 2 \
  --calibration-file storage/raw_data/kplates_calibration.json \
  --csv storage/raw_data/kplates_bumble_test.csv
```

Les variables prévues pour la suite sont :

- `KINE_BLUETOOTH_BACKEND=raw-hci` par défaut ;
- `KINE_BLUETOOTH_BACKEND=bumble` uniquement pour les essais préparatoires ;
- `KINE_BUMBLE_TRANSPORT=usb:0` pour le transport Bumble du dongle.

Le gestionnaire permanent refusera volontairement de basculer en Bumble tant
que les pilotes capteur n'auront pas été migrés et validés capteur par capteur.
Ce garde-fou évite d'introduire un comportement non observé dans l'application
officielle Kinvent.

## Cycle Bluetooth Kinvent

Les pilotes reproduisent le cycle observé dans les captures HCI de
l'application officielle :

- la connexion BLE et les notifications restent configurées tant que
  l'utilisateur ne clique pas sur « Déconnecter » ;
- après connexion, le flux de mesure reste disponible pour les écrans de
  préparation et de répartition d'appui, tandis que le maintien de liaison
  `0xFF` est envoyé toutes les dix secondes ;
- au démarrage d'un test, le flux est relancé sans recréer la connexion ;
- les plateformes ne sont mises au repos avec `0x10` qu'après un test, pas
  juste après la connexion ;
- à la fin du test, trois commandes `0x10` remettent le capteur au repos sans
  fermer la liaison Bluetooth.

Pour les K-Force Plates, la relance observée est `0x90`, une attente d'environ
700 ms, puis `0x11` sur chaque plateforme. Pour le K-Push, le K-Pull et le
K-Move, la relance du test utilise `0x11`.

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
