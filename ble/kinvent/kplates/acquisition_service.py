import csv
import os
import shlex
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR


try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class DualPlateAcquisitionService:
    def __init__(self):
        self._lock = threading.RLock()
        self._process = None
        self._started_at = None
        self._finished_at = None
        self._csv_path = None
        self._last_error = None
        self._return_code = None

    def _refresh(self):
        if self._process is None:
            return
        return_code = self._process.poll()
        if return_code is None:
            return
        self._return_code = return_code
        self._finished_at = self._finished_at or now_iso()
        if return_code != 0 and self._last_error is None:
            log_lines = self._read_log_tail()
            detail = next(
                (line.strip() for line in reversed(log_lines) if line.strip()),
                None,
            )
            self._last_error = (
                f"Échec du lancement Bluetooth (code {return_code})"
                + (f" : {detail}" if detail else ".")
            )

    def start(
        self,
        adapter="hci1",
        duration=30.0,
        tare_duration=2.0,
        sync_tolerance_ms=20.0,
        filename=None,
    ):
        with self._lock:
            self._refresh()
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("Une acquisition est déjà en cours.")

            if filename is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"kplates_dual_{stamp}.csv"
            if Path(filename).name != filename:
                raise ValueError("Le nom de fichier ne doit pas contenir de dossier.")
            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            csv_path = BASE_DIR / "storage" / "raw_data" / filename
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            script = BASE_DIR / "scripts" / "kinvent_dual_hci.py"
            prefix = shlex.split(os.getenv("KINE_HCI_COMMAND_PREFIX", ""))
            command = prefix + [
                sys.executable,
                "-u",
                str(script),
                "--adapter",
                adapter,
                "--duration",
                str(duration),
                "--tare-duration",
                str(tare_duration),
                "--sync-tolerance-ms",
                str(sync_tolerance_ms),
                "--csv",
                str(csv_path),
            ]

            log_path = csv_path.with_suffix(".log")
            log_file = log_path.open("w", encoding="utf-8")
            try:
                try:
                    self._process = subprocess.Popen(
                        command,
                        cwd=BASE_DIR,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as exc:
                    raise RuntimeError(
                        f"Impossible de lancer le processus Bluetooth: {exc}"
                    ) from exc
            finally:
                log_file.close()

            self._started_at = now_iso()
            self._finished_at = None
            self._csv_path = csv_path
            self._last_error = None
            self._return_code = None
            return self.status()

    def stop(self):
        with self._lock:
            self._refresh()
            if self._process is None or self._process.poll() is not None:
                return self.status()
            os.killpg(self._process.pid, signal.SIGTERM)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self._process.pid, signal.SIGKILL)
                self._process.wait(timeout=2)
            self._return_code = self._process.returncode
            self._finished_at = now_iso()
            return self.status()

    def status(self):
        with self._lock:
            self._refresh()
            running = self._process is not None and self._process.poll() is None
            elapsed_seconds = None
            if self._started_at:
                started = datetime.fromisoformat(self._started_at)
                finished = (
                    datetime.fromisoformat(self._finished_at)
                    if self._finished_at
                    else datetime.now(timezone.utc)
                )
                elapsed_seconds = max(0.0, (finished - started).total_seconds())
            return {
                "running": running,
                "pid": self._process.pid if running else None,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "return_code": self._return_code,
                "csv_path": str(self._csv_path) if self._csv_path else None,
                "log_path": str(self._csv_path.with_suffix(".log"))
                if self._csv_path
                else None,
                "last_error": self._last_error,
                "elapsed_seconds": elapsed_seconds,
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
            last_line = lines[-1]
            if last_line == header:
                return None
            fields = next(csv.reader([header]))
            values = next(csv.reader([last_line]))
            if len(fields) != len(values):
                return None
            return dict(zip(fields, values))
        except (OSError, csv.Error):
            return None

    def _read_log_tail(self, max_lines=12):
        if self._csv_path is None:
            return []
        log_path = self._csv_path.with_suffix(".log")
        if not log_path.exists():
            return []
        try:
            return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[
                -max_lines:
            ]
        except OSError:
            return []

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
