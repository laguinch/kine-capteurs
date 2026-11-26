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

### Initialisation batterie et vidage conditionnels (correctif suivant)

Les temporisations fixes de 0,30 / 0,16 / 0,38 seconde sont remplacées par les
conditions retrouvées dans `BatteryLevelInstruction` (#34581–34587) et
`EmptyBufferInstruction` (#34353–34355, callback #75130) du même bundle :

- Après `76`, attendre la confirmation `F=<nombre>Hz`, puis 200 ms avant `11`.
- Fermer chaque paquet batterie de 11 octets au marqueur suivant et collecter
  cinq paquets complets avant le premier `10`.
- Envoyer le second `10` de l'instruction de vidage, puis vérifier toutes les
  80 ms que plus de 300 ms se sont écoulées depuis la dernière donnée UART.
- Envoyer seulement ensuite `60 00 19 00 4b 0d 0a`, puis poursuivre les réponses
  de contrôle déjà vérifiées. Aucune nouvelle commande ni relance n'est ajoutée.

La capture double confirme ces jalons : réponse de format → démarrage à
223/228 ms, et dernières données → changement de fréquence à 371/347 ms.
La fixture inclut maintenant les réponses et notifications batterie de cette
fenêtre. Les tests vérifient aussi la fragmentation, les confirmations 25/75 Hz,
le seuil de cinq paquets et l'interdiction de changer de fréquence tant que des
données continuent d'arriver. Les étapes restent indépendantes par plate.

La borne locale d'échec de la lecture batterie reste deux secondes, mesurées
à partir de `76`. Le repli de l'application vers une valeur batterie zéro en cas
d'échec n'est pas reproduit sans capture correspondante. Le vidage n'a pas de
nouvelle limite arbitraire : si les données continuent indéfiniment, il attend
et ne prétend pas que la plate est prête. Ce correctif ne garantit pas la
résolution des déconnexions radio antérieures à l'initialisation.

### Silence temporaire pendant une acquisition de répartition / un jeu

Vérification du bundle KINVENT 2.26.1 : `subscribeToDataStream` (#34227,
#34372) conserve un abonnement aux notifications, sans délai d'arrêt d'une
seconde dans ces fonctions. Le callback BLE `onDisconnected` (#75197) signale
une déconnexion anormale et appelle `reconnect`. `ConnectionThrottleMonitor`
(#34988–34995) exige un firmware V3 via `isSupported` : ce mécanisme ne concerne
pas le chemin V2 2.34. L'abonnement d'interface #93605 examiné journalise la
déconnexion anormale ; il ne démontre pas le comportement visuel de tous les
écrans de jeu. On ne peut pas affirmer que Kinvent suspend son chronomètre.

Notre règle terminale « une seconde sans notification → abandon du test » n'est
pas établie par ces fonctions officielles. Elle est retirée pour le mode
`balance` : les paires réellement reçues continuent d'être enregistrées jusqu'à
la fin prévue ou l'arrêt utilisateur. Une déconnexion BLE explicite reste une
erreur. La règle du mode CMJ n'est pas changée.

Choix explicite de notre interface (et non reproduction revendiquée de l'écran
Kinvent) : après une seconde sans nouvelle paire, les trois jeux suspendent les
mouvements et le score, affichent l'attente et reprennent à la nouvelle mesure.
Les réponses HTTP répétant le même horodatage ne réactivent pas le jeu. La durée
de session continue de s'écouler, sans remise à zéro à la reprise et sans
prolongation automatique. Aucune mesure manquante n'est interpolée ni écrite au
CSV. Cette tolérance ne corrige pas la cause des pertes de flux ou des défauts
USB. Aucune nouvelle commande Bluetooth ou relance n'est ajoutée.

Tests : `test_kplate_game_session.py` vérifie la politique du worker et
`node tests_library/test_kplate_game_freshness.cjs` vérifie les trois jeux
(doublons, interruption, reprise, conservation du score et fin de session).

### Fenêtre de recherche lors de la création de la seconde liaison

La capture double contient trois commandes LE Create Connection à
14:48:28.474288, 14:48:29.187828 et 14:48:29.545512 UTC. Elles commencent toutes
par les paramètres `60 00 30 00` : intervalle de recherche 0x0060 (60 ms),
fenêtre 0x0030 (30 ms). Le pilote envoyait `10 00 10 00` (10/10 ms).
Les deux paramètres de création de liaison sont désormais alignés sur la
capture, pour les deux plates ; le test de la commande HCI les vérifie.
Il ne s'agit pas de l'intervalle entre événements d'une liaison déjà établie.
Aucune nouvelle commande ni tentative supplémentaire n'est introduite.

Écarts encore ouverts : Android utilise une liste d'acceptation et le type
d'adresse locale 3 avec une configuration de confidentialité. Le pilote utilise
une adresse distante explicite et le type local public 0. L'adresse publique
nulle affichée sur le serveur mérite vérification directe, mais ne prouve pas
à elle seule la cause des échecs. Copier une adresse privée du téléphone ou le
type 3 sans les prérequis du contrôleur ne constituerait pas une reproduction
valide. La découverte GATT Android reste également plus complète. Cette
correction de fenêtre de recherche ne garantit pas la disparition de 0x3e.

### Compteurs de diagnostic du flux

Les acquisitions produisent une ligne JSON `DIAG_FLUX` toutes les cinq secondes,
puis un bilan final (également en cas d'arrêt ou d'exception). Les intervalles
comptent les deltas depuis le bilan précédent ; le bilan final couvre toute
l'acquisition. Les compteurs par plate distinguent notifications UART, octets,
mesures décodées, rejets du décodeur, écartements par synchronisation et pertes
par débordement de la file. Une notification peut contenir plusieurs mesures.
Le bilan inclut la taille des files, le fragment en attente, l'âge de la dernière
notification et le nombre de paires produites par seconde.

`retard_chronologie_ms` compare l'horloge hôte actuelle à la chronologie locale
reconstruite du capteur. Il peut révéler du retard ou une dérive de synchronisation,
mais ne mesure pas directement la latence radio et peut être négatif. Les rejets
du décodeur ne comptabilisent pas tous les octets abandonnés par le reconstitueur.
Aucune force, donnée patient ou commande Bluetooth supplémentaire n'est ajoutée
par ces diagnostics. Les règles d'appariement et les temporisations radio restent
inchangées. Ces compteurs servent à identifier la cause ; ils ne la corrigent pas.


### Chronologie des jeux V2 — comparaison avec Kinvent 2.26.1

Le pilote ne doit pas confondre une interruption de réception et une remise à
zéro du compteur. L'ancien seuil de 1 000 ms recalait la chronologie sur la
réception Bluetooth : une rafale tardive pouvait alors décaler durablement une
plateforme et provoquer des exclusions dans l'appariement.

Vérification du bytecode Hermes du XAPK (SHA-256
`a8d96bf27b34a8ee6e03a25c801b8cc99b046f55807c349f3a30192639c4a371`) :

- `PlateKforceDevice.getTimestampType`, fonction 35050 : le firmware 2.34
  utilise les millisecondes.
- `processMilliseconds`, 35052, et `shouldRolloverTimestamp`, 35053 : accumulation
  des écarts capteur ; pas de recalage sur la réception. Un recul est accepté
  comme débordement seulement si précédent − courant > 64 500, avec la constante
  officielle 65 535. Un recul différent ou un saut avant > 10 000 ms est une
  erreur, pas une nouvelle origine implicite.
- `subscribeToDataStream`, 34227, et `onSamplingStart`, 35054 : vidage du tampon,
  réinitialisation du précédent et du temps relatif à la nouvelle souscription.
  Le pilote vide donc les anciennes mesures et le fragment UART avant `11`,
  après la séquence existante `90` / attente de 700 ms. Les deux temps relatifs
  utilisent une origine locale commune ; l'arrivée tardive du premier paquet ne
  crée plus un décalage permanent. Les notifications pendant cette transition
  ne sont pas décodées comme des mesures de la nouvelle acquisition.

La capture double déjà disponible (`bugreport-plate-kinvent-D-G`, SHA-256 du
btsnoop `badaf4c7eb8496b52235b8cce01da811fc552c06fe3cde8be7adfd34a2ff2130`)
confirme les remises à zéro après `90` aux deux débuts d'acquisition et six
passages de compteur. Relecture locale : 6 697 trames droite et 6 696 gauche,
aucune erreur de chronologie. Les tests synthétiques couvrent aussi une arrivée
par rafales après un trou de deux secondes et des premiers paquets décalés.
Aucune commande radio, attente ou reconnexion n'est ajoutée.

Limites : ce correctif n'explique pas à lui seul une baisse des octets reçus.
Le coordinateur officiel des jeux (`DoubleDeviceRealTimeCoordinator`, 49012–49013)
possède également une interpolation des échantillons manquants. Elle n'est pas
reproduite ici : l'appariement actuel reste distinct et aucune mesure interpolée
n'est ajoutée aux exports. La fluidité réelle et la stabilité radio doivent être
vérifiées sur le serveur ; une relecture de capture ne les démontre pas.


### Erreur d'horodatage : exclusion du paquet, maintien de l'acquisition

La vérification de `processMilliseconds` seule était incomplète dans `f6094ff` :
son exception remontait jusqu'au gestionnaire et arrêtait le jeu. Le bytecode de
`rawDataPackageToForceSamples` (34217, branche de traitement à l'offset 0x17d)
intercepte cette exception précise, journalise le message et renvoie une liste
vide. Le module d'exception est bien le même (1940) : dépendance 10 du module
3953 pour `PlateKforceDevice`, dépendance 7 du module 3867 pour le convertisseur.
Les autres exceptions sont propagées.

Le pilote applique désormais cette distinction avec `PlateTimestampError` :
le paquet rejeté ne modifie ni dernière mesure, ni tare, ni file, ni origine de
l'horloge. Les trames valides suivantes d'une même notification restent traitées.
Les contrôles chronologiques précédents sont conservés, sans nouvelle commande,
reconnexion ou recalage temporel. `DIAG_FLUX.rejets_chronologie`, par plateforme,
compte séparément ces exclusions ; `mesures` ne compte que les mesures acceptées.
Les tests couvrent recul, grand saut avant, reprise à l'horodatage suivant,
plusieurs trames dans une notification et propagation des erreurs indépendantes.

Cette correction traite l'arrêt logiciel. Elle ne démontre pas que la cause des
retards et des horodatages désordonnés dans le flux reçu est résolue.


### Présence du pilote au repos

L'API considère les états `idle` / `degraded` périmés après 30 secondes.
La boucle de réception au repos rafraîchit désormais `updated_at` après chaque
passage réussi. Elle conserve la phase, les côtés connectés, les erreurs et les
résultats : ce signal indique seulement que le processus travaille, pas que les
mesures sont fraîches. Aucun état terminal ou fichier appartenant à un autre
processus n'est rafraîchi. Aucune commande Bluetooth n'est ajoutée.
Cette correction évite un faux affichage hors ligne après un jeu ; elle ne
corrige pas les retards du flux de mesures.


### Échec d'initialisation dans le service permanent

`PlateInitializationError` reste distinct des erreurs entraînant les tentatives
internes de `initialize_session`. Le service permanent l'intercepte maintenant
aux deux points d'entrée (connexion initiale et reconnexion demandée) pour
exécuter le nettoyage existant et enregistrer l'échec de cette génération.
Le processus reste disponible pour une nouvelle demande explicite. La même
commande ne redémarre pas l'initialisation et ne provoque plus de traceback suivi
d'une relance du processus par le gestionnaire.

Les réponses attendues, les commandes et les délais d'initialisation restent
inchangés. Recevoir des paquets batterie après `66` ne valide pas la fréquence ;
la cause d'une telle réponse reste à diagnostiquer. Le test de régression couvre
les deux points d'entrée, le nettoyage et l'absence de nouvelle tentative pour
la même génération.
