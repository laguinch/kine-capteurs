"""Échange de commandes avec l'unique propriétaire du dongle Bluetooth."""

import json
import os
import uuid
from pathlib import Path

from app.config import BASE_DIR


RAW_DIR = BASE_DIR / "storage" / "raw_data"
CONTROL_PATH = RAW_DIR / "kinvent_bluetooth_control.json"
STATE_PATH = RAW_DIR / "kinvent_bluetooth_state.json"
RESET_PATH = RAW_DIR / "kinvent_bluetooth_reset.json"


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def manager_state():
    return _read_json(STATE_PATH)


def request_sensor(target):
    ensure_no_reset()
    if target not in {None, "kplates", "kpush", "kpull", "kmove", "anr_m40"}:
        raise ValueError(f"Capteur inconnu: {target}")
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
        except ProcessLookupError:
            return 1
        except PermissionError:
            # Le gestionnaire Bluetooth tourne en root tandis que l'API
            # tourne sous le compte du cabinet. EPERM confirme ici que le
            # processus existe, mais qu'il n'est pas signalable par l'API.
            pass
        except OSError:
            return 1
        if self.generation and state.get("generation") != self.generation:
            command = _read_json(CONTROL_PATH)
            if (
                command.get("generation") == self.generation
                and command.get("target") == self.target
            ):
                # La commande est encore dans la file du gestionnaire.
                return None
        if state.get("target") == self.target and state.get("phase") in {
            "switching",
            "active",
        }:
            return None
        return state.get("return_code", 0)


def reset_state():
    return _read_json(RESET_PATH)


def request_reset(idle_only=False):
    """Queue a maintenance request for the sole controller owner."""
    ensure_no_reset()
    state = manager_state()
    pid = state.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("Le gestionnaire Bluetooth est arrêté.")
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass  # The root-owned manager exists.
    except OSError as exc:
        raise RuntimeError("Le gestionnaire Bluetooth est arrêté.") from exc
    command = {
        "action": "reset",
        "target": None,
        "idle_only": bool(idle_only),
        "generation": uuid.uuid4().hex,
    }
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONTROL_PATH.with_name(f"{CONTROL_PATH.name}.{command['generation']}.tmp")
    temporary.write_text(json.dumps(command), encoding="utf-8")
    temporary.replace(CONTROL_PATH)
    return command


def ensure_no_reset():
    command = _read_json(CONTROL_PATH)
    if command.get("action") != "reset":
        return
    result = reset_state()
    if result.get("generation") != command.get("generation") or result.get("status") not in {"done", "error", "rejected", "skipped"}:
        raise RuntimeError("Une réinitialisation Bluetooth est en cours. Attendez sa fin.")
