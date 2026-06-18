# Protocole Kinvent

Notes de rétro-ingénierie BLE.

Services observés :

- `49535343-fe7d-4ae5-8fa9-9fafd205e455`
- UART (notify + write sans réponse) : `49535343-1e4d-4bd9-ba61-23c647249616`
- notify secondaire : `49535343-4c8a-39b3-2f49-511cff073b7e`
- write alternatif observé : `49535343-8841-43f4-a8d4-ecbe34729bb3`

Pour les K-Force Plates testées sur macOS, activer les deux notifications puis
envoyer la séquence d'initialisation sur l'UUID UART déclenche le flux de données.

## Capture Android

La capture BTSnoop du 15 juin 2026 montre le service Kinvent aux handles
`0x0050` à `0x0058` :

- UART `...9616` : valeur `0x0052`, CCCD `0x0053`
- write alternatif `...9bb3` : valeur `0x0055`
- notify secondaire `...3b7e` : valeur `0x0057`, CCCD `0x0058`

Séquence observée :

1. écriture sans réponse `10` sur `0x0052`
2. activation des notifications (`01 00`) sur `0x0053`
3. écritures sans réponse sur `0x0052` :
   `10`, `09`, `21`, `76`, `11`, `10`, `10`, `56`,
   `ac 00 54 f8`, `11`

Avant la découverte GATT par Android, le capteur joue aussi le rôle de client ATT :
il demande un MTU de 158 puis recherche le service Kinvent sur le téléphone.
Android répond au MTU et renvoie `Attribute Not Found` à la recherche de service.
Dans la capture Linux, BlueZ ne répond pas à ces requêtes entrantes et interrompt
la connexion avant que les caractéristiques distantes soient disponibles.

## Contournement Linux

Le prototype `scripts/kinvent_raw_hci.py` réserve un contrôleur Bluetooth dédié
avec le canal noyau `HCI_USER`. Il ne passe ni par D-Bus ni par la couche GATT de
BlueZ. Il répond directement aux requêtes ATT initiées par le capteur, active le
CCCD `0x0053`, puis écrit la séquence sur la valeur `0x0052`.

Cette approche permet de conserver un autre contrôleur sous BlueZ pour l'ANRM40.
Le contrôleur Kinvent doit être hors tension dans BlueZ avant l'ouverture du
canal utilisateur et le script doit être exécuté avec les droits root.
