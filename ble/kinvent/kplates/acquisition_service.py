import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR


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
            self._last_error = f"Le processus Bluetooth s'est arrêté avec le code {return_code}."

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

            csv_path = BASE_DIR / "storage" / "raw_data" / filename
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            script = BASE_DIR / "scripts" / "kinvent_dual_hci.py"
            prefix = os.getenv("KINE_HCI_COMMAND_PREFIX", "").split()
            command = prefix + [
                sys.executable,
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
            self._csv_path = csv_path
            self._last_error = None
            self._return_code = None
            return self.status()

    def stop(self):
        with self._lock:
            self._refresh()
            if self._process is None or self._process.poll() is not None:
                return self.status()
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._return_code = self._process.returncode
            self._finished_at = now_iso()
            return self.status()

    def status(self):
        with self._lock:
            self._refresh()
            running = self._process is not None and self._process.poll() is None
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
            }


dual_plate_service = DualPlateAcquisitionService()
