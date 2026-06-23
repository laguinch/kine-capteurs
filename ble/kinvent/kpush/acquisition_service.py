import csv
import json
import os
import signal
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR
from ble.kinvent.bluetooth_manager import (
    ManagedSensorProcess,
    manager_state,
    request_sensor,
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class KPushAcquisitionService:
    def __init__(self):
        raw_dir = BASE_DIR / "storage" / "raw_data"
        self._lock = threading.RLock()
        self._process = None
        self._connected_at = None
        self._started_at = None
        self._finished_at = None
        self._duration = None
        self._recording = False
        self._csv_path = None
        self._live_path = raw_dir / "kpush_live.csv"
        self._log_path = raw_dir / "kpush_worker.log"
        self._control_path = raw_dir / "kpush_worker_control.json"
        self._last_error = None
        self._stop_requested = False
        self._sensor_ready = False
        self._test_generation = None

    def connect(self, tare_duration=2.0):
        with self._lock:
            self._refresh()
            if self._process is not None:
                return self.status()
            self._live_path.parent.mkdir(parents=True, exist_ok=True)
            for path in (self._live_path, self._log_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            self._write_control("idle")
            del tare_duration
            command = request_sensor("kpush")
            self._process = ManagedSensorProcess(
                "kpush",
                command["generation"],
            )
            self._connected_at = now_iso()
            self._last_error = None
            self._stop_requested = False
            self._sensor_ready = False
            return self.status()

    def disconnect(self):
        with self._lock:
            self._refresh()
            if self._recording:
                raise RuntimeError("Arrêtez le test avant de déconnecter le K-Push.")
            if self._process is not None:
                self._stop_requested = True
                request_sensor(None)
            return self.status()

    def start(self, duration=30.0, filename=None, tare_duration=2.0):
        del tare_duration
        with self._lock:
            self._refresh()
            if self._process is None:
                raise RuntimeError("Connectez le K-Push avant de démarrer.")
            if self._connection_phase() != "ready":
                raise RuntimeError("Attendez la fin de la connexion et de la tare.")
            if self._recording:
                raise RuntimeError("Une acquisition K-Push est déjà en cours.")
            if filename is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"kpush_{stamp}.csv"
            if Path(filename).name != filename:
                raise ValueError("Le nom du fichier ne doit pas contenir de dossier.")
            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            self._csv_path = BASE_DIR / "storage" / "raw_data" / filename
            self._started_at = now_iso()
            self._finished_at = None
            self._duration = float(duration)
            self._recording = True
            self._test_generation = uuid.uuid4().hex
            self._write_control("start", self._test_generation)
            self._write_recording_csv()
            return self.status()

    def stop(self):
        with self._lock:
            self._refresh()
            if self._recording:
                self._recording = False
                self._finished_at = now_iso()
                self._write_control("stop", self._test_generation)
                # L'état doit basculer immédiatement. La copie finale du CSV
                # vient ensuite et ne peut plus maintenir l'interface en mode
                # « Acquisition en cours ».
                self._write_recording_csv()
            return self.status()

    def status(self):
        with self._lock:
            self._refresh()
            phase = self._connection_phase()
            elapsed = self._recording_elapsed()
            if self._recording and self._duration and elapsed >= self._duration:
                self.stop()
                elapsed = self._recording_elapsed()
                phase = self._connection_phase()
            return {
                "running": self._recording,
                "connected": self._process is not None,
                "phase": "active" if self._recording else phase,
                "pid": self._process.pid if self._process is not None else None,
                "connected_at": self._connected_at,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "elapsed_seconds": elapsed,
                "duration": self._duration,
                "csv_path": str(self._csv_path) if self._csv_path else None,
                "log_path": str(self._log_path),
                "last_error": self._last_error,
            }

    def latest(self):
        with self._lock:
            status = self.status()
            if self._recording:
                self._write_recording_csv()
            row = self._read_latest_row(self._live_path)
            measurement = None
            if row and status["phase"] in {"ready", "active"}:
                measurement = {
                    "timestamp_utc": row["timestamp_utc"],
                    "sensor_time": float(row["sensor_time"]),
                    "force_kg": float(row["force_kg"]),
                    "force_n": float(row["force_n"]),
                }
                maximum = self._read_max_force()
                measurement["max_force_n"] = maximum
                measurement["max_force_kg"] = maximum / 9.81
            return {
                **status,
                "measurement": measurement,
                "log_tail": self._read_log_tail(),
            }

    def _connection_phase(self):
        if self._process is None:
            return "error" if self._last_error else "disconnected"
        if self._sensor_ready:
            return "ready"
        text = self._read_log()
        if "K-Push prêt; liaison Bluetooth conservée." in text:
            self._sensor_ready = True
            return "ready"
        if "Tare K-Push pendant" in text:
            return "tare"
        return "connecting"

    def _recording_elapsed(self):
        if not self._started_at:
            return 0.0
        start = datetime.fromisoformat(self._started_at)
        end = (
            datetime.now(timezone.utc)
            if self._recording
            else datetime.fromisoformat(self._finished_at)
            if self._finished_at
            else start
        )
        return max(0.0, (end - start).total_seconds())

    def _refresh(self):
        if self._process is None and manager_state().get("target") == "kpush":
            state = manager_state()
            self._process = ManagedSensorProcess(
                "kpush",
                state.get("generation"),
            )
        if self._process is None:
            return
        return_code = self._process.poll()
        if return_code is None:
            return
        self._process = None
        if self._recording:
            self._recording = False
            self._finished_at = now_iso()
        if return_code != 0 and not self._stop_requested:
            lines = self._read_log_tail()
            self._last_error = (
                lines[-1] if lines else f"Connexion interrompue (code {return_code})."
            )
        self._stop_requested = False
        self._sensor_ready = False

    def _write_control(self, action, generation=None):
        self._control_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._control_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "action": action,
                    "generation": generation or uuid.uuid4().hex,
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(self._control_path)

    def _recording_rows(self):
        if not self._started_at or not self._live_path.exists():
            return []
        try:
            with self._live_path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            return [
                row for row in rows
                if row.get("timestamp_utc", "") >= self._started_at
            ]
        except (OSError, csv.Error):
            return []

    def _write_recording_csv(self):
        if self._csv_path is None:
            return
        rows = self._recording_rows()
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self._csv_path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(
                target,
                fieldnames=[
                    "timestamp_utc",
                    "sensor_time",
                    "raw_force",
                    "tare_offset",
                    "force_counts",
                    "force_kg",
                    "force_n",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _read_latest_row(path):
        if path is None or not path.exists():
            return None
        try:
            with path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            return rows[-1] if rows else None
        except (OSError, csv.Error, ValueError):
            return None

    def _read_max_force(self):
        maximum = 0.0
        for row in self._recording_rows():
            try:
                maximum = max(maximum, float(row["force_n"]))
            except (TypeError, ValueError):
                continue
        return maximum

    def _read_log_tail(self, max_lines=12):
        if not self._log_path.exists():
            return []
        try:
            return self._log_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()[-max_lines:]
        except OSError:
            return []

    def _read_log(self):
        if not self._log_path.exists():
            return ""
        try:
            return self._log_path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return ""


kpush_service = KPushAcquisitionService()
