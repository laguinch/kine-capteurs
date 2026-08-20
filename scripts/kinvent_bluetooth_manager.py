"""Gestionnaire unique du dongle Bluetooth Kinvent."""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import BASE_DIR  # noqa: E402
from ble.kinvent.bluetooth_manager import (  # noqa: E402
    CONTROL_PATH,
    STATE_PATH,
)
from ble.kinvent.bumble_backend import (  # noqa: E402
    BUMBLE_BACKEND,
    backend_from_environment,
    bumble_config_from_environment,
    manager_backend_notice,
    normalize_backend,
    require_bumble,
)


RAW_DIR = BASE_DIR / "storage" / "raw_data"
WORKER_IDLE_STALE_SECONDS = 30.0

TARGETS = {
    "kplates": {
        "script": "kinvent_kplates_bumble.py",
        "control": "kplates_worker_control.json",
        "state": "kplates_worker_state.json",
        "log": "kplates_worker.log",
        "args": [
            "--tare-duration", "2",
            "--calibration-file", str(RAW_DIR / "kplates_calibration.json"),
            "--sync-tolerance-ms", "20",
            "--control-file", str(RAW_DIR / "kplates_worker_control.json"),
            "--state-file", str(RAW_DIR / "kplates_worker_state.json"),
        ],
    },
    "kpush": {
        "script": "kinvent_kpush_hci.py",
        "control": "kpush_worker_control.json",
        "log": "kpush_worker.log",
        "args": [
            "--duration", "0",
            "--tare-duration", "2",
            "--control-file", str(RAW_DIR / "kpush_worker_control.json"),
            "--csv", str(RAW_DIR / "kpush_live.csv"),
            "--connect-attempts", "2",
        ],
    },
    "kpull": {
        "script": "kinvent_kpull_hci.py",
        "control": "kpull_worker_control.json",
        "log": "kpull_worker.log",
        "args": [
            "--duration", "0",
            "--tare-duration", "2",
            "--counts-per-kg", "9722.166667",
            "--control-file", str(RAW_DIR / "kpull_worker_control.json"),
            "--csv", str(RAW_DIR / "kpull_live.csv"),
            "--connect-attempts", "2",
        ],
    },
    "kmove": {
        "script": "kinvent_kmove_bumble.py",
        "control": "kmove_worker_control.json",
        "log": "kmove_worker.log",
        "args": [
            "--duration", "0",
            "--reference-duration", "2",
            "--control-file", str(RAW_DIR / "kmove_worker_control.json"),
            "--csv", str(RAW_DIR / "kmove_live.csv"),
        ],
    },
    "anr_m40": {
        "script": "anr_m40_raw_hci.py",
        "control": "anr_m40_worker_control.json",
        "log": "anr_m40_worker.log",
        "args": [
            "--address", "68:23:B0:B6:AF:F3",
            "--duration", "0",
            "--device-id", "1",
            "--skip-mtu",
            "--print-interval", "2",
            "--control-file", str(RAW_DIR / "anr_m40_worker_control.json"),
            "--csv", str(RAW_DIR / "anr_m40_live.csv"),
        ],
    },
}

KPLATES_BACKEND_HCI = "hci-direct"
KPLATES_BACKEND_BUMBLE = "bumble"


def kplates_backend_from_environment():
    return (
        os.environ.get("KINE_KPLATES_BACKEND", KPLATES_BACKEND_HCI)
        .strip()
        .lower()
        .replace("_", "-")
    )


def hci_adapter_from_environment():
    return os.environ.get("KINE_HCI_ADAPTER", "hci0")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def describe_command(command):
    action = command.get("action") or "inconnue"
    target = command.get("target")
    generation = command.get("generation") or "sans génération"
    return f"action={action}, cible={target}, génération={generation}"


