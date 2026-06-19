"""Acquisition simultanée de deux K-Force Plates sans GATT BlueZ."""

import argparse
import csv
import errno
import json
import os
import socket
import struct
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BLUETOOTH_SYSFS = Path("/sys/class/bluetooth")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.common.devices import KPLATE_LEFT, KPLATE_RIGHT  # noqa: E402
from ble.kinvent.kplates.protocol import (  # noqa: E402
    MIN_VALID_KG,
    compute_distribution,
    parse_frame,
)
from scripts.kinvent_raw_hci import (  # noqa: E402
    AF_BLUETOOTH,
    ATT_CID,
    ATT_OP_ERROR_RESPONSE,
    ATT_OP_FIND_BY_TYPE_VALUE_REQUEST,
    ATT_OP_MTU_REQUEST,
    ATT_OP_MTU_RESPONSE,
    ATT_OP_NOTIFICATION,
    ATT_OP_WRITE_COMMAND,
    ATT_OP_WRITE_REQUEST,
    ATT_OP_WRITE_RESPONSE,
    BTPROTO_HCI,
    EVT_CMD_COMPLETE,
    EVT_CMD_STATUS,
    EVT_DISCONN_COMPLETE,
    EVT_LE_ADVERTISING_REPORT,
    EVT_LE_CONN_COMPLETE,
    EVT_LE_ENHANCED_CONN_COMPLETE,
    EVT_LE_META_EVENT,
    HCI_ACLDATA_PKT,
    HCI_CHANNEL_USER,
    HCI_COMMAND_PKT,
    HCI_EVENT_PKT,
    INIT_COMMANDS,
    OCF_LE_CREATE_CONN,
    OCF_LE_REMOTE_CONN_PARAM_REQ_REPLY,
    OCF_LE_SET_EVENT_MASK,
    OCF_LE_SET_SCAN_ENABLE,
    OCF_LE_SET_SCAN_PARAMETERS,
    OCF_RESET,
    OCF_SET_EVENT_MASK,
    OGF_HOST_CTL,
    OGF_LE_CTL,
    SockaddrHci,
    UART_CCCD_HANDLE,
    UART_VALUE_HANDLE,
    address_to_le_bytes,
    hci_opcode,
    le_bytes_to_address,
    parse_adapter,
)

