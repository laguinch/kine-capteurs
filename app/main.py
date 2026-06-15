from fastapi import FastAPI

app = FastAPI(title="Kine Capteurs")

@app.get("/")
def home():
    return {"status": "ok", "app": "Kine Capteurs"}
