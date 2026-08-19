import csv
import json
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


FIELDS = ["timestamp_utc", "elapsed_seconds", "emg_raw"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class ANRM40AcquisitionService:
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
        self._live_path = raw_dir / "anr_m40_live.csv"
        self._log_path = raw_dir / "anr_m40_worker.log"
        self._control_path = raw_dir / "anr_m40_worker_control.json"
        self._last_error = None
        self._stop_requested = False
        self._sensor_ready = False
        self._test_generation = None
        self._recording_last_timestamp = None
        self._recording_max_emg = 0
        self._recording_live_position = 0
        self._recording_pending_fragment = ""
        self._battery_pct = None

    def connect(self):
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
            command = request_sensor("anr_m40")
            self._process = ManagedSensorProcess(
                "anr_m40",
                command["generation"],
            )
            self._connected_at = now_iso()
            self._last_error = None
            self._stop_requested = False
            self._sensor_ready = False
            self._battery_pct = None
            return self.status()

    def disconnect(self):
        with self._lock:
            self._refresh()
            if self._recording:
                raise RuntimeError("Arrêtez le test avant de déconnecter l'ANR M40.")
            if self._armed:
                raise RuntimeError("Annulez le test armé avant de déconnecter l'ANR M40.")
            if self._process is not None:
                self._stop_requested = True
                request_sensor(None)
            return self.status()

    def start(self, duration=30.0, filename=None):
        with self._lock:
            self._refresh()
            if self._process is None:
                raise RuntimeError("Connectez l'ANR M40 avant de démarrer.")
            if self._connection_phase() != "ready":
                raise RuntimeError("Attendez la fin de la connexion ANR M40.")
            if self._recording or self._armed:
                raise RuntimeError("Une acquisition ANR M40 est déjà en cours.")
            if filename is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"anr_m40_{stamp}.csv"
            if Path(filename).name != filename:
                raise ValueError("Le nom du fichier ne doit pas contenir de dossier.")
            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            self._csv_path = BASE_DIR / "storage" / "raw_data" / filename
            self._started_at = now_iso()
            self._finished_at = None
            self._duration = float(duration)
            self._recording = True
            self._armed = False
            self._test_generation = uuid.uuid4().hex
            self._recording_last_timestamp = None
            self._recording_max_emg = 0
            self._recording_live_position = self._live_file_size()
            self._recording_pending_fragment = ""
            self._write_control("start", self._test_generation)
            self._initialize_recording_csv()
            return self.status()

    def stop(self):
        with self._lock:
            self._refresh()
            if self._armed:
                self._armed = False
                self._finished_at = None
                self._write_control("stop", self._test_generation)
            elif self._recording:
                self._write_recording_csv()
                self._recording = False
                self._finished_at = now_iso()
                self._write_control("stop", self._test_generation)
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
                "battery_pct": self._read_battery_from_log(),
            }

    def latest(self):
        with self._lock:
            status = self.status()
            row = self._read_latest_row(self._live_path)
            measurement = None
            if row and status["phase"] in {"ready", "active"}:
                emg_raw = int(float(row["emg_raw"]))
                if self._recording:
                    self._recording_max_emg = max(
                        self._recording_max_emg,
                        emg_raw,
                    )
                maximum = (
                    self._recording_max_emg
                    if self._recording
                    else self._read_max_emg()
                )
                measurement = {
                    "timestamp_utc": row["timestamp_utc"],
                    "elapsed_seconds": float(row["elapsed_seconds"]),
                    "emg_raw": emg_raw,
                    "max_emg_raw": maximum,
                    "battery_pct": status.get("battery_pct"),
                }
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
        if "ANR M40 prêt; liaison Bluetooth conservée." in text:
            self._update_battery_from_text(text)
            self._sensor_ready = True
            return "ready"
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
        state = manager_state()
        if (
            self._process is None
            and state.get("target") == "anr_m40"
            and state.get("phase") in {"switching", "active"}
        ):
            self._process = ManagedSensorProcess(
                "anr_m40",
                state.get("generation"),
            )
        if self._process is None:
            self._recording = False
            self._armed = False
            self._sensor_ready = False
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

    def _recording_rows(self, since_timestamp=None):
        if not self._started_at or not self._live_path.exists():
            return []
        try:
            with self._live_path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
            return [
                row for row in rows
                if row.get("timestamp_utc", "") >= self._started_at
                and (
                    since_timestamp is None
                    or row.get("timestamp_utc", "") > since_timestamp
                )
            ]
        except (OSError, csv.Error):
            return []

    def _initialize_recording_csv(self):
        if self._csv_path is None:
            return
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self._csv_path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writeheader()

    def _write_recording_csv(self):
        if self._csv_path is None:
            return
        rows = self._recording_rows()
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self._csv_path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        self._recording_max_emg = 0
        for row in rows:
            try:
                self._recording_max_emg = max(
                    self._recording_max_emg,
                    int(float(row["emg_raw"])),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def _append_recording_rows(self):
        if self._csv_path is None:
            return
        rows = self._read_new_recording_rows()
        if not rows:
            return
        self._csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self._csv_path.open("a", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writerows(rows)
        self._recording_last_timestamp = rows[-1].get("timestamp_utc")
        for row in rows:
            try:
                self._recording_max_emg = max(
                    self._recording_max_emg,
                    int(float(row["emg_raw"])),
                )
            except (KeyError, TypeError, ValueError):
                continue

    @staticmethod
    def _read_latest_row(path):
        if path is None or not path.exists():
            return None
        try:
            with path.open("rb") as source:
                source.seek(0, 2)
                position = source.tell()
                if position == 0:
                    return None
                buffer = bytearray()
                while position > 0 and buffer.count(b"\n") < 2:
                    read_size = min(4096, position)
                    position -= read_size
                    source.seek(position)
                    buffer[:0] = source.read(read_size)
            lines = buffer.decode("utf-8", errors="replace").splitlines()
            if len(lines) < 2:
                return None
            line = lines[-1] if lines[-1].strip() else lines[-2]
            if line.startswith("timestamp_utc,"):
                return None
            row = next(csv.DictReader(["timestamp_utc,elapsed_seconds,emg_raw", line]))
            return row
        except (OSError, csv.Error, StopIteration, ValueError):
            return None

    def _live_file_size(self):
        try:
            return self._live_path.stat().st_size
        except OSError:
            return 0

    def _read_new_recording_rows(self):
        if not self._started_at or not self._live_path.exists():
            return []
        try:
            with self._live_path.open("rb") as source:
                source.seek(self._recording_live_position)
                chunk = source.read()
                self._recording_live_position = source.tell()
            if not chunk:
                return []
            text = self._recording_pending_fragment + chunk.decode(
                "utf-8",
                errors="replace",
            )
            lines = text.splitlines()
            if text and not text.endswith(("\n", "\r")):
                self._recording_pending_fragment = lines.pop() if lines else text
            else:
                self._recording_pending_fragment = ""
            rows = csv.DictReader(["timestamp_utc,elapsed_seconds,emg_raw", *lines])
            return [
                row for row in rows
                if row.get("timestamp_utc", "") >= self._started_at
                and (
                    self._recording_last_timestamp is None
                    or row.get("timestamp_utc", "") > self._recording_last_timestamp
                )
            ]
        except (OSError, csv.Error, ValueError):
            return []

    def _read_max_emg(self):
        maximum = 0
        if self._csv_path and self._csv_path.exists():
            try:
                with self._csv_path.open(encoding="utf-8", newline="") as source:
                    rows = list(csv.DictReader(source))
            except (OSError, csv.Error):
                rows = []
        else:
            rows = self._recording_rows()
        for row in rows:
            try:
                maximum = max(maximum, int(float(row["emg_raw"])))
            except (KeyError, TypeError, ValueError):
                continue
        return maximum

    def _read_battery_from_log(self):
        if self._battery_pct is not None:
            return self._battery_pct
        for line in reversed(self._read_log_tail(max_lines=80)):
            marker = "Batterie M40:"
            if marker not in line or "%" not in line:
                continue
            try:
                self._battery_pct = int(
                    line.split(marker, 1)[1].split("%", 1)[0].strip()
                )
                return self._battery_pct
            except (IndexError, ValueError):
                continue
        return None

    def _update_battery_from_text(self, text):
        marker = "Batterie M40:"
        for line in reversed(text.splitlines()):
            if marker not in line or "%" not in line:
                continue
            try:
                self._battery_pct = int(
                    line.split(marker, 1)[1].split("%", 1)[0].strip()
                )
                return
            except (IndexError, ValueError):
                continue

    def _read_log_tail(self, max_lines=12):
        if not self._log_path.exists():
            return []
        try:
            with self._log_path.open("rb") as source:
                source.seek(0, 2)
                position = source.tell()
                buffer = bytearray()
                while position > 0 and buffer.count(b"\n") <= max_lines:
                    read_size = min(8192, position)
                    position -= read_size
                    source.seek(position)
                    buffer[:0] = source.read(read_size)
            return buffer.decode(
                "utf-8",
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


anr_m40_service = ANRM40AcquisitionService()
