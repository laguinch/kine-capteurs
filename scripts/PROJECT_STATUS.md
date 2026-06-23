# Projet kine-capteurs

## Dépôt GitHub

https://github.com/laguinch/kine-capteurs

## Capteurs Kinvent

### K-Plate

- Gauche : E8:EB:1B:6F:A7:5F
- Droite : E8:EB:1B:79:B1:AB

### K-Push

- Test BLE validé
- Décodage force en kg et N validé

### K-Pull

- Non testé à ce jour

### K-Move

- Structure créée
- Protocole à analyser

## Architecture

ble/
├── common/
│ ├── bluetooth.py
│ ├── devices.py
│ ├── scanner.py
│ └── utils.py
│
└── kinvent/
├── kplates/
├── kpush/
├── kpull/
└── kmove/

## Serveur

Ubuntu

Répertoire :

/opt/kine-capteurs-staging

## État actuel

### Validé

- GitHub opérationnel
- VS Code relié à GitHub
- Déploiement serveur possible par git pull
- Clé Bluetooth CSR fonctionnelle
- Bleak installé
- Scan BLE fonctionnel
- KFORCEPlateL04357 détectée
- KFORCEPlateR04356 détectée

### Problème actuel

Erreur :


## Prochaine étape



## Objectif final

Plateforme d'évaluation kinésithérapique :

- Gestion patients
- Bibliothèque de tests
- K-Plate
- K-Push
- K-Pull
- K-Move
- Graphiques temps/force
- Analyse asymétrie droite/gauche
- Temps de décollage
- Temps d'atterrissage
- Génération de rapports