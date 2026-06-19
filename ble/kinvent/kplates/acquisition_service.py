import csv
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class DualPlateAcquisitionService:
    def __init__(self):
        raw_dir = BASE_DIR / "storage" / "raw_data"
        self._lock = threading.RLock()
        self._control_path = raw_dir / "kplates_worker_control.json"
        self._worker_state_path = raw_dir / "kplates_worker_state.json"
        self._worker_log_path = raw_dir / "kplates_worker.log"
        self._calibration_path = raw_dir / "kplates_calibration.json"
        self._generation = None
        self._started_at = None
        self._finished_at = None
        self._csv_path = None
        self._tare_required = None

    def start(
        self,
        adapter="hci1",
        duration=30.0,
        tare_duration=2.0,
        sync_tolerance_ms=20.0,
        filename=None,
        recalibrate=False,
    ):
        del adapter, tare_duration, sync_tolerance_ms
        with self._lock:
            worker = self._read_worker_state()
            if not self._worker_alive(worker.get("pid")):
                raise RuntimeError(
                    "Le service Bluetooth permanent n'est pas démarré."
                )
            if worker.get("phase") != "idle":
                if worker.get("phase") == "active":
                    raise RuntimeError("Une acquisition est déjà en cours.")
                if worker.get("phase") == "error":
                    raise RuntimeError(
                        worker.get("error")
                        or "La connexion Bluetooth a échoué."
                    )
                if worker.get("phase") == "disconnected":
                    raise RuntimeError(
                        "Connectez les capteurs avant de démarrer un test."
                    )
                raise RuntimeError(
                    "Les plateformes sont en cours de connexion."
                )

            if filename is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"kplates_dual_{stamp}.csv"
            if Path(filename).name != filename:
                raise ValueError("Le nom de fichier ne doit pas contenir de dossier.")
            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            self._csv_path = BASE_DIR / "storage" / "raw_data" / filename
            self._csv_path.parent.mkdir(parents=True, exist_ok=True)
            self._generation = uuid.uuid4().hex
            self._started_at = now_iso()
            self._finished_at = None
            self._tare_required = recalibrate or not self._calibration_path.exists()
            self._write_json(
                self._control_path,
                {
                    "action": "start",
                    "generation": self._generation,
                    "duration": duration,
                    "csv_path": str(self._csv_path),
                    "recalibrate": recalibrate,
                },
            )
            return self.status()

    def stop(self):
        with self._lock:
            worker = self._read_worker_state()
            generation = worker.get("generation") or self._generation
            if generation and (
                worker.get("phase") == "active"
                or self._generation is not None
            ):
                self._write_json(
                    self._control_path,
                    {"action": "stop", "generation": generation},
                )
                self._finished_at = now_iso()
            return self.status()

    def connect(self):
        with self._lock:
            worker = self._read_worker_state()
            if not self._worker_alive(worker.get("pid")):
                raise RuntimeError(
                    "Le service Bluetooth permanent n'est pas démarré."
                )
            if worker.get("phase") == "active":
                raise RuntimeError(
                    "Arrêtez le test avant de modifier la connexion."
                )
            generation = uuid.uuid4().hex
            self._write_json(
                self._control_path,
                {"action": "connect", "generation": generation},
            )
            self._generation = None
            self._started_at = None
            self._finished_at = None
            return self.status()

    def disconnect(self):
        with self._lock:
            worker = self._read_worker_state()
            if worker.get("phase") == "active":
                raise RuntimeError(
                    "Arrêtez le test avant de déconnecter les capteurs."
                )
            self._write_json(
                self._control_path,
                {"action": "disconnect"},
            )
            self._generation = None
            self._started_at = None
            self._finished_at = None
            return self.status()

    def status(self):
        with self._lock:
            worker = self._read_worker_state()
            worker_alive = self._worker_alive(worker.get("pid"))
            phase = worker.get("phase", "offline")
            generation_matches = (
                self._generation is not None
                and worker.get("generation") == self._generation
            )
            control = self._read_control()
            command_pending = (
                worker_alive
                and self._generation is not None
                and phase == "idle"
                and control.get("action") == "start"
                and control.get("generation") == self._generation
                and worker.get("generation") != self._generation
            )
            running = command_pending or (
                worker_alive
                and phase == "active"
                and (self._generation is None or generation_matches)
            )

            if generation_matches:
                if worker.get("csv_path"):
                    self._csv_path = Path(worker["csv_path"])
                if worker.get("started_at"):
                    self._started_at = worker["started_at"]
                if phase in {
                    "idle",
                    "error",
                    "disconnected",
                    "degraded",
                } and self._started_at:
                    self._finished_at = self._finished_at or now_iso()

            elapsed_seconds = None
            if self._started_at:
                started = datetime.fromisoformat(self._started_at)
                finished = (
                    datetime.fromisoformat(self._finished_at)
                    if self._finished_at
                    else datetime.now(timezone.utc)
                )
                elapsed_seconds = max(0.0, (finished - started).total_seconds())

            last_error = (
                worker.get("error")
                if phase in {"error", "disconnected", "degraded"}
                and worker.get("error")
                else None
            )
            return {
                "running": running,
                "worker_phase": phase,
                "worker_ready": worker_alive and phase == "idle",
                "bluetooth_connected": worker_alive
                and phase in {"idle", "active"},
                "connected_sides": worker.get("connected_sides", []),
                "pid": worker.get("pid") if worker_alive else None,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "return_code": None,
                "csv_path": str(self._csv_path) if self._csv_path else None,
                "log_path": str(self._worker_log_path),
                "last_error": last_error,
                "elapsed_seconds": elapsed_seconds,
                "tare_required": self._tare_required,
                "calibration_available": self._calibration_path.exists(),
            }

    def latest(self):
        with self._lock:
            status = self.status()
            row = self._read_latest_row()
            return {
                **status,
                "measurement": self._convert_measurement(row) if row else None,
                "log_tail": self._read_log_tail(),
            }

    def _read_latest_row(self):
        if self._csv_path is None or not self._csv_path.exists():
            return None
        try:
            with self._csv_path.open("rb") as csv_file:
                header = csv_file.readline().decode("utf-8").strip()
                csv_file.seek(0, os.SEEK_END)
                end = csv_file.tell()
                if end <= len(header) + 1:
                    return None
                chunk_size = min(65536, end)
                csv_file.seek(end - chunk_size)
                chunk = csv_file.read().decode("utf-8", errors="ignore")
            lines = [line for line in chunk.splitlines() if line.strip()]
            if not lines:
                return None
            fields = next(csv.reader([header]))
            values = next(csv.reader([lines[-1]]))
            if len(fields) != len(values):
                return None
            return dict(zip(fields, values))
        except (OSError, csv.Error):
            return None

    def _read_log_tail(self, max_lines=12):
        if not self._worker_log_path.exists():
            return []
        try:
            return self._worker_log_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()[-max_lines:]
        except OSError:
            return []

    def _read_worker_state(self):
        try:
            return json.loads(self._worker_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _read_control(self):
        try:
            return json.loads(self._control_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _worker_alive(pid):
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _convert_measurement(row):
        numeric_fields = {
            "sync_delta_ms",
            "left_sensor_time",
            "right_sensor_time",
            "left_kg",
            "right_kg",
            "total_kg",
            "left_n",
            "right_n",
            "total_n",
            "left_pct",
            "right_pct",
            "asymmetry_pct",
            "left_cop_x",
            "left_cop_y",
            "right_cop_x",
            "right_cop_y",
            "global_cop_x",
            "global_cop_y",
        }
        converted = {}
        for key, value in row.items():
            if key in numeric_fields:
                try:
                    converted[key] = float(value) if value != "" else None
                except (TypeError, ValueError):
                    converted[key] = None
            else:
                converted[key] = value
        return converted


dual_plate_service = DualPlateAcquisitionService()