OGF_LINK_CTL = 0x01
OCF_DISCONNECT = 0x0006
CSV_FIELDS = [
    "timestamp_utc",
    "sync_delta_ms",
    "sync_quality",
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
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class PlateDisconnected(ConnectionError):
    def __init__(self, plate, reason):
        self.plate = plate
        self.reason = reason
        super().__init__(
            f"Plateforme {plate.side} déconnectée: raison 0x{reason:02x}"
        )


def available_hci_adapters():
    if not BLUETOOTH_SYSFS.exists():
        return []
    adapters = []
    for entry in BLUETOOTH_SYSFS.glob("hci*"):
        suffix = entry.name[3:]
        if suffix.isdigit():
            adapters.append(int(suffix))
    return sorted(adapters)


def resolve_hci_adapter(requested, timeout=8.0):
    deadline = time.monotonic() + timeout
    while True:
        available = available_hci_adapters()
        if requested in available:
            return requested

        # Si le dongle USB a été réenregistré après une acquisition, son nouvel
        # index reste généralement non nul.
        external = [adapter for adapter in available if adapter != 0]
        if external:
            selected = max(external)
            print(
                f"hci{requested} n'est plus disponible; "
                f"utilisation automatique de hci{selected}."
            )
            return selected

        # Sur le serveur du cabinet, bluetooth.service est désactivé et hci0
        # peut servir de contrôleur de secours lorsque le dongle USB disparaît.
        if available == [0]:
            print(
                f"hci{requested} n'est plus disponible; "
                "utilisation automatique de hci0."
            )
            return 0

        if time.monotonic() >= deadline:
            visible = ", ".join(f"hci{item}" for item in available) or "aucun"
            raise SystemExit(
                f"Contrôleur hci{requested} introuvable après {timeout:.0f} s "
                f"(contrôleurs visibles: {visible})."
            )
        time.sleep(0.25)


class PlateState:
    def __init__(self, side, address, tare_duration, buffer_size=64):
        self.side = side
        self.address = address.upper()
        self.address_le = address_to_le_bytes(address)
        self.handle = None
        self.tare_duration = tare_duration
        self.tare_started_at = None
        self.tare_samples = []
        self.offsets = None
        self.latest = None
        self.distribution = None
        self.notifications = 0
        self.last_notification_at = None
        self.stream_restarts = 0
        self.samples = deque(maxlen=buffer_size)
        self.reconnections = 0
        self.last_sensor_time = None
        self.sensor_time_unwrapped = None
        self.timeline_sensor_origin = None
        self.timeline_host_origin = None

    def sample_monotonic(self, sensor_time, received_monotonic):
        if self.last_sensor_time is None:
            self.last_sensor_time = sensor_time
            self.sensor_time_unwrapped = sensor_time
            self.timeline_sensor_origin = sensor_time
            self.timeline_host_origin = received_monotonic
        else:
            delta = (sensor_time - self.last_sensor_time) & 0xFFFF
            if delta > 1000:
                # Certaines commandes de redémarrage du flux remettent
                # l'horloge 16 bits du capteur à zéro. Ce n'est pas un tour
                # complet de compteur: on recale alors la chronologie sur la
                # réception courante.
                self.last_sensor_time = sensor_time
                self.sensor_time_unwrapped = sensor_time
                self.timeline_sensor_origin = sensor_time
                self.timeline_host_origin = received_monotonic
                return received_monotonic
            self.sensor_time_unwrapped += delta
            self.last_sensor_time = sensor_time

        return self.timeline_host_origin + (
            self.sensor_time_unwrapped - self.timeline_sensor_origin
        ) / 1000.0

    def decode(self, value):
        raw_sample = parse_frame(value)
        if raw_sample is None:
            return None

        if self.offsets is None:
            if self.tare_started_at is None:
                self.tare_started_at = time.monotonic()
                print(
                    f"{self.side}: tare pendant {self.tare_duration:.1f} s, "
                    "laisser les deux plateformes vides."
                )
            self.tare_samples.append(
                {
                    "av_d": raw_sample["raw_av_d"],
                    "av_g": raw_sample["raw_av_g"],
                    "ar_g": raw_sample["raw_ar_g"],
                    "ar_d": raw_sample["raw_ar_d"],
                }
            )
            if time.monotonic() - self.tare_started_at >= self.tare_duration:
                self.offsets = {
                    key: round(median(sample[key] for sample in self.tare_samples))
                    for key in ("av_d", "av_g", "ar_g", "ar_d")
                }
                print(
                    f"{self.side}: tare terminée: "
                    + ", ".join(
                        f"{key.upper()}={value}"
                        for key, value in self.offsets.items()
                    )
                )

        if self.offsets is None:
            return None

        self.latest = parse_frame(value, self.offsets)
        self.distribution = (
            compute_distribution(self.latest)
            if self.latest["force_kg"] >= MIN_VALID_KG
            else None
        )
        received_monotonic = time.monotonic()
        self.samples.append(
            {
                "received_monotonic": received_monotonic,
                "sample_monotonic": self.sample_monotonic(
                    self.latest["t"], received_monotonic
                ),
                "received_utc": now_iso(),
                "sample": self.latest,
                "distribution": self.distribution,
            }
        )
        return self.latest


class DualKinventClient:
    def __init__(
        self,
        adapter,
        left_address,
        right_address,
        csv_path,
        tare_duration,
        print_interval,
        sync_tolerance_ms=20.0,
        calibration_path=None,
        recalibrate=False,
    ):
        self.adapter = adapter
        self.plates = [
            PlateState("gauche", left_address, tare_duration),
            PlateState("droite", right_address, tare_duration),
        ]
        self.calibration_path = Path(calibration_path) if calibration_path else None
        self.calibration_saved = False
        self.by_handle = {}
        self.sock = None
        self.fragments = {}
        self.pending_att = {}
        self.last_print = 0.0
        self.print_interval = print_interval
        self.sync_tolerance = sync_tolerance_ms / 1000.0
        # Les notifications entretiennent naturellement la liaison. Les
        # commandes envoyées pendant les mesures provoquent des déconnexions
        # 0x08 sur ces plateformes.
        self.keepalive_interval = None
        self.next_keepalive = None
        self.paired_samples = 0
        self.dropped_samples = {"gauche": 0, "droite": 0}
        self.csv_file = None
        self.writer = None
        self.reconnect_not_before = 0.0

        if not recalibrate:
            self.load_calibration()

        if csv_path:
            self.open_csv(csv_path)

    def open_csv(self, csv_path):
        self.close_csv()
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_file = path.open("w", newline="", encoding="utf-8")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(CSV_FIELDS)
        self.csv_file.flush()

    def close_csv(self):
        if self.csv_file:
            self.csv_file.close()
        self.csv_file = None
        self.writer = None

    def wait_for_reconnect_cooldown(self):
        remaining = self.reconnect_not_before - time.monotonic()
        if remaining > 0:
            print(
                f"Repos Bluetooth pendant {remaining:.1f} s avant reconnexion..."
            )
            time.sleep(remaining)

    def load_calibration(self):
        if self.calibration_path is None or not self.calibration_path.exists():
            return False
        try:
            data = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            for plate in self.plates:
                saved = data.get(plate.side, {})
                offsets = saved.get("offsets", {})
                if saved.get("address") != plate.address or set(offsets) != {
                    "av_d",
                    "av_g",
                    "ar_g",
                    "ar_d",
                }:
                    return False
            for plate in self.plates:
                plate.offsets = {
                    key: int(value)
                    for key, value in data[plate.side]["offsets"].items()
                }
            self.calibration_saved = True
            print(f"Tare existante chargée depuis {self.calibration_path}.")
            return True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            print("Tare enregistrée invalide; une nouvelle tare sera effectuée.")
            return False

    def save_calibration(self):
        if self.calibration_saved or self.calibration_path is None:
            return
        if any(plate.offsets is None for plate in self.plates):
            return
        data = {
            plate.side: {"address": plate.address, "offsets": plate.offsets}
            for plate in self.plates
        }
        self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.calibration_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.calibration_path)
        self.calibration_saved = True
        print(f"Tare enregistrée dans {self.calibration_path}.")

    def open(self):
        if sys.platform != "linux":
            raise SystemExit("Ce script doit être exécuté sous Linux.")
        self.adapter = resolve_hci_adapter(self.adapter)
        self.sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
        try:
            import ctypes

            address = SockaddrHci(AF_BLUETOOTH, self.adapter, HCI_CHANNEL_USER)
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.bind(
                self.sock.fileno(), ctypes.byref(address), ctypes.sizeof(address)
            ):
                error_number = ctypes.get_errno()
                raise OSError(error_number, errno.errorcode.get(error_number))
        except OSError as exc:
            self.sock.close()
            raise SystemExit(
                f"Impossible de réserver hci{self.adapter}: {exc}. "
                "Arrête bluetooth.service et place le contrôleur DOWN."
            ) from exc
        self.sock.settimeout(0.1)
        print(f"Canal HCI double ouvert sur hci{self.adapter}.")

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
        self.close_csv()

    def send_command(self, ogf, ocf, parameters=b""):
        opcode = hci_opcode(ogf, ocf)
        self.sock.sendall(
            struct.pack("<BHB", HCI_COMMAND_PKT, opcode, len(parameters)) + parameters
        )
        return opcode

    def wait_for_command(self, opcode, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packet = self.receive()
            if packet is None:
                continue
            packet_type, payload = packet
            if packet_type == HCI_EVENT_PKT and len(payload) >= 2:
                params = payload[2:]
                if payload[0] == EVT_CMD_COMPLETE and len(params) >= 3:
                    completed = struct.unpack_from("<H", params, 1)[0]
                    if completed == opcode:
                        status = params[3] if len(params) > 3 else 0
                        if status:
                            raise RuntimeError(
                                f"Commande HCI 0x{opcode:04x}: 0x{status:02x}"
                            )
                        return
                if payload[0] == EVT_CMD_STATUS and len(params) >= 4:
                    completed = struct.unpack_from("<H", params, 2)[0]
                    if completed == opcode:
                        if params[0]:
                            raise RuntimeError(
                                f"Commande HCI 0x{opcode:04x}: 0x{params[0]:02x}"
                            )
                        return
            self.process(packet)
        raise TimeoutError(f"Pas de réponse HCI 0x{opcode:04x}")

    def reset(self):
        print("Réinitialisation du contrôleur...")
        opcode = self.send_command(OGF_HOST_CTL, OCF_RESET)
        self.wait_for_command(opcode, timeout=5.0)

        # Les contrôleurs Realtek peuvent ignorer la première commande envoyée
        # immédiatement après HCI Reset, le temps que le firmware se stabilise.
        time.sleep(0.3)

        for ogf, ocf, params in [
            (
                OGF_HOST_CTL,
                OCF_SET_EVENT_MASK,
                bytes.fromhex("ff ff fb ff 07 f8 bf 3d"),
            ),
            (
                OGF_LE_CTL,
                OCF_LE_SET_EVENT_MASK,
                bytes.fromhex("ff 07 00 00 00 00 00 00"),
            ),
        ]:
            last_error = None
            for attempt in range(1, 4):
                opcode = self.send_command(ogf, ocf, params)
                try:
                    self.wait_for_command(opcode, timeout=5.0)
                    break
                except TimeoutError as exc:
                    last_error = exc
                    print(
                        f"Commande HCI 0x{opcode:04x}: "
                        f"pas de réponse, essai {attempt}/3."
                    )
                    time.sleep(0.3)
            else:
                raise last_error
            opcode = self.send_command(ogf, ocf, params)
            self.wait_for_command(opcode)

    def receive(self):
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return None
        return (data[0], data[1:]) if data else None

    def scan_for(self, plate, timeout):
        print(f"Recherche de la plateforme {plate.side}: {plate.address}")
        scan_params = struct.pack("<BHHBB", 1, 0x0010, 0x0010, 0, 0)
        opcode = self.send_command(OGF_LE_CTL, OCF_LE_SET_SCAN_PARAMETERS, scan_params)
        self.wait_for_command(opcode)
        opcode = self.send_command(OGF_LE_CTL, OCF_LE_SET_SCAN_ENABLE, b"\x01\x00")
        self.wait_for_command(opcode)
        found = False
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline and not found:
                packet = self.receive()
                if packet is None:
                    continue
                packet_type, payload = packet
                if (
                    packet_type == HCI_EVENT_PKT
                    and len(payload) >= 4
                    and payload[0] == EVT_LE_META_EVENT
                    and payload[2] == EVT_LE_ADVERTISING_REPORT
                ):
                    count = payload[3]
                    offset = 4
                    for _ in range(count):
                        if offset + 10 > len(payload):
                            break
                        address = le_bytes_to_address(payload[offset + 2 : offset + 8])
                        data_length = payload[offset + 8]
                        if address == plate.address:
                            found = True
                            break
                        offset += 10 + data_length
                else:
                    self.process(packet)
        finally:
            opcode = self.send_command(OGF_LE_CTL, OCF_LE_SET_SCAN_ENABLE, b"\x00\x00")
            self.wait_for_command(opcode)
        if not found:
            raise TimeoutError(f"Plateforme {plate.side} introuvable")

    def connect(self, plate, timeout):
        params = struct.pack(
            "<HHBB6sBHHHHHH",
            0x0010,
            0x0010,
            0,
            0,
            plate.address_le,
            0,
            0x000C,
            0x0018,
            0,
            0x07D0,
            0,
            0,
        )
        opcode = self.send_command(OGF_LE_CTL, OCF_LE_CREATE_CONN, params)
        self.wait_for_command(opcode)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packet = self.receive()
            if packet is None:
                continue
            packet_type, payload = packet
            if (
                packet_type == HCI_EVENT_PKT
                and len(payload) >= 15
                and payload[0] == EVT_LE_META_EVENT
                and payload[2]
                in (EVT_LE_CONN_COMPLETE, EVT_LE_ENHANCED_CONN_COMPLETE)
            ):
                peer = le_bytes_to_address(payload[8:14])
                if peer == plate.address:
                    if payload[3]:
                        raise RuntimeError(
                            f"Connexion {plate.side}: statut 0x{payload[3]:02x}"
                        )
                    plate.handle = struct.unpack_from("<H", payload, 4)[0] & 0x0FFF
                    self.by_handle[plate.handle] = plate
                    print(
                        f"Plateforme {plate.side} connectée, "
                        f"handle 0x{plate.handle:04x}."
                    )
                    return
            self.process(packet)
        raise TimeoutError(f"Connexion {plate.side}: délai dépassé")

    def send_att(self, plate, payload):
        l2cap = struct.pack("<HH", len(payload), ATT_CID) + payload
        self.sock.sendall(
            struct.pack("<BHH", HCI_ACLDATA_PKT, plate.handle, len(l2cap)) + l2cap
        )

    def send_write_command(self, plate, value):
        self.send_att(
            plate,
            bytes([ATT_OP_WRITE_COMMAND])
            + struct.pack("<H", UART_VALUE_HANDLE)
            + value,
        )

    def wait_write_response(self, plate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            queued = self.pending_att.get(plate.handle, [])
            if queued:
                att = queued.pop(0)
                if att[0] == ATT_OP_WRITE_RESPONSE:
                    return
                if att[0] == ATT_OP_ERROR_RESPONSE:
                    raise RuntimeError(
                        f"Écriture {plate.side} refusée: {att.hex(' ')}"
                    )
            packet = self.receive()
            if packet:
                self.process(packet)
        raise TimeoutError(f"Pas de réponse CCCD pour {plate.side}")

    def start_stream(self, plate, write_delay):
        self.send_write_command(plate, b"\x10")
        self.pump(write_delay)
        self.send_att(
            plate,
            bytes([ATT_OP_WRITE_REQUEST])
            + struct.pack("<H", UART_CCCD_HANDLE)
            + b"\x01\x00",
        )
        self.wait_write_response(plate)
        for command in INIT_COMMANDS:
            self.send_write_command(plate, command)
            self.pump(write_delay)
        print(f"Flux {plate.side} démarré.")

    def extract_att(self, payload):
        if len(payload) < 4:
            return None, None
        handle_flags, acl_length = struct.unpack_from("<HH", payload, 0)
        handle = handle_flags & 0x0FFF
        pb = (handle_flags >> 12) & 3
        fragment = payload[4 : 4 + acl_length]
        if pb in (0, 2):
            if len(fragment) < 4:
                return None, None
            length, cid = struct.unpack_from("<HH", fragment, 0)
            self.fragments[handle] = [length, cid, bytearray(fragment[4:])]
        elif pb == 1 and handle in self.fragments:
            self.fragments[handle][2].extend(fragment)
        else:
            return None, None
        length, cid, data = self.fragments[handle]
        if len(data) < length:
            return None, None
        del self.fragments[handle]
        return (handle, bytes(data[:length])) if cid == ATT_CID else (None, None)

    def process(self, packet):
        packet_type, payload = packet
        if packet_type == HCI_ACLDATA_PKT:
            handle, att = self.extract_att(payload)
            if handle is not None and att:
                self.handle_att(handle, att)
            return
        if packet_type != HCI_EVENT_PKT or len(payload) < 2:
            return
        if payload[0] == EVT_DISCONN_COMPLETE and len(payload) >= 6:
            handle = struct.unpack_from("<H", payload, 3)[0] & 0x0FFF
            plate = self.by_handle.get(handle)
            if plate:
                self.by_handle.pop(handle, None)
                plate.handle = None
                plate.samples.clear()
                plate.last_sensor_time = None
                plate.sensor_time_unwrapped = None
                plate.timeline_sensor_origin = None
                plate.timeline_host_origin = None
                raise PlateDisconnected(plate, payload[5])
        if (
            payload[0] == EVT_LE_META_EVENT
            and len(payload) >= 13
            and payload[2] == 0x06
        ):
            handle = struct.unpack_from("<H", payload, 3)[0] & 0x0FFF
            if handle in self.by_handle:
                values = struct.unpack_from("<HHHH", payload, 5)
                response = struct.pack(
                    "<HHHHHHH", handle, *values, 0x0000, 0x0000
                )
                self.send_command(
                    OGF_LE_CTL, OCF_LE_REMOTE_CONN_PARAM_REQ_REPLY, response
                )

    def handle_att(self, handle, att):
        plate = self.by_handle.get(handle)
        if plate is None:
            return
        opcode = att[0]
        if opcode == ATT_OP_MTU_REQUEST and len(att) >= 3:
            requested = struct.unpack_from("<H", att, 1)[0]
            self.send_att(
                plate,
                bytes([ATT_OP_MTU_RESPONSE])
                + struct.pack("<H", min(requested, 158)),
            )
            return
        if opcode == ATT_OP_FIND_BY_TYPE_VALUE_REQUEST and len(att) >= 5:
            start = struct.unpack_from("<H", att, 1)[0]
            self.send_att(
                plate,
                bytes(
                    [
                        ATT_OP_ERROR_RESPONSE,
                        ATT_OP_FIND_BY_TYPE_VALUE_REQUEST,
                    ]
                )
                + struct.pack("<H", start)
                + b"\x0a",
            )
            return
        if opcode in (ATT_OP_WRITE_RESPONSE, ATT_OP_ERROR_RESPONSE):
            self.pending_att.setdefault(handle, []).append(att)
            return
        if opcode == ATT_OP_NOTIFICATION and len(att) >= 3:
            plate.notifications += 1
            plate.last_notification_at = time.monotonic()
            value = att[3:]
            sample = plate.decode(value)
            if sample:
                self.pair_samples()

    def combined_values(self, left_entry=None, right_entry=None):
        left, right = self.plates
        left_sample = left_entry["sample"] if left_entry else left.latest
        right_sample = right_entry["sample"] if right_entry else right.latest
        left_distribution = (
            left_entry["distribution"] if left_entry else left.distribution
        )
        right_distribution = (
            right_entry["distribution"] if right_entry else right.distribution
        )
        if left_sample is None or right_sample is None:
            return None
        left_kg = max(0.0, left_sample["force_kg"])
        right_kg = max(0.0, right_sample["force_kg"])
        total_kg = left_kg + right_kg
        left_pct = left_kg / total_kg * 100 if total_kg >= MIN_VALID_KG else None
        right_pct = right_kg / total_kg * 100 if total_kg >= MIN_VALID_KG else None
        asymmetry = right_pct - left_pct if left_pct is not None else None

        # Repère global normalisé: centres des plaques à -1 (gauche) et +1
        # (droite), COP intra-plaque ramené sur une demi-largeur.
        if total_kg >= MIN_VALID_KG:
            left_x = -1.0 + (
                left_distribution["cop_x"] * 0.5 if left_distribution else 0
            )
            right_x = 1.0 + (
                right_distribution["cop_x"] * 0.5 if right_distribution else 0
            )
            global_x = (left_x * left_kg + right_x * right_kg) / total_kg
            left_y = left_distribution["cop_y"] if left_distribution else 0
            right_y = right_distribution["cop_y"] if right_distribution else 0
            global_y = (left_y * left_kg + right_y * right_kg) / total_kg
        else:
            global_x = global_y = None

        return {
            "left_kg": left_kg,
            "right_kg": right_kg,
            "total_kg": total_kg,
            "left_pct": left_pct,
            "right_pct": right_pct,
            "asymmetry": asymmetry,
            "global_x": global_x,
            "global_y": global_y,
        }

    def pair_samples(self):
        left, right = self.plates
        self.save_calibration()
        while left.samples and right.samples:
            left_entry = left.samples[0]
            right_entry = right.samples[0]
            delta = (
                left_entry["sample_monotonic"]
                - right_entry["sample_monotonic"]
            )
            if abs(delta) <= self.sync_tolerance:
                left.samples.popleft()
                right.samples.popleft()
                self.write_combined(left_entry, right_entry, abs(delta) * 1000)
                self.paired_samples += 1
            elif delta < 0:
                left.samples.popleft()
                self.dropped_samples["gauche"] += 1
            else:
                right.samples.popleft()
                self.dropped_samples["droite"] += 1

    def write_combined(self, left_entry, right_entry, sync_delta_ms):
        values = self.combined_values(left_entry, right_entry)
        if values is None:
            return
        left_dist = left_entry["distribution"] or {}
        right_dist = right_entry["distribution"] or {}
        left_sample = left_entry["sample"]
        right_sample = right_entry["sample"]
        sync_quality = "excellent" if sync_delta_ms <= 10 else "acceptable"
        if self.writer:
            self.writer.writerow(
                [
                    max(left_entry["received_utc"], right_entry["received_utc"]),
                    round(sync_delta_ms, 3),
                    sync_quality,
                    left_sample["t"],
                    right_sample["t"],
                    round(values["left_kg"], 6),
                    round(values["right_kg"], 6),
                    round(values["total_kg"], 6),
                    round(values["left_kg"] * 9.81, 6),
                    round(values["right_kg"] * 9.81, 6),
                    round(values["total_kg"] * 9.81, 6),
                    round(values["left_pct"], 6)
                    if values["left_pct"] is not None
                    else "",
                    round(values["right_pct"], 6)
                    if values["right_pct"] is not None
                    else "",
                    round(values["asymmetry"], 6)
                    if values["asymmetry"] is not None
                    else "",
                    left_dist.get("cop_x", ""),
                    left_dist.get("cop_y", ""),
                    right_dist.get("cop_x", ""),
                    right_dist.get("cop_y", ""),
                    round(values["global_x"], 6)
                    if values["global_x"] is not None
                    else "",
                    round(values["global_y"], 6)
                    if values["global_y"] is not None
                    else "",
                ]
            )
            self.csv_file.flush()
        if time.monotonic() - self.last_print >= self.print_interval:
            if values["total_kg"] >= MIN_VALID_KG:
                print(
                    f"G={values['left_kg']:.1f} kg "
                    f"({values['left_pct']:.1f}%) | "
                    f"D={values['right_kg']:.1f} kg "
                    f"({values['right_pct']:.1f}%) | "
                    f"TOTAL={values['total_kg']:.1f} kg | "
                    f"ASYM={values['asymmetry']:+.1f}%"
                )
            else:
                print("Deux plateformes: hors appui")
            self.last_print = time.monotonic()

    def pump(
        self,
        duration,
        progress=False,
        show_progress=True,
        stop_requested=None,
    ):
        deadline = time.monotonic() + duration
        next_progress = time.monotonic()
        if progress:
            self.next_keepalive = None
        while time.monotonic() < deadline:
            if stop_requested is not None and stop_requested():
                return False
            packet = self.receive()
            if packet:
                try:
                    self.process(packet)
                except PlateDisconnected as exc:
                    if not progress:
                        raise
                    raise RuntimeError(
                        f"{exc}. Le test est arrêté pour protéger la session "
                        "Bluetooth."
                    ) from exc
            if (
                progress
                and self.keepalive_interval is not None
                and self.next_keepalive is not None
                and time.monotonic() >= self.next_keepalive
            ):
                for plate in self.plates:
                    if plate.handle is not None:
                        self.send_write_command(plate, b"\xff")
                self.next_keepalive = time.monotonic() + self.keepalive_interval
            if progress and show_progress and time.monotonic() >= next_progress:
                print(f"Temps restant: {max(0, deadline-time.monotonic()):.1f} s")
                next_progress = time.monotonic() + 5
        return True

    def reconnect_plate(self, plate, acquisition_deadline, attempts=4):
        last_error = None
        for attempt in range(1, attempts + 1):
            remaining = acquisition_deadline - time.monotonic()
            if remaining <= 1:
                break
            try:
                print(
                    f"Reconnexion {plate.side}, essai {attempt}/{attempts}..."
                )
                self.scan_for(plate, min(5.0, remaining))
                self.connect(plate, min(8.0, remaining))
                self.pump(min(0.8, max(0.0, remaining)))
                self.start_stream(plate, 0.5)
                plate.reconnections += 1
                print(f"Plateforme {plate.side} reconnectée.")
                return
            except (PlateDisconnected, TimeoutError, RuntimeError) as exc:
                last_error = exc
                if plate.handle is not None:
                    self.by_handle.pop(plate.handle, None)
                    plate.handle = None
                time.sleep(0.5)
        raise RuntimeError(
            f"Reconnexion impossible pour la plateforme {plate.side}."
        ) from last_error

    def connect_and_start_plate(self, plate, scan_timeout, connect_timeout, write_delay):
        last_error = None
        for attempt in range(1, 5):
            try:
                if attempt > 1:
                    print(
                        f"Initialisation {plate.side}, "
                        f"nouvel essai {attempt}/4..."
                    )
                self.scan_for(plate, scan_timeout)
                self.connect(plate, connect_timeout)
                self.pump(0.8)
                self.start_stream(plate, write_delay)
                return
            except (PlateDisconnected, TimeoutError, RuntimeError) as exc:
                last_error = exc
                print(f"Initialisation {plate.side} interrompue: {exc}")
                if plate.handle is not None:
                    self.by_handle.pop(plate.handle, None)
                    plate.handle = None
                plate.samples.clear()
                time.sleep(0.5)
        raise RuntimeError(
            f"Connexion initiale impossible pour la plateforme {plate.side}."
        ) from last_error

    def ensure_streams_ready(self, attempts=3):
        missing = list(self.plates)
        for attempt in range(1, attempts + 1):
            before = {plate.side: plate.notifications for plate in self.plates}
            self.pump(1.5)
            missing = [
                plate
                for plate in self.plates
                if plate.notifications <= before[plate.side]
            ]
            if not missing:
                print("Les deux flux de mesure sont actifs.")
                return
            print(
                "Flux sans mesure: "
                + ", ".join(plate.side for plate in missing)
                + f" (vérification {attempt}/{attempts})."
            )
            for plate in missing:
                self.start_stream(plate, 0.1)
                plate.stream_restarts += 1
        raise RuntimeError(
            "Les deux plateformes sont connectées, mais un flux de mesure "
            "ne démarre pas."
        )

    def clear_connection_state(self):
        self.by_handle.clear()
        self.fragments.clear()
        self.pending_att.clear()
        for plate in self.plates:
            plate.handle = None
            plate.samples.clear()
            plate.last_notification_at = None
            plate.last_sensor_time = None
            plate.sensor_time_unwrapped = None
            plate.timeline_sensor_origin = None
            plate.timeline_host_origin = None

    def disconnect_all(self, timeout=3.0):
        connected = [plate for plate in self.plates if plate.handle is not None]
        if not connected or self.sock is None:
            return
        print("Déconnexion propre des plateformes...")
        for plate in connected:
            handle = plate.handle
            try:
                opcode = self.send_command(
                    OGF_LINK_CTL,
                    OCF_DISCONNECT,
                    struct.pack("<HB", handle, 0x13),
                )
                self.wait_for_command(opcode, timeout=1.5)
            except (OSError, RuntimeError, TimeoutError, PlateDisconnected):
                pass

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and any(
            plate.handle is not None for plate in connected
        ):
            packet = self.receive()
            if packet is None:
                continue
            try:
                self.process(packet)
            except PlateDisconnected as exc:
                print(f"Plateforme {exc.plate.side} déconnectée proprement.")

        for plate in connected:
            if plate.handle is not None:
                self.by_handle.pop(plate.handle, None)
                plate.handle = None
        time.sleep(1.0)

    def initialize_session(
        self,
        scan_timeout,
        connect_timeout,
        write_delay,
        attempts=3,
    ):
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                if attempt > 1:
                    print(
                        f"Réinitialisation complète de la session, "
                        f"essai {attempt}/{attempts}..."
                    )
                self.reset()
                self.clear_connection_state()
                for plate in self.plates:
                    self.connect_and_start_plate(
                        plate,
                        scan_timeout,
                        connect_timeout,
                        write_delay,
                    )
                self.ensure_streams_ready()
                return
            except (PlateDisconnected, TimeoutError, RuntimeError) as exc:
                last_error = exc
                print(f"Session Bluetooth incomplète: {exc}")
                self.disconnect_all()
                self.clear_connection_state()
                time.sleep(0.8)
        raise RuntimeError(
            "Impossible d'activer simultanément les deux plateformes."
        ) from last_error

    def run(self, scan_timeout, connect_timeout, duration, write_delay):
        self.open()
        try:
            self.initialize_session(
                scan_timeout,
                connect_timeout,
                write_delay,
            )
            print(f"Acquisition double pendant {duration:.1f} s...")
            self.pump(duration, progress=True)
            print("Acquisition double terminée.")
            for plate in self.plates:
                print(
                    f"{plate.side}: {plate.notifications} notifications, "
                    f"{plate.reconnections} reconnexion(s), "
                    f"{plate.stream_restarts} relance(s) de flux"
                )
            if self.paired_samples == 0:
                raise RuntimeError(
                    "Aucune mesure synchronisée reçue des deux plateformes."
                )
            print(
                f"Paires synchronisées: {self.paired_samples} | "
                f"écartées gauche={self.dropped_samples['gauche']}, "
                f"droite={self.dropped_samples['droite']}"
            )
        finally:
            if self.sock is not None:
                try:
                    self.disconnect_all()
                except (OSError, RuntimeError, TimeoutError, PlateDisconnected):
                    pass
            self.close()

    @staticmethod
    def write_worker_state(path, **state):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"updated_at": now_iso(), "pid": os.getpid(), **state},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(target)

    def run_persistent(
        self,
        scan_timeout,
        connect_timeout,
        write_delay,
        control_file,
        state_file,
    ):
        control_path = Path(control_file)
        generation = None
        active_generation = None

        def read_command():
            try:
                return json.loads(control_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                return {}

        generation = read_command().get("generation")
        try:
            self.write_worker_state(state_file, phase="disconnected")
            while True:
                command = read_command()
                action = command.get("action")
                requested = command.get("generation")

                if self.sock is None:
                    if (
                        action == "connect"
                        and requested
                        and requested != generation
                    ):
                        generation = requested
                        self.write_worker_state(
                            state_file,
                            phase="connecting",
                            generation=generation,
                        )
                        try:
                            self.wait_for_reconnect_cooldown()
                            self.open()
                            self.initialize_session(
                                scan_timeout,
                                connect_timeout,
                                write_delay,
                                attempts=1,
                            )
                        except (
                            OSError,
                            PlateDisconnected,
                            TimeoutError,
                            RuntimeError,
                            SystemExit,
                        ) as exc:
                            self.disconnect_all()
                            self.clear_connection_state()
                            self.close()
                            self.reconnect_not_before = time.monotonic() + 10.0
                            self.write_worker_state(
                                state_file,
                                phase="disconnected",
                                generation=generation,
                                error=(
                                    "Connexion impossible. Attendez quelques "
                                    f"secondes puis réessayez : {exc}"
                                ),
                            )
                        else:
                            self.write_worker_state(
                                state_file,
                                phase="idle",
                                generation=generation,
                            )
                    else:
                        time.sleep(0.25)
                    continue

                if action == "disconnect":
                    self.disconnect_all()
                    self.clear_connection_state()
                    self.close()
                    self.reconnect_not_before = time.monotonic() + 10.0
                    generation = requested or generation
                    self.write_worker_state(
                        state_file,
                        phase="disconnected",
                        generation=generation,
                    )
                    continue
                if (
                    action == "start"
                    and requested
                    and requested != generation
                ):
                    generation = requested
                    active_generation = generation
                    duration = float(command["duration"])
                    if command.get("recalibrate"):
                        print("Nouvelle tare demandée.")
                        self.calibration_saved = False
                        if self.calibration_path:
                            try:
                                self.calibration_path.unlink()
                            except FileNotFoundError:
                                pass
                        for plate in self.plates:
                            plate.offsets = None
                            plate.tare_started_at = None
                            plate.tare_samples = []
                        tare_deadline = time.monotonic() + 15.0
                        while (
                            time.monotonic() < tare_deadline
                            and any(plate.offsets is None for plate in self.plates)
                        ):
                            self.pump(0.25)
                        self.save_calibration()
                        if any(plate.offsets is None for plate in self.plates):
                            raise RuntimeError(
                                "La nouvelle tare n'a pas pu être terminée."
                            )
                    if any(plate.handle is None for plate in self.plates):
                        raise RuntimeError(
                            "Une plateforme est déconnectée. Utilisez le "
                            "bouton « Connecter les capteurs »."
                        )
                    self.ensure_streams_ready()
                    self.paired_samples = 0
                    self.dropped_samples = {"gauche": 0, "droite": 0}
                    for plate in self.plates:
                        plate.samples.clear()
                    self.open_csv(command["csv_path"])
                    self.write_worker_state(
                        state_file,
                        phase="active",
                        generation=generation,
                        csv_path=command["csv_path"],
                        started_at=now_iso(),
                    )
                    try:
                        completed = self.pump(
                            duration,
                            progress=True,
                            stop_requested=lambda: (
                                read_command().get("action") == "stop"
                                and read_command().get("generation")
                                == active_generation
                            ),
                        )
                    except RuntimeError as exc:
                        self.close_csv()
                        self.disconnect_all()
                        self.clear_connection_state()
                        self.close()
                        self.write_worker_state(
                            state_file,
                            phase="disconnected",
                            generation=generation,
                            csv_path=command["csv_path"],
                            error=str(exc),
                        )
                        active_generation = None
                        continue
                    self.close_csv()
                    if self.paired_samples == 0:
                        self.write_worker_state(
                            state_file,
                            phase="error",
                            generation=generation,
                            csv_path=command["csv_path"],
                            paired_samples=0,
                            error=(
                                "Aucune mesure synchronisée reçue des deux "
                                "plateformes."
                            ),
                        )
                    else:
                        self.write_worker_state(
                            state_file,
                            phase="idle",
                            generation=generation,
                            csv_path=command["csv_path"],
                            paired_samples=self.paired_samples,
                            stopped=not completed,
                        )
                    active_generation = None
                try:
                    self.pump(1.0, progress=True, show_progress=False)
                except (PlateDisconnected, TimeoutError, RuntimeError) as exc:
                    print(f"Session inactive interrompue: {exc}")
                    self.disconnect_all()
                    self.clear_connection_state()
                    self.close()
                    self.write_worker_state(
                        state_file,
                        phase="disconnected",
                        generation=generation,
                        error=str(exc),
                    )
        except Exception as exc:
            self.write_worker_state(
                state_file,
                phase="error",
                generation=generation,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            self.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Acquisition simultanée de deux K-Force Plates via HCI direct."
    )
    parser.add_argument("--adapter", type=parse_adapter, default=1)
    parser.add_argument("--left-address", default=KPLATE_LEFT)
    parser.add_argument("--right-address", default=KPLATE_RIGHT)
    parser.add_argument("--scan-timeout", type=float, default=15.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--write-delay", type=float, default=0.5)
    parser.add_argument("--tare-duration", type=float, default=2.0)
    parser.add_argument("--calibration-file")
    parser.add_argument("--recalibrate", action="store_true")
    parser.add_argument("--print-interval", type=float, default=0.5)
    parser.add_argument(
        "--sync-tolerance-ms",
        type=float,
        default=20.0,
        help="Écart maximal entre une mesure gauche et droite.",
    )
    parser.add_argument("--csv", default="storage/raw_data/kplates_dual.csv")
    parser.add_argument("--control-file")
    parser.add_argument("--state-file")
    return parser


def main():
    args = build_parser().parse_args()
    csv_path = None if args.control_file and args.state_file else args.csv
    client = DualKinventClient(
        args.adapter,
        args.left_address,
        args.right_address,
        csv_path,
        args.tare_duration,
        args.print_interval,
        args.sync_tolerance_ms,
        args.calibration_file,
        args.recalibrate,
    )
    if args.control_file and args.state_file:
        client.run_persistent(
            args.scan_timeout,
            args.connect_timeout,
            args.write_delay,
            args.control_file,
            args.state_file,
        )
    else:
        client.run(
            args.scan_timeout,
            args.connect_timeout,
            args.duration,
            args.write_delay,
        )


if __name__ == "__main__":
    main()
