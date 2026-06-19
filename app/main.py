from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes.kplates import router as kplates_router
from api.routes.kpush import router as kpush_router
from app.config import BASE_DIR

app = FastAPI(title="Kine Capteurs")
app.include_router(kplates_router)
app.include_router(kpush_router)
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "frontend" / "static"),
    name="static",
)

@app.get("/")
def home():
    return FileResponse(Path(BASE_DIR) / "frontend" / "static" / "index.html")


@app.get("/kpush")
def kpush():
    return FileResponse(Path(BASE_DIR) / "frontend" / "static" / "kpush.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Kine Capteurs"}
