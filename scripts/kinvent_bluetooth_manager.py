"""Gestionnaire unique du dongle Bluetooth Kinvent."""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import BASE_DIR  # noqa: E402
from ble.kinvent.bluetooth_manager import (  # noqa: E402
    CONTROL_PATH,
    STATE_PATH,
)
from scripts.kinvent_raw_hci import RawKinventClient, parse_adapter  # noqa: E402


RAW_DIR = BASE_DIR / "storage" / "raw_data"

TARGETS = {
    "kplates": {
        "script": "kinvent_dual_hci.py",
        "control": "kplates_worker_control.json",
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
        ],
    },
    "kmove": {
        "script": "kinvent_kmove_hci.py",
        "control": "kmove_worker_control.json",
        "log": "kmove_worker.log",
        "args": [
            "--duration", "0",
            "--reference-duration", "2",
            "--control-file", str(RAW_DIR / "kmove_worker_control.json"),
            "--csv", str(RAW_DIR / "kmove_live.csv"),
        ],
    },
}


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


class KinventBluetoothManager:
    def __init__(self, adapter):
        self.adapter = adapter
        self.controller = RawKinventClient(
            adapter=adapter,
            address="00:00:00:00:00:00",
            address_type="public",
        )
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
        try:
            self.controller.open()
            self.controller.reset()
        except (OSError, RuntimeError, TimeoutError) as exc:
            self.state(
                "error",
                error=f"Contrôleur Bluetooth indisponible: {exc}",
            )
            self.controller.close()
            raise
        self.state("idle")

    def recover_controller_after_failure(self, failed_target, return_code):
        """Récupère le dongle uniquement après une panne réelle du pilote."""
        last_error = None
        for attempt in range(1, 3):
            try:
                self.state(
                    "recovering",
                    failed_target=failed_target,
                    return_code=return_code,
                    attempt=attempt,
                )
                self.controller.reset()
                return True
            except (OSError, RuntimeError, TimeoutError) as exc:
                last_error = exc
                try:
                    self.controller.close()
                except OSError:
                    pass
                time.sleep(1.0)
                try:
                    self.controller.open()
                except OSError as open_error:
                    last_error = open_error
                    time.sleep(1.0)
        self.state(
            "error",
            failed_target=failed_target,
            return_code=return_code,
            error=f"Contrôleur Bluetooth non récupéré: {last_error}",
        )
        return False

    def stop_child(self):
        if self.child is None or self.target is None:
            return
        config = TARGETS[self.target]
        control_path = RAW_DIR / config["control"]
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
            self.child.wait(timeout=3)
        self.child = None
        self.target = None

    def launch(self, target):
        config = TARGETS[target]
        log_path = RAW_DIR / config["log"]
        command = [
            sys.executable,
            "-u",
            str(BASE_DIR / "scripts" / config["script"]),
            "--adapter", str(self.adapter),
            "--hci-fd", str(self.controller.sock.fileno()),
            *config["args"],
        ]
        log_file = log_path.open("a", encoding="utf-8")
        try:
            self.child = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                pass_fds=(self.controller.sock.fileno(),),
            )
        finally:
            log_file.close()
        self.target = target
        self.state("active")

    def run(self):
        self.start()
        try:
            while True:
                command = read_json(CONTROL_PATH)
                generation = command.get("generation")
                if generation and generation != self.generation:
                    self.generation = generation
                    requested = command.get("target")
                    self.state("switching", requested_target=requested)
                    self.stop_child()
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
                        if return_code:
                            # Une sortie anormale du pilote constitue la panne
                            # réelle pour laquelle une nouvelle initialisation
                            # du contrôleur est autorisée.
                            recovered = self.recover_controller_after_failure(
                                failed_target,
                                return_code,
                            )
                            if not recovered:
                                raise RuntimeError(
                                    "Contrôleur Bluetooth non récupéré"
                                )
                        self.state(
                            "error" if return_code else "idle",
                            return_code=return_code,
                            failed_target=failed_target,
                        )
                time.sleep(0.2)
        finally:
            self.stop_child()
            self.controller.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", type=parse_adapter, default=0)
    args = parser.parse_args()
    KinventBluetoothManager(args.adapter).run()


if __name__ == "__main__":
    main()
