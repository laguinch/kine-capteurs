# Instructions obligatoires du projet

## Bluetooth Kinvent

Pour toute intervention sur les K-Force Plates, K-Push, K-Pull, K-Move ou un
autre capteur Kinvent :

1. Consulter d'abord les bug-reports et captures HCI présents dans
   `bug_report/`.
2. Reproduire strictement le comportement observé dans l'application
   officielle Kinvent.
3. Ne jamais ajouter une commande, un délai, une reconnexion, une relance ou
   une récupération qui n'apparaît pas dans les captures.
4. Si les captures ne permettent pas de déterminer un comportement, arrêter
   l'implémentation et le signaler clairement à l'utilisateur.
5. Utiliser un gestionnaire Bluetooth unique pour conserver la propriété du
   dongle et changer de capteur sans réinitialisation intermédiaire.
6. Ajouter ou maintenir des tests qui verrouillent les séquences officielles.

Ces règles sont prioritaires pour toute modification Bluetooth.

## Données patients

Ne jamais ajouter au dépôt la base réelle, les exports, les journaux du cabinet
ou toute donnée permettant d'identifier un patient.
