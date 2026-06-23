"""Échange de commandes avec l'unique propriétaire du dongle Bluetooth."""

import json
import os
import uuid
from pathlib import Path

from app.config import BASE_DIR


RAW_DIR = BASE_DIR / "storage" / "raw_data"
CONTROL_PATH = RAW_DIR / "kinvent_bluetooth_control.json"
STATE_PATH = RAW_DIR / "kinvent_bluetooth_state.json"


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def manager_state():
    return _read_json(STATE_PATH)


def request_sensor(target):
    if target not in {None, "kplates", "kpush", "kpull", "kmove"}:
        raise ValueError(f"Capteur Kinvent inconnu: {target}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    command = {
        "action": "select" if target else "disconnect",
        "target": target,
        "generation": uuid.uuid4().hex,
    }
    temporary = CONTROL_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(command), encoding="utf-8")
    temporary.replace(CONTROL_PATH)
    return command


class ManagedSensorProcess:
    """Vue compatible Popen sur le pilote enfant du gestionnaire."""

    def __init__(self, target, generation=None):
        self.target = target
        self.generation = generation

    @property
    def pid(self):
        state = manager_state()
        return state.get("child_pid") or state.get("pid")

    def poll(self):
        state = manager_state()
        manager_pid = state.get("pid")
        if not isinstance(manager_pid, int):
            return 1
        try:
            os.kill(manager_pid, 0)
        except (OSError, ProcessLookupError):
            return 1
        if self.generation and state.get("generation") != self.generation:
            # La commande est encore dans la file du gestionnaire.
            return None
        if state.get("target") == self.target and state.get("phase") in {
            "switching",
            "active",
        }:
            return None
        return state.get("return_code", 0)
