from fastapi import FastAPI

from api.routes.kplates import router as kplates_router

app = FastAPI(title="Kine Capteurs")
app.include_router(kplates_router)

@app.get("/")
def home():
    return {"status": "ok", "app": "Kine Capteurs"}