class KinventBluetoothManager:
    def __init__(self, backend=None):
        self.backend = normalize_backend(backend or backend_from_environment())
        self.bumble_config = bumble_config_from_environment()
        self.kplates_backend = kplates_backend_from_environment()
        self.child = None
        self.target = None
        self.generation = None

    def state(self, phase, **extra):
        write_json(
            STATE_PATH,
            {
                "pid": os.getpid(),
                "phase": phase,
                "target": self.target,
                "child_pid": self.child.pid if self.child else None,
                "generation": self.generation,
                **extra,
            },
        )

    def start(self):
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        print(
            manager_backend_notice(self.backend, self.bumble_config),
            flush=True,
        )
        require_bumble()
        self.state(
            "idle",
            backend=self.backend,
            transport=self.bumble_config.transport,
            kplates_backend=self.kplates_backend,
            hci_adapter=hci_adapter_from_environment(),
        )

    def recover_controller_after_failure(self, failed_target, return_code):
        self.state(
            "error",
            failed_target=failed_target,
            return_code=return_code,
            error=self.failure_message(failed_target),
        )
        return False

    @staticmethod
    def failure_message(failed_target):
        if failed_target:
            config = TARGETS.get(failed_target) or {}
            state_name = config.get("state")
            if state_name:
                worker = read_json(RAW_DIR / state_name)
                error = worker.get("error") or worker.get("last_error")
                if error:
                    return error
        return "Pilote Bluetooth interrompu; reconnexion manuelle requise."

    def stop_child(self):
        if self.child is None or self.target is None:
            return True
        config = TARGETS[self.target]
        control_path = RAW_DIR / config["control"]
        stopping_target = self.target
        stopping_pid = self.child.pid
        print(
            "Arrêt du pilote capteur demandé par le gestionnaire: "
            f"{stopping_target}.",
            flush=True,
        )
        write_json(
            control_path,
            {
                "action": "disconnect",
                "generation": self.generation,
                "managed_shutdown": True,
            },
        )
        try:
            self.child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill()
                try:
                    self.child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    print(
                        "Pilote capteur bloqué dans le noyau USB; "
                        "débranchez/rebranchez le dongle nRF52840 puis "
                        "relancez la connexion.",
                        flush=True,
                    )
                    self.state(
                        "error",
                        failed_target=stopping_target,
                        blocked_child_pid=stopping_pid,
                        error=(
                            "Pilote Bluetooth bloqué côté USB. "
                            "Débranchez/rebranchez le dongle nRF52840."
                        ),
                    )
                    return False
        self.child = None
        self.target = None
        return True

    def launch(self, target):
        config = TARGETS[target]
        log_path = RAW_DIR / config["log"]
        if target == "kplates" and self.kplates_backend == KPLATES_BACKEND_HCI:
            command = [
                sys.executable,
                "-u",
                str(BASE_DIR / "scripts" / "kinvent_dual_hci.py"),
                "--adapter", hci_adapter_from_environment(),
                "--tare-duration", "2",
                "--calibration-file", str(RAW_DIR / "kplates_calibration.json"),
                "--sync-tolerance-ms", "20",
                "--control-file", str(RAW_DIR / "kplates_worker_control.json"),
                "--state-file", str(RAW_DIR / "kplates_worker_state.json"),
            ]
        elif target in {"anr_m40", "kpush", "kpull"}:
            command = [
                sys.executable,
                "-u",
                str(BASE_DIR / "scripts" / config["script"]),
                "--adapter", hci_adapter_from_environment(),
                *config["args"],
            ]
        else:
            if target == "kplates" and self.kplates_backend != KPLATES_BACKEND_BUMBLE:
                raise ValueError(
                    "Backend K-Force Plates inconnu: "
                    f"{self.kplates_backend!r}."
                )
            command = [
                sys.executable,
                "-u",
                str(BASE_DIR / "scripts" / config["script"]),
                "--transport", self.bumble_config.transport,
                *config["args"],
            ]
        log_file = log_path.open("a", encoding="utf-8")
        try:
            self.child = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        finally:
            log_file.close()
        self.target = target
        self.state("active")

    @staticmethod
    def child_exit_phase(return_code, recovered):
        if return_code and not recovered:
            return "error"
        return "idle"

    @staticmethod
    def is_manager_command(command):
        return command.get("action") in {"select", "disconnect"}

    def current_target_is_active(self, requested):
        if requested != self.target or self.child is None:
            return False
        if self.child.poll() is not None:
            self.state(
                "idle",
                stale_target=self.target,
                stale_child_pid=self.child.pid,
                error=(
                    "Pilote capteur arrêté sans état final; "
                    "relance demandée."
                ),
            )
            return False
        config = TARGETS.get(requested) or {}
        state_name = config.get("state")
        if not state_name:
            return True
        worker = read_json(RAW_DIR / state_name)
        if not worker:
            print(
                "Pilote capteur actif sans état worker; relance demandée: "
                f"{requested}.",
                flush=True,
            )
            self.state(
                "switching",
                requested_target=requested,
                stale_target=self.target,
                stale_child_pid=self.child.pid,
                error=(
                    "Pilote capteur actif sans état worker; "
                    "relance demandée."
                ),
            )
            return False
        worker_pid = worker.get("pid")
        if worker_pid != self.child.pid:
            print(
                "État worker désynchronisé du pilote capteur; "
                f"relance demandée: {requested}.",
                flush=True,
            )
            self.state(
                "switching",
                requested_target=requested,
                stale_target=self.target,
                stale_child_pid=self.child.pid,
                worker_pid=worker_pid,
                error=(
                    "État worker désynchronisé du pilote capteur; "
                    "relance demandée."
                ),
            )
            return False
        if not self.worker_state_is_fresh(worker):
            print(
                "État worker périmé du pilote capteur; "
                f"relance demandée: {requested}.",
                flush=True,
            )
            self.state(
                "switching",
                requested_target=requested,
                stale_target=self.target,
                stale_child_pid=self.child.pid,
                error=(
                    "État worker périmé du pilote capteur; "
                    "relance demandée."
                ),
            )
            return False
        return True

    @staticmethod
    def worker_state_is_fresh(worker):
        if worker.get("phase") not in {"idle", "degraded"}:
            return True
        updated_at = worker.get("updated_at")
        if not updated_at:
            return False
        try:
            updated = datetime.fromisoformat(updated_at)
        except (TypeError, ValueError):
            return False
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        return age <= WORKER_IDLE_STALE_SECONDS

    def run(self):
        self.start()
        try:
            while True:
                command = read_json(CONTROL_PATH)
                generation = command.get("generation")
                if generation and generation != self.generation:
                    action = command.get("action")
                    if not self.is_manager_command(command):
                        print(
                            "Commande Bluetooth globale ignorée: "
                            f"{describe_command(command)}.",
                            flush=True,
                        )
                        time.sleep(0.2)
                        continue
                    self.generation = generation
                    requested = command.get("target")
                    print(
                        "Commande Bluetooth globale reçue: "
                        f"{describe_command(command)}.",
                        flush=True,
                    )
                    if (
                        action == "select"
                        and self.current_target_is_active(requested)
                    ):
                        print(
                            "Capteur déjà actif; conservation du pilote: "
                            f"{requested}.",
                            flush=True,
                        )
                        self.state("active")
                        time.sleep(0.2)
                        continue
                    self.state("switching", requested_target=requested)
                    if not self.stop_child():
                        time.sleep(0.2)
                        continue
                    if requested:
                        self.launch(requested)
                    else:
                        self.state("idle")
                if self.child is not None:
                    return_code = self.child.poll()
                    if return_code is not None:
                        failed_target = self.target
                        self.child = None
                        self.target = None
                        recovered = True
                        if return_code:
                            # Une sortie anormale du pilote constitue la panne
                            # réelle. Avec Bumble/nRF52840, le gestionnaire
                            # unique ne doit pas se relancer lui-même et
                            # rejouer une ancienne commande: il repasse en
                            # erreur propre et attend une nouvelle demande.
                            recovered = self.recover_controller_after_failure(
                                failed_target,
                                return_code,
                            )
                        next_phase = self.child_exit_phase(return_code, recovered)
                        next_state = {
                            "return_code": return_code,
                            "failed_target": failed_target,
                        }
                        if next_phase == "error":
                            next_state["error"] = self.failure_message(
                                failed_target
                            )
                        self.state(next_phase, **next_state)
                time.sleep(0.2)
        finally:
            self.stop_child()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", help=argparse.SUPPRESS)
    parser.add_argument(
        "--backend",
        choices=[BUMBLE_BACKEND],
        default=None,
        help=(
            "Backend Bluetooth. Le serveur utilise uniquement Bumble/nRF52840."
        ),
    )
    args = parser.parse_args()
    KinventBluetoothManager(backend=args.backend).run()


if __name__ == "__main__":
    main()
