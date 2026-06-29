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


class KMoveAcquisitionService:
    def __init__(self):
        raw_dir = BASE_DIR / "storage" / "raw_data"
        self._lock = threading.RLock()
        self._process = None
        self._connected_at = None
        self._started_at = None
        self._finished_at = None
        self._duration = None
        self._recording = False
        self._armed = False
        self._csv_path = None
        self._live_path = raw_dir / "kmove_live.csv"
        self._log_path = raw_dir / "kmove_worker.log"
        self._control_path = raw_dir / "kmove_worker_control.json"
        self._last_error = None
        self._stop_requested = False
        self._sensor_ready = False
        self._test_generation = None

    def connect(self, reference_duration=2.0):
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
            del reference_duration
            command = request_sensor("kmove")
            self._process = ManagedSensorProcess(
                "kmove",
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
                raise RuntimeError("Arrêtez le test avant de déconnecter le K-Move.")
            if self._armed:
                raise RuntimeError("Annulez le test armé avant de déconnecter le K-Move.")
            if self._process is not None:
                self._stop_requested = True
                request_sensor(None)
            return self.status()

    def start(self, duration=30.0, filename=None):
        with self._lock:
            self._refresh()
            if self._process is None:
                raise RuntimeError("Connectez le K-Move avant de démarrer.")
            if self._connection_phase() != "ready":
                raise RuntimeError("Attendez la fin de la prise de référence.")
            if self._recording or self._armed:
                raise RuntimeError("Une acquisition K-Move est déjà en cours.")
            if filename is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"kmove_{stamp}.csv"
            if Path(filename).name != filename:
                raise ValueError("Le nom du fichier ne doit pas contenir de dossier.")
            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            self._csv_path = BASE_DIR / "storage" / "raw_data" / filename
            self._started_at = None
            self._finished_at = None
            self._duration = float(duration)
            self._recording = False
            self._armed = True
            self._test_generation = uuid.uuid4().hex
            self._write_control("start", self._test_generation)
            self._write_recording_csv()
            return self.status()

    def stop(self):
        with self._lock:
            self._refresh()
            if self._armed:
                self._armed = False
                self._finished_at = None
                self._stop_requested = True
                self._write_control("stop", self._test_generation)
            elif self._recording:
                self._recording = False
                self._finished_at = now_iso()
                self._stop_requested = True
                self._write_control("stop", self._test_generation)
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
            if self._armed:
                phase = "armed"
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
            row = self._read_latest_row(self._live_path)
            if self._armed and row and self._should_trigger(row):
                self._begin_recording(row)
                status = self.status()
            if self._recording:
                self._write_recording_csv()
            measurement = None
            if row and status["phase"] in {"ready", "armed", "active"}:
                measurement = {
                    "timestamp_utc": row["timestamp_utc"],
                    "sensor_time": float(row["sensor_time"]),
                    "rotation_deg": float(row["rotation_x_deg"]),
                    "flexion_extension_deg": float(row["rotation_y_deg"]),
                    "inclination_deg": float(row["rotation_z_deg"]),
                    "battery_pct": int(float(row["battery_pct"])),
                }
                measurement["ranges"] = self._read_ranges()
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
        if "K-Move prêt; liaison Bluetooth conservée." in text:
            self._sensor_ready = True
            return "ready"
        if "Référence K-Move pendant" in text:
            return "reference"
        if self._connected_at:
            connected_at = datetime.fromisoformat(self._connected_at)
            waiting = (
                datetime.now(timezone.utc) - connected_at
            ).total_seconds()
            if waiting >= 45:
                self._last_error = (
                    "Le K-Move est connecté, mais son flux de mesure "
                    "ne démarre pas."
                )
                return "error"
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
        if self._process is None and manager_state().get("target") == "kmove":
            state = manager_state()
            self._process = ManagedSensorProcess(
                "kmove",
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
        self._armed = False
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

    def _begin_recording(self, row):
        self._started_at = row.get("timestamp_utc") or now_iso()
        self._finished_at = None
        self._recording = True
        self._armed = False

    @staticmethod
    def _should_trigger(row):
        try:
            values = (
                abs(float(row.get("rotation_x_deg") or 0)),
                abs(float(row.get("rotation_y_deg") or 0)),
                abs(float(row.get("rotation_z_deg") or 0)),
            )
            return max(values) >= 2.0
        except (TypeError, ValueError):
            return False

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
        fieldnames = [
            "timestamp_utc",
            "sensor_time",
            "quaternion_w",
            "quaternion_x",
            "quaternion_y",
            "quaternion_z",
            "rotation_x_deg",
            "rotation_y_deg",
            "rotation_z_deg",
            "accel_x_raw",
            "accel_y_raw",
            "accel_z_raw",
            "battery_pct",
        ]
        with self._csv_path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=fieldnames)
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

    def _read_ranges(self):
        fields = {
            "rotation": "rotation_x_deg",
            "flexion_extension": "rotation_y_deg",
            "inclination": "rotation_z_deg",
        }
        ranges = {
            name: {"min": 0.0, "max": 0.0}
            for name in fields
        }
        for row in self._recording_rows():
            for name, field in fields.items():
                try:
                    value = float(row[field])
                except (KeyError, TypeError, ValueError):
                    continue
                ranges[name]["min"] = min(ranges[name]["min"], value)
                ranges[name]["max"] = max(ranges[name]["max"], value)
        return ranges

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


kmove_service = KMoveAcquisitionService()
