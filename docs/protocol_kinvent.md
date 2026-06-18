# Protocole Kinvent

Notes de rétro-ingénierie BLE.

Services observés :

- `49535343-fe7d-4ae5-8fa9-9fafd205e455`
- UART (notify + write sans réponse) : `49535343-1e4d-4bd9-ba61-23c647249616`
- notify secondaire : `49535343-4c8a-39b3-2f49-511cff073b7e`
- write alternatif observé : `49535343-8841-43f4-a8d4-ecbe34729bb3`

Pour les K-Force Plates testées sur macOS, activer les deux notifications puis
envoyer la séquence d'initialisation sur l'UUID UART déclenche le flux de données.
