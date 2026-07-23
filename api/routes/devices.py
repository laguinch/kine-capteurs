from fastapi import APIRouter

from ble.common.devices import KMOVE, KPLATE_LEFT, KPLATE_RIGHT, KPULL, KPUSH
from ble.kinvent.bluetooth_manager import manager_state, request_sensor
from ble.kinvent.kmove.acquisition_service import kmove_service
from ble.kinvent.kplates.acquisition_service import dual_plate_service
from ble.kinvent.kpull.acquisition_service import kpull_service
from ble.kinvent.kpush.acquisition_service import kpush_service


router = APIRouter(prefix="/api/devices", tags=["Appareils"])


def _phase_label(phase):
    return {
        "active": "en test",
        "armed": "prêt pour test",
        "connecting": "connexion",
        "degraded": "connexion partielle",
        "disconnected": "non connecté",
        "error": "erreur",
        "idle": "connecté",
        "offline": "non connecté",
        "ready": "connecté",
        "reference": "référence",
        "switching": "changement",
        "tare": "tare",
    }.get(phase or "offline", phase or "non connecté")


def _device(
    key,
    name,
    kind,
    addresses,
    status,
    connected,
    phase,
    target,
    open_path,
):
    return {
        "key": key,
        "name": name,
        "kind": kind,
        "addresses": addresses,
        "connected": bool(connected),
        "active": target == key,
        "phase": phase,
        "phase_label": _phase_label(phase),
        "running": bool(status.get("running")),
        "last_error": status.get("last_error"),
        "open_path": open_path,
        "connected_sides": status.get("connected_sides", []),
    }


def devices_snapshot():
    manager = manager_state()
    target = manager.get("target")
    kplates = dual_plate_service.status()
    kpush = kpush_service.status()
    kpull = kpull_service.status()
    kmove = kmove_service.status()

    return {
        "manager": {
            "phase": manager.get("phase", "offline"),
            "phase_label": _phase_label(manager.get("phase")),
            "target": target,
            "backend": manager.get("backend"),
            "transport": manager.get("transport"),
            "kplates_backend": manager.get("kplates_backend"),
            "hci_adapter": manager.get("hci_adapter"),
            "error": manager.get("error"),
        },
        "devices": [
            _device(
                "kplates",
                "K‑Force Plates",
                "Plateformes",
                [
                    {"side": "gauche", "address": KPLATE_LEFT},
                    {"side": "droite", "address": KPLATE_RIGHT},
                ],
                kplates,
                kplates.get("bluetooth_connected"),
                kplates.get("worker_phase"),
                target,
                "/kforceplates",
            ),
            _device(
                "kpush",
                "K‑Push",
                "Pression",
                [{"address": KPUSH}],
                kpush,
                kpush.get("connected"),
                kpush.get("phase"),
                target,
                "/kpush",
            ),
            _device(
                "kpull",
                "K‑Pull",
                "Traction",
                [{"address": KPULL}],
                kpull,
                kpull.get("connected"),
                kpull.get("phase"),
                target,
                "/kpull",
            ),
            _device(
                "kmove",
                "K‑Move",
                "Amplitude",
                [{"address": KMOVE}],
                kmove,
                kmove.get("connected"),
                kmove.get("phase"),
                target,
                "/kmove",
            ),
        ],
    }


@router.get("")
def list_devices():
    return devices_snapshot()


@router.post("/disconnect")
def disconnect_current_device():
    request_sensor(None)
    return devices_snapshot()
