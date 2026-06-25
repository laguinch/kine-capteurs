from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.routes.evaluations import router as evaluations_router
from api.routes.kplates import router as kplates_router
from api.routes.kmove import router as kmove_router
from api.routes.kpull import router as kpull_router
from api.routes.kpush import router as kpush_router
from api.routes.patients import router as patients_router
from app.config import BASE_DIR
from database.database import init_db

app = FastAPI(title="Kine Capteurs")
init_db()
app.include_router(kplates_router)
app.include_router(kpush_router)
app.include_router(kpull_router)
app.include_router(kmove_router)
app.include_router(patients_router)
app.include_router(evaluations_router)
app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "frontend" / "static"),
    name="static",
)


def frontend_page(filename):
    return FileResponse(
        Path(BASE_DIR) / "frontend" / "static" / filename,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/")
def home():
    return frontend_page("home.html")


@app.get("/nouvelle-seance")
def new_session():
    return frontend_page("new_session.html")


@app.get("/patients")
def patients():
    return frontend_page("patients.html")


@app.get("/seances")
def sessions():
    return frontend_page("sessions.html")


@app.get("/session/anonyme")
def anonymous_session():
    return frontend_page("session.html")


@app.get("/session/patient/{patient_id}")
def patient_session(patient_id: str):
    return frontend_page("session.html")


@app.get("/capteurs")
def sensors():
    return frontend_page("sensors.html")


@app.get("/kforceplates")
def kforceplates():
    return frontend_page("index.html")


@app.get("/kforceplates/jeux")
def kforceplates_games():
    return frontend_page("kplates_games.html")


@app.get("/kforceplates/jeux/voiture")
def kforceplates_car_game():
    return frontend_page("kplates_car_game.html")


@app.get("/kpush")
def kpush():
    return frontend_page("kpush.html")


@app.get("/kpush/test")
def kpush_test():
    return frontend_page("kpush_test.html")


@app.get("/kpull")
def kpull():
    return frontend_page("kpull.html")


@app.get("/kpull/test")
def kpull_test():
    return frontend_page("kpull_test.html")


@app.get("/kmove")
def kmove():
    return frontend_page("kmove.html")


@app.get("/kmove/test")
def kmove_test():
    return frontend_page("kmove_test.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "Kine Capteurs"}
