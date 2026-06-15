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
