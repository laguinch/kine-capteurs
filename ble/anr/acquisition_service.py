import csv
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import BASE_DIR
from ble.kinvent.bluetooth_manager import (
    ManagedSensorProcess,
    manager_state,
    request_sensor,
)


FIELDS = ["timestamp_utc", "elapsed_seconds", "emg_raw"]
LIVE_STALE_SECONDS = 5.0


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class ANRM40AcquisitionService:
    """Session ANR M40 volontairement simple.

    Contrairement aux capteurs Kinvent, l'ANR n'a pas de préconnexion clinique
    persistante ici: Démarrer ouvre une session neuve, Arrêter écrit le CSV et
    libère le dongle.
    """

    def __init__(self):
        raw_dir = BASE_DIR / "storage" / "raw_data"
        self._lock = threading.RLock()
        self._process = None
        self._started_at = None
        self._finished_at = None
        self._csv_path = None
        self._live_path = raw_dir / "anr_m40_live.csv"
        self._log_path = raw_dir / "anr_m40_worker.log"
        self._recording = False
        self._last_error = None
        self._battery_pct = None
        self._max_emg = 0
        self._generation = None

    def connect(self):
        return self.status()

    def disconnect(self):
        return self.stop()

    def start(self, duration=None, filename=None):
        with self._lock:
            self._refresh()
            if self._recording:
                raise RuntimeError("Une acquisition ANR M40 est déjà en cours.")
            if filename is None:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"anr_m40_{stamp}.csv"
            if Path(filename).name != filename:
                raise ValueError("Le nom du fichier ne doit pas contenir de dossier.")
            if not filename.lower().endswith(".csv"):
                filename += ".csv"

            state = manager_state()
            if state.get("target") == "anr_m40":
                request_sensor(None)
                time.sleep(0.8)

            self._live_path.parent.mkdir(parents=True, exist_ok=True)
            for path in (self._live_path, self._log_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

            command = request_sensor("anr_m40")
            self._process = ManagedSensorProcess(
                "anr_m40",
                command["generation"],
            )
            self._generation = command["generation"]
            self._csv_path = BASE_DIR / "storage" / "raw_data" / filename
            self._started_at = now_iso()
            self._finished_at = None
            self._recording = True
            self._last_error = None
            self._battery_pct = None
            self._max_emg = 0
            self._initialize_recording_csv()
            return self.latest()

    def stop(self):
        with self._lock:
            self._refresh()
            if self._recording:
                self._write_recording_csv()
                self._recording = False
                self._finished_at = now_iso()
            if self._process is not None:
                request_sensor(None)
                self._process = None
            return self.latest()

    def status(self):
        with self._lock:
            self._refresh()
            return self._status_payload()

    def latest(self):
        with self._lock:
            self._refresh()
            status = self._status_payload()
            row = self._read_latest_row(self._live_path)
            measurement = None
            if row and self._row_belongs_to_session(row) and not self._row_is_stale(row):
                emg = int(float(row["emg_raw"]))
                self._max_emg = max(self._max_emg, emg)
                measurement = {
                    "timestamp_utc": row["timestamp_utc"],
                    "elapsed_seconds": self._elapsed_seconds(),
                    "emg_raw": emg,
                    "max_emg_raw": self._max_emg,
                    "battery_pct": status["battery_pct"],
                }
            elif self._recording and self._live_stream_is_stale():
                self._last_error = (
                    "Flux ANR M40 interrompu. Relancez le test."
                )
                self._recording = False
                self._finished_at = now_iso()
                status = self._status_payload()

            return {
                **status,
                "measurement": measurement,
            }

    def _status_payload(self):
        phase = "active" if self._recording else "ready"
        if self._last_error:
            phase = "error"
        elif self._process is not None and not self._has_session_measurement():
            phase = "connecting"
        return {
            "running": self._recording,
            "connected": self._process is not None,
            "phase": phase,
            "pid": self._process.pid if self._process is not None else None,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "elapsed_seconds": self._elapsed_seconds(),
            "duration": None,
            "csv_path": str(self._csv_path) if self._csv_path else None,
            "log_path": str(self._log_path),
            "last_error": self._last_error,
            "battery_pct": self._read_battery_from_log(),
        }

    def _elapsed_seconds(self):
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
        if self._process is None:
            return
        return_code = self._process.poll()
        if return_code is None:
            return
        self._process = None
        if self._recording:
            self._write_recording_csv()
            self._recording = False
            self._finished_at = now_iso()
        if return_code != 0:
            tail = self._read_log_tail()
            self._last_error = (
                tail[-1] if tail else f"Connexion ANR interrompue (code {return_code})."
            )

    def _row_belongs_to_session(self, row):
        return bool(
            self._started_at
            and row.get("timestamp_utc", "") >= self._started_at
        )

    def _has_session_measurement(self):
        row = self._read_latest_row(self._live_path)
        return bool(row and self._row_belongs_to_session(row))

    def _live_stream_is_stale(self):
        row = self._read_latest_row(self._live_path)
        return row is not None and self._row_belongs_to_session(row) and self._row_is_stale(row)

    @staticmethod
    def _row_is_stale(row):
        try:
            updated = datetime.fromisoformat(row["timestamp_utc"])
        except (KeyError, TypeError, ValueError):
            return True
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated).total_seconds() > LIVE_STALE_SECONDS

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

    def _recording_rows(self):
        if not self._started_at or not self._live_path.exists():
            return []
        try:
            with self._live_path.open(encoding="utf-8", newline="") as source:
                rows = list(csv.DictReader(source))
        except (OSError, csv.Error):
            return []
        return [
            row for row in rows
            if row.get("timestamp_utc", "") >= self._started_at
        ]

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
                while position > 0 and buffer.count(b"\n") < 8:
                    read_size = min(4096, position)
                    position -= read_size
                    source.seek(position)
                    buffer[:0] = source.read(read_size)
            lines = buffer.decode("utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                if not line.strip() or line.startswith("timestamp_utc,"):
                    continue
                try:
                    row = next(
                        csv.DictReader(
                            ["timestamp_utc,elapsed_seconds,emg_raw", line]
                        )
                    )
                    float(row["elapsed_seconds"])
                    int(float(row["emg_raw"]))
                    return row
                except (
                    csv.Error,
                    IndexError,
                    KeyError,
                    StopIteration,
                    TypeError,
                    ValueError,
                ):
                    continue
        except (OSError, csv.Error, ValueError):
            return None
        return None

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


anr_m40_service = ANRM40AcquisitionService()
