"""Client Kinvent BLE direct, sans pile GATT BlueZ.

Ce prototype réserve un contrôleur Bluetooth via le canal HCI_USER, établit la
connexion LE et échange directement des paquets ATT sur le CID fixe 0x0004.
Il est destiné aux K-Force Plates/K-Push dont les handles correspondent à la
capture Android du 15 juin 2026.

Exécution Linux (root requis) :

    sudo .venv/bin/python scripts/kinvent_raw_hci.py \
        --adapter hci1 --address E8:EB:1B:6F:A7:5F
"""

import argparse
import ctypes
import csv
import errno
import socket
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


AF_BLUETOOTH = getattr(socket, "AF_BLUETOOTH", 31)
BTPROTO_HCI = getattr(socket, "BTPROTO_HCI", 1)
HCI_CHANNEL_USER = 1

HCI_COMMAND_PKT = 0x01
HCI_ACLDATA_PKT = 0x02
HCI_EVENT_PKT = 0x04

EVT_DISCONN_COMPLETE = 0x05
EVT_CMD_COMPLETE = 0x0E
EVT_CMD_STATUS = 0x0F
EVT_NUM_COMPLETED_PACKETS = 0x13
EVT_LE_META_EVENT = 0x3E

EVT_LE_CONN_COMPLETE = 0x01
EVT_LE_ADVERTISING_REPORT = 0x02
EVT_LE_ENHANCED_CONN_COMPLETE = 0x0A

OGF_HOST_CTL = 0x03
OGF_LE_CTL = 0x08

OCF_SET_EVENT_MASK = 0x0001
OCF_RESET = 0x0003
OCF_LE_SET_EVENT_MASK = 0x0001
OCF_LE_SET_SCAN_PARAMETERS = 0x000B
OCF_LE_SET_SCAN_ENABLE = 0x000C
OCF_LE_CREATE_CONN = 0x000D
OCF_LE_REMOTE_CONN_PARAM_REQ_REPLY = 0x0020

ATT_CID = 0x0004
ATT_OP_ERROR_RESPONSE = 0x01
ATT_OP_MTU_REQUEST = 0x02
ATT_OP_MTU_RESPONSE = 0x03
ATT_OP_FIND_BY_TYPE_VALUE_REQUEST = 0x06
ATT_OP_WRITE_REQUEST = 0x12
ATT_OP_WRITE_RESPONSE = 0x13
ATT_OP_NOTIFICATION = 0x1B
ATT_OP_WRITE_COMMAND = 0x52

UART_VALUE_HANDLE = 0x0052
UART_CCCD_HANDLE = 0x0053
ALT_NOTIFY_CCCD_HANDLE = 0x0058

INIT_COMMANDS = [
    b"\x10",
    b"\x09",
    b"\x21",
    b"\x76",
    b"\x11",
    b"\x10",
    b"\x10",
    b"\x56",
    bytes.fromhex("ac 00 54 f8"),
    b"\x11",
]


class SockaddrHci(ctypes.Structure):
    _fields_ = [
        ("hci_family", ctypes.c_ushort),
        ("hci_dev", ctypes.c_ushort),
        ("hci_channel", ctypes.c_ushort),
    ]


def hci_opcode(ogf, ocf):
    return (ogf << 10) | ocf


def parse_adapter(value):
    value = value.lower()
    if value.startswith("hci"):
        value = value[3:]
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Adaptateur attendu: hci0, hci1...") from exc


def address_to_le_bytes(address):
    parts = address.split(":")
    if len(parts) != 6:
        raise argparse.ArgumentTypeError(f"Adresse Bluetooth invalide: {address}")
    try:
        return bytes(int(part, 16) for part in reversed(parts))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Adresse Bluetooth invalide: {address}"
        ) from exc


