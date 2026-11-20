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

## Initialisation double plate V2 2.34

Le pilote HCI double utilise désormais une progression indépendante par plate
pour les réponses de contrôle identifiées. La source est la capture existante
`bug_report/bugreport-plate-kinvent-D-G/FS/data/log/bt/btsnoop_hci.log`, fenêtre
du 15 juin 2026 de 14:48:30 à 14:48:34 UTC. Son SHA-256 est
`badaf4c7eb8496b52235b8cce01da811fc552c06fe3cde8be7adfd34a2ff2130`.

| Commande | Réponse nécessaire avant la suite |
| --- | --- |
| `09` | `KINVENT FW 2.34V` |
| `66` | `46 3d 32 35 48 7a 00 00` (`F=25Hz`) |
| `56` | `46 3d 32 35 48 7a 00 00` (`F=25Hz`) |
| `ac 00 54 f8` | `ac 00 54 aa 52` (rollover déjà activé) |
| `ac 01 04 a9` | `ac 01 04 cc 65` (variante de ces plates) |

Ces réponses doivent arriver sur la valeur UART `0x0052` de la bonne connexion.
Elles peuvent être fragmentées ; le tampon est vidé à chaque nouvelle instruction.
Une plate qui répond n'attend pas la réponse de l'autre. Par exemple, dans la
capture la réponse à `66` arrive après 1,656612 s à gauche et 1,654078 s à droite :
ce sont des latences constatées, pas des temporisations fixes de 1,67 s à rejouer.

Le recoupement provient du bundle Hermes de KINVENT 2.26.1 (versionCode 135),
XAPK SHA-256 `a8d96bf27b34a8ee6e03a25c801b8cc99b046f55807c349f3a30192639c4a371`.
Fonctions identifiées : `startNextInstruction` #34224,
`onInstructionCompleted` #34226, `ReadFirmwareInstruction` #34608/#34609,
`ChangeFrequencyInstruction` #34573/#34574, `MeasurementWithTimeProtocolInstruction`
#34565/#34566, `RolloverTimerInstruction` #34631/#34632 et
`ReadPlateVariantInstruction` #34641. Les numéros sont propres à ce bundle.
La décompilation Hermes 98 est partielle : les réponses ci-dessus sont également
confirmées dans la capture, et non déduites du seul pseudo-code.

Le délai de deux secondes des instructions de lecture/fréquence du bundle sert
de borne locale d'échec pour ces attentes (y compris la lecture du rollover).
Il ne retarde pas une réponse déjà reçue et ne déclenche aucune commande,
reconnexion ou réinitialisation automatique. Une réponse absente ou différente
arrête cette initialisation avec un diagnostic. Les autres firmwares, variantes
EEPROM et le rollover désactivé ne sont pas implémentés par extrapolation.

La séquence de commandes, le préambule CCCD/radio et les délais des étapes sans
réponse validée, notamment autour de la batterie (`76`), sont conservés.
Ce correctif ne reconstitue donc pas encore toute la logique de l'application.

`tests_library/fixtures/kplates_init_234.json` contient uniquement les commandes,
réponses de contrôle et horaires extraits de cette capture. Les tests rejouent
leurs latences par instruction et vérifient la séparation gauche/droite, les
réponses fragmentées, les réponses incorrectes et l'arrêt sans relance :

```sh
python3 -m unittest tests_library.test_kplate_initialization tests_library.test_kplate_dual tests_library.test_kplate_protocol
```

Une validation sur les plates du serveur reste nécessaire. Aucune nouvelle
capture Bluetooth n'est requise pour exécuter ces tests hors ligne.

### Conservation des événements de connexion et de réglage radio

Le pilote conserve désormais les événements LE Connection Complete (classique
et étendu) et Connection Update Complete reçus pendant une autre attente HCI.
Auparavant, `wait_for_command` les transmettait à `process`, qui les ignorait :
une réponse déjà reçue pouvait donc aboutir à « Réglage radio sans réponse ».
Les résultats sont consommés par la liaison concernée et effacés entre sessions.
Un échec de connexion est également reconnu lorsque son adresse distante n'est
pas renseignée. Le handle zéro reste un identifiant de connexion valide.

Dans la capture double du 15 juin, à 14:48:29.739334 UTC, le handle `0x000c`
reçoit `3e 0a 03 00 0c 00 00 00 00 00 00 00`, puis un résultat valide à
14:48:30.052428. Le premier résultat ne doit pas valider le réglage : le pilote
vérifie désormais l'intervalle demandé, la latence et le délai de supervision.
Aucune commande, temporisation ou tentative supplémentaire n'est ajoutée.

Pendant l'initialisation, les notifications UART sont traitées par son automate
et ne sont plus décodées comme des mesures du test. Cela évite de compter les
réponses de firmware et de fréquence comme des trames de mesure rejetées.
Le décodage des mesures reprend une fois l'initialisation terminée.

`tests_library/test_kplate_hci_events.py` couvre le résultat nul capturé,
les réponses arrivant avant Command Status (entrelacement simulé), la séparation
des deux liaisons, les erreurs, le handle zéro et l'isolation des mesures.
Ces tests ne démontrent pas la cause de toutes les coupures physiques signalées
par `0x08` ou `0x3e`, ni de l'absence de réponse au reset `0x0c03`.
La découverte GATT complète et la nouvelle logique de collecte batterie ne
font pas partie de ce correctif ; leur analyse reste à valider séparément.

### Mesures groupées et fragmentées dans les notifications

Une notification UART n'est pas nécessairement une mesure. Le pilote traitait
chaque valeur ATT comme un paquet unique de 17 octets, rejetant les notifications
contenant plusieurs mesures ou seulement une partie de mesure. Il reconstitue
maintenant un flux indépendant pour chaque plate avant le décodage et remet
chaque mesure complète à la synchronisation gauche/droite.

Le cadrage suit `ForceDataBuffer` du bundle Hermes (#34382/#34383) : le marqueur
`ff ff fe` suivant ferme le paquet précédent, dont la taille doit être 17 pour
ce format de mesure. Un paquet incomplet reste en attente ; une taille incorrecte
est abandonnée au prochain marqueur. Le tampon est borné et vidé à la connexion,
à la déconnexion et au nettoyage de session. Les horodatages du capteur restent
utilisés pour espacer les mesures, même lorsque leur réception est groupée.
Cela implique l'attente du marqueur suivant (normalement une période de mesure),
comme dans le parseur officiel, sans ajout de commande ni de temporisation radio.

Vérification hors ligne sur la capture double du 15 juin : 6 697 mesures à droite
et 6 696 à gauche selon ce cadrage. Les résultats sont identiques avec les
notifications originales et avec le même flux redécoupé par blocs de 1, 17, 34
ou 85 octets. Les paquets bordant une réponse de contrôle et le dernier paquet
sans marqueur suivant ne constituent pas des paquets complets selon ce cadrage.
Les tests automatisés utilisent des valeurs synthétiques et couvrent tous les
blocs de 1 à 85 octets, la séparation des plates et la remise à zéro du tampon.
Ce changement ne résout pas à lui seul les pertes de liaison radio.
