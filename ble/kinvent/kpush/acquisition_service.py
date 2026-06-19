import csv
import os
import signal
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class KPushAcquisitionService:
    def __init__(self):
        self._lock = threading.RLock()
        self._process = None
        self._started_at = None
        self._finished_at = None
        self._csv_path = None
        self._log_path = BASE_DIR / "storage" / "raw_data" / "kpush_worker.log"
        self._last_error = None
        self._stop_requested = False

    def start(self, duration=30.0, filename=None, tare_duration=2.0):
        with self._lock:
            self._refresh()
            if self._process is not None:
                raise RuntimeError("Une acquisition K-Push est déjà en cours.")
            if filename is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"kpush_{stamp}.csv"
            if Path(filename).name != filename:
                raise ValueError("Le nom du fichier ne doit pas contenir de dossier.")
            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            self._csv_path = BASE_DIR / "storage" / "raw_data" / filename
            self._csv_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            command = [
                "sudo",
                "-n",
                str(BASE_DIR / "scripts" / "run_kpush_session.sh"),
                str(float(duration)),
                filename,
                str(float(tare_duration)),
            ]
            log_file = self._log_path.open("w", encoding="utf-8")
            try:
                self._process = subprocess.Popen(
                    command,
                    cwd=BASE_DIR,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log_file.close()
            self._started_at = now_iso()
            self._finished_at = None
            self._last_error = None
            self._stop_requested = False
            return self.status()

    def stop(self):
        with self._lock:
            self._refresh()
            if self._process is not None:
                self._stop_requested = True
                try:
                    os.killpg(self._process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            return self.status()

    def status(self):
        with self._lock:
            self._refresh()
            running = self._process is not None
            elapsed = None
            if self._started_at:
                start = datetime.fromisoformat(self._started_at)
                end = (
                    datetime.now(timezone.utc)
                    if running
                    else datetime.fromisoformat(self._finished_at)
                    if self._finished_at
                    else datetime.now(timezone.utc)
                )
                elapsed = max(0.0, (end - start).total_seconds())
            return {
                "running": running,
                "pid": self._process.pid if running else None,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "elapsed_seconds": elapsed,
                "csv_path": str(self._csv_path) if self._csv_path else None,
                "log_path": str(self._log_path),
                "last_error": self._last_error,
            }

    def latest(self):
        with self._lock:
            status = self.status()
            row = self._read_latest_row()
            measurement = None
            if row:
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

    def _refresh(self):
        if self._process is None:
            return
        return_code = self._process.poll()
        if return_code is None:
            return
        self._process = None
        self._finished_at = self._finished_at or now_iso()
        if return_code != 0 and not self._stop_requested:
            lines = self._read_log_tail()
            self._last_error = (
                lines[-1] if lines else f"Acquisition interrompue (code {return_code})."
            )
        self._stop_requested = False

    def _read_latest_row(self):
        if self._csv_path is None or not self._csv_path.exists():
            return None
        try:
            with self._csv_path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            return rows[-1] if rows else None
        except (OSError, csv.Error, ValueError):
            return None

    def _read_max_force(self):
        if self._csv_path is None or not self._csv_path.exists():
            return 0.0
        maximum = 0.0
        try:
            with self._csv_path.open(encoding="utf-8", newline="") as source:
                for row in csv.DictReader(source):
                    maximum = max(maximum, float(row["force_n"]))
        except (OSError, csv.Error, ValueError):
            return maximum
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


kpush_service = KPushAcquisitionService()