def le_bytes_to_address(value):
    return ":".join(f"{byte:02X}" for byte in reversed(value))


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class RawKinventClient:
    def __init__(self, adapter, address, address_type, csv_path=None):
        self.adapter = adapter
        self.address = address.upper()
        self.address_le = address_to_le_bytes(address)
        self.address_type = 0 if address_type == "public" else 1
        self.sock = None
        self.connection_handle = None
        self.acl_fragments = {}
        self.csv_file = None
        self.csv_writer = None
        self.notifications = 0

        if csv_path:
            path = Path(csv_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.csv_file = path.open("w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(
                ["timestamp_utc", "value_handle", "length", "data_hex"]
            )

    def open(self):
        if sys.platform != "linux":
            raise SystemExit("Ce script HCI direct doit être exécuté sous Linux.")

        self.sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
        try:
            address = SockaddrHci(
                AF_BLUETOOTH,
                self.adapter,
                HCI_CHANNEL_USER,
            )
            libc = ctypes.CDLL(None, use_errno=True)
            result = libc.bind(
                self.sock.fileno(),
                ctypes.byref(address),
                ctypes.sizeof(address),
            )
            if result != 0:
                error_number = ctypes.get_errno()
                raise OSError(
                    error_number,
                    f"bind HCI_USER hci{self.adapter}: "
                    f"{errno.errorcode.get(error_number, 'erreur inconnue')}",
                )
        except OSError as exc:
            self.sock.close()
            self.sock = None
            if exc.errno in (errno.EBUSY, errno.EPERM, errno.EACCES):
                raise SystemExit(
                    f"Impossible de réserver hci{self.adapter}: {exc}.\n"
                    "Utilise une clé dédiée, lance le script avec sudo et mets "
                    "ce contrôleur hors tension dans BlueZ avant le test."
                ) from exc
            raise

        self.sock.settimeout(0.2)
        print(f"Canal HCI direct ouvert sur hci{self.adapter}.")

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        if self.csv_file is not None:
            self.csv_file.close()
            self.csv_file = None

    def send_command(self, ogf, ocf, parameters=b""):
        opcode = hci_opcode(ogf, ocf)
        packet = struct.pack("<BHB", HCI_COMMAND_PKT, opcode, len(parameters))
        self.sock.sendall(packet + parameters)
        return opcode

    def reset(self):
        print("Réinitialisation du contrôleur...")
        opcode = self.send_command(OGF_HOST_CTL, OCF_RESET)
        self.wait_for_command(opcode, 3.0)
        opcode = self.send_command(
            OGF_HOST_CTL,
            OCF_SET_EVENT_MASK,
            bytes.fromhex("ff ff fb ff 07 f8 bf 3d"),
        )
        self.wait_for_command(opcode, 3.0)
        opcode = self.send_command(
            OGF_LE_CTL,
            OCF_LE_SET_EVENT_MASK,
            bytes.fromhex("ff 07 00 00 00 00 00 00"),
        )
        self.wait_for_command(opcode, 3.0)

    def wait_for_command(self, opcode, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packet = self.receive_packet()
            if packet is None:
                continue
            packet_type, payload = packet
            if packet_type != HCI_EVENT_PKT or len(payload) < 2:
                continue
            event_code = payload[0]
            parameters = payload[2:]
            if event_code == EVT_CMD_COMPLETE and len(parameters) >= 3:
                completed_opcode = struct.unpack_from("<H", parameters, 1)[0]
                if completed_opcode == opcode:
                    status = parameters[3] if len(parameters) > 3 else 0
                    if status:
                        raise RuntimeError(
                            f"Commande HCI 0x{opcode:04x}: statut 0x{status:02x}"
                        )
                    return
            if event_code == EVT_CMD_STATUS and len(parameters) >= 4:
                status = parameters[0]
                completed_opcode = struct.unpack_from("<H", parameters, 2)[0]
                if completed_opcode == opcode:
                    if status:
                        raise RuntimeError(
                            f"Commande HCI 0x{opcode:04x}: statut 0x{status:02x}"
                        )
                    return
        raise TimeoutError(f"Pas de réponse à la commande HCI 0x{opcode:04x}")

    def start_scan(self):
        parameters = struct.pack(
            "<BHHBB",
            0x01,  # scan actif
            0x0010,
            0x0010,
            0x00,  # adresse locale publique
            0x00,  # tous les annonceurs
        )
        opcode = self.send_command(OGF_LE_CTL, OCF_LE_SET_SCAN_PARAMETERS, parameters)
        self.wait_for_command(opcode, 3.0)
        opcode = self.send_command(OGF_LE_CTL, OCF_LE_SET_SCAN_ENABLE, b"\x01\x00")
        self.wait_for_command(opcode, 3.0)

    def stop_scan(self):
        opcode = self.send_command(OGF_LE_CTL, OCF_LE_SET_SCAN_ENABLE, b"\x00\x00")
        self.wait_for_command(opcode, 3.0)

    def wait_for_advertisement(self, timeout):
        print(f"Recherche directe de {self.address} pendant {timeout:.1f} s...")
        self.start_scan()
        found = False
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                packet = self.receive_packet()
                if packet is None:
                    continue
                packet_type, payload = packet
                if packet_type != HCI_EVENT_PKT or len(payload) < 4:
                    continue
                if (
                    payload[0] != EVT_LE_META_EVENT
                    or payload[2] != EVT_LE_ADVERTISING_REPORT
                ):
                    continue
                reports = payload[3]
                offset = 4
                for _ in range(reports):
                    if offset + 10 > len(payload):
                        break
                    address = le_bytes_to_address(payload[offset + 2 : offset + 8])
                    data_length = payload[offset + 8]
                    report_length = 10 + data_length
                    if address == self.address:
                        print(f"Capteur trouvé: {address}")
                        found = True
                        break
                    offset += report_length
                if found:
                    break
        finally:
            self.stop_scan()

        if not found:
            raise TimeoutError(f"Capteur non trouvé: {self.address}")

    def connect(self, timeout):
        print(f"Connexion LE directe à {self.address}...")
        parameters = struct.pack(
            "<HHBB6sBHHHHHH",
            0x0010,  # scan interval
            0x0010,  # scan window
            0x00,  # initiator filter policy
            self.address_type,
            self.address_le,
            0x00,  # own address type
            0x0018,  # 30 ms
            0x0028,  # 50 ms
            0x0000,  # latency
            0x01F4,  # supervision timeout: 5 s
            0x0000,
            0x0000,
        )
        opcode = self.send_command(OGF_LE_CTL, OCF_LE_CREATE_CONN, parameters)
        self.wait_for_command(opcode, 3.0)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packet = self.receive_packet()
            if packet is None:
                continue
            packet_type, payload = packet
            if packet_type != HCI_EVENT_PKT or len(payload) < 7:
                continue
            if payload[0] != EVT_LE_META_EVENT:
                continue
            subevent = payload[2]
            if subevent not in (
                EVT_LE_CONN_COMPLETE,
                EVT_LE_ENHANCED_CONN_COMPLETE,
            ):
                continue
            status = payload[3]
            if status:
                raise RuntimeError(f"Connexion LE refusée: statut 0x{status:02x}")
            self.connection_handle = struct.unpack_from("<H", payload, 4)[0] & 0x0FFF
            print(f"Connecté, handle HCI 0x{self.connection_handle:04x}.")
            return
        raise TimeoutError("Connexion LE: délai dépassé")

    def send_att(self, payload):
        if self.connection_handle is None:
            raise RuntimeError("Aucune connexion HCI active")
        l2cap = struct.pack("<HH", len(payload), ATT_CID) + payload
        # La capture Android utilise PB=0 pour les premiers fragments ACL TX.
        # Certains contrôleurs acceptent PB=2 pour les réponses simples mais ne
        # transmettent pas correctement les requêtes ATT suivantes.
        handle_flags = self.connection_handle
        packet = (
            struct.pack("<BHH", HCI_ACLDATA_PKT, handle_flags, len(l2cap)) + l2cap
        )
        self.sock.sendall(packet)

    def send_write_command(self, value):
        self.send_att(
            bytes([ATT_OP_WRITE_COMMAND])
            + struct.pack("<H", UART_VALUE_HANDLE)
            + value
        )
        print(f"SEND {value.hex(' ')}")

    def send_write_request(self, handle, value, timeout=5.0):
        print(f"ATT WRITE request handle=0x{handle:04x}: {value.hex(' ')}")
        self.send_att(
            bytes([ATT_OP_WRITE_REQUEST]) + struct.pack("<H", handle) + value
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packet = self.receive_packet()
            if packet is None:
                continue
            att = self.process_packet(packet)
            if not att:
                continue
            if att[0] == ATT_OP_WRITE_RESPONSE:
                print(f"ATT WRITE response handle=0x{handle:04x}")
                return
            if att[0] == ATT_OP_ERROR_RESPONSE:
                raise RuntimeError(f"Écriture ATT refusée: {att.hex(' ')}")
        raise TimeoutError(f"Pas de réponse à l'écriture ATT handle 0x{handle:04x}")

    def receive_packet(self):
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return None
        if not data:
            return None
        return data[0], data[1:]

    def extract_att(self, payload):
        if len(payload) < 4:
            return None
        handle_flags, acl_length = struct.unpack_from("<HH", payload, 0)
        handle = handle_flags & 0x0FFF
        pb_flag = (handle_flags >> 12) & 0x03
        fragment = payload[4 : 4 + acl_length]
        if self.connection_handle is not None and handle != self.connection_handle:
            return None

        if pb_flag in (0x00, 0x02):
            if len(fragment) < 4:
                return None
            l2cap_length, cid = struct.unpack_from("<HH", fragment, 0)
            state = [l2cap_length, cid, bytearray(fragment[4:])]
            self.acl_fragments[handle] = state
        elif pb_flag == 0x01 and handle in self.acl_fragments:
            self.acl_fragments[handle][2].extend(fragment)
        else:
            return None

        length, cid, data = self.acl_fragments[handle]
        if len(data) < length:
            return None
        del self.acl_fragments[handle]
        if cid != ATT_CID:
            return None
        return bytes(data[:length])

    def process_packet(self, packet):
        packet_type, payload = packet
        if packet_type == HCI_ACLDATA_PKT:
            att = self.extract_att(payload)
            if att:
                self.handle_att(att)
            return att

        if packet_type != HCI_EVENT_PKT or len(payload) < 2:
            return None
        if payload[0] == EVT_DISCONN_COMPLETE and len(payload) >= 6:
            handle = struct.unpack_from("<H", payload, 3)[0] & 0x0FFF
            if handle == self.connection_handle:
                reason = payload[5]
                self.connection_handle = None
                raise ConnectionError(
                    f"Capteur déconnecté, raison HCI 0x{reason:02x}"
                )
        if (
            payload[0] == EVT_LE_META_EVENT
            and len(payload) >= 13
            and payload[2] == 0x06
        ):
            handle = struct.unpack_from("<H", payload, 3)[0] & 0x0FFF
            if handle == self.connection_handle:
                interval_min, interval_max, latency, timeout = struct.unpack_from(
                    "<HHHH", payload, 5
                )
                print(
                    "Paramètres LE demandés: "
                    f"{interval_min * 1.25:.2f}-{interval_max * 1.25:.2f} ms, "
                    f"latence={latency}, timeout={timeout * 10} ms"
                )
                response = struct.pack(
                    "<HHHHHHH",
                    handle,
                    interval_min,
                    interval_max,
                    latency,
                    timeout,
                    0x0000,
                    0x0000,
                )
                self.send_command(
                    OGF_LE_CTL,
                    OCF_LE_REMOTE_CONN_PARAM_REQ_REPLY,
                    response,
                )
        return None

    def handle_att(self, att):
        opcode = att[0]
        if opcode == ATT_OP_MTU_REQUEST and len(att) >= 3:
            requested = struct.unpack_from("<H", att, 1)[0]
            accepted = min(requested, 158)
            print(f"ATT MTU demandé: {requested}, réponse: {accepted}")
            self.send_att(bytes([ATT_OP_MTU_RESPONSE]) + struct.pack("<H", accepted))
            return

        if opcode == ATT_OP_FIND_BY_TYPE_VALUE_REQUEST and len(att) >= 5:
            start_handle = struct.unpack_from("<H", att, 1)[0]
            print("Recherche du service Kinvent local: réponse Attribute Not Found.")
            self.send_att(
                bytes(
                    [
                        ATT_OP_ERROR_RESPONSE,
                        ATT_OP_FIND_BY_TYPE_VALUE_REQUEST,
                    ]
                )
                + struct.pack("<H", start_handle)
                + b"\x0a"
            )
            return

        if opcode == ATT_OP_NOTIFICATION and len(att) >= 3:
            value_handle = struct.unpack_from("<H", att, 1)[0]
            value = att[3:]
            self.notifications += 1
            print(
                f"{now_iso()} | handle=0x{value_handle:04x} | "
                f"len={len(value)} | {value.hex(' ')}"
            )
            if self.csv_writer is not None:
                self.csv_writer.writerow(
                    [now_iso(), f"0x{value_handle:04x}", len(value), value.hex(" ")]
                )
                self.csv_file.flush()
            return

        print(f"ATT reçu opcode=0x{opcode:02x}: {att.hex(' ')}")

    def service_initial_handshake(self, duration=0.8):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            packet = self.receive_packet()
            if packet is not None:
                self.process_packet(packet)

    def start_stream(self, write_delay):
        # L'application Android envoie un premier 0x10 avant d'activer le CCCD.
        self.send_write_command(b"\x10")
        time.sleep(write_delay)

        print(f"Activation notification UART sur 0x{UART_CCCD_HANDLE:04x}...")
        self.send_write_request(UART_CCCD_HANDLE, b"\x01\x00")

        for command in INIT_COMMANDS:
            self.send_write_command(command)
            self.pump(write_delay)

    def pump(self, duration):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            packet = self.receive_packet()
            if packet is not None:
                self.process_packet(packet)

    def run(self, scan_timeout, connect_timeout, duration, write_delay):
        self.open()
        try:
            self.reset()
            self.wait_for_advertisement(scan_timeout)
            self.connect(connect_timeout)
            self.service_initial_handshake()
            self.start_stream(write_delay)
            print(f"Acquisition pendant {duration:.1f} s...")
            self.pump(duration)
            print(f"Notifications reçues: {self.notifications}")
        finally:
            self.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Acquisition Kinvent directe par HCI/ATT, sans GATT BlueZ.",
    )
    parser.add_argument("--adapter", type=parse_adapter, default=1)
    parser.add_argument("--address", required=True)
    parser.add_argument(
        "--address-type",
        choices=["public", "random"],
        default="public",
    )
    parser.add_argument("--scan-timeout", type=float, default=15.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--write-delay", type=float, default=0.5)
    parser.add_argument("--csv")
    return parser


def main():
    args = build_parser().parse_args()
    client = RawKinventClient(
        args.adapter,
        args.address,
        args.address_type,
        args.csv,
    )
    client.run(
        args.scan_timeout,
        args.connect_timeout,
        args.duration,
        args.write_delay,
    )


if __name__ == "__main__":
    main()
