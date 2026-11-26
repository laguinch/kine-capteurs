# Préparation du correctif USB Zephyr 4.4.0

État : patch source préparé. **Ni compilation cible ni essai matériel validés.**
Il n'est pas appliqué par `scripts/update.sh` et ne flashe aucun dongle.

## Origine et portée

Zephyr v4.4.0 utilise `bt_hci_tx_sync_in` : le même thread attend la fin de
chaque transfert USB, mesures ACL ou événements HCI. Le correctif officiel
[e554b42d](https://github.com/zephyrproject-rtos/zephyr/commit/e554b42dfb0d402950a08f147aee670c244ba4ae)
supprime cette sérialisation. Il n'est pas applicable tel quel à v4.4.0 car le
chemin OUT et les pools ont changé entre-temps.

`zephyr-4.4-usb-in.patch` en adapte uniquement la partie IN : pool distinct de
deux buffers (valeur par défaut amont), dimensionné pour un paquet HCI complet,
libération dans le callback de fin et suppression du sémaphore par transfert.
Le chemin OUT de 4.4.0 est conservé. Le buffer d'un type de paquet non supporté
est également libéré. Les commandes Kinvent, intervalles radio et règles
chronologiques ne sont pas modifiés.

Cela constitue une piste pour les retards et réponses HCI absentes, **pas une
preuve de leur cause sur le dongle utilisé**. Le tag du dossier de compilation
ne prouve pas à lui seul quel binaire est installé sur le matériel.

## Validation avant utilisation

Le manifeste contient les SHA-256 exacts du fichier d'origine et du résultat.
Application et annulation du patch ont été vérifiées sur le fichier officiel
v4.4.0. Il faut encore compiler pour la cible et avec les paramètres du build
serveur, dans un nouveau dossier, puis vérifier le binaire produit.

Ne pas copier le fichier de la branche `main` de Zephyr directement dans 4.4.0.
Ne pas flasher à partir de ce patch sans compilation cible réussie et procédure
de retour au firmware précédent. Conserver le build et les binaires existants.

Pour inspecter l'application du patch sans modifier le dépôt Zephyr :

```bash
git -C /home/yann/zephyrproject-4.4/zephyr apply --check /opt/kine-capteurs-staging/firmware/nrf52840_hci_usb/zephyr-4.4-usb-in.patch
```
