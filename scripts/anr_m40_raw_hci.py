"""Diagnostic ANR M40 par HCI/ATT direct, sans BlueZ ni GATT Bumble."""

from __future__ import annotations

import argparse
import csv
import struct
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.anr.protocol import decode_emg, encode_device_id  # noqa: E402
from scripts.kinvent_raw_hci import (  # noqa: E402
    ATT_OP_ERROR_RESPONSE,
    ATT_OP_MTU_REQUEST,
    ATT_OP_MTU_RESPONSE,
    ATT_OP_NOTIFICATION,
    ATT_OP_WRITE_REQUEST,
    ATT_OP_WRITE_RESPONSE,
    RawKinventClient,
    now_iso,
    parse_adapter,
)


ATT_OP_FIND_INFORMATION_REQUEST = 0x04
ATT_OP_FIND_INFORMATION_RESPONSE = 0x05
ATT_OP_READ_BY_TYPE_REQUEST = 0x08
ATT_OP_READ_BY_TYPE_RESPONSE = 0x09

UUID_CHARACTERISTIC = 0x2803
UUID_CCCD = 0x2902
UUID_ANALOG = 0x2A58
UUID_DIGITAL = 0x2A56


def uuid16_bytes(value):
    return struct.pack("<H", value)


class RawANRM40Client(RawKinventClient):
    def __init__(
        self,
        adapter,
        address,
        address_type,
        csv_path=None,
        device_id=1,
        print_interval=0.1,
    ):
        super().__init__(
            adapter=adapter,
            address=address,
            address_type=address_type,
            csv_path=None,
            tare_duration=0.0,
            print_interval=print_interval,
        )
        self.device_id = device_id
        self.print_interval = print_interval
        self.started_at = None
        self.last_print = 0.0
        self.latest_emg = None
        self.characteristics = {}
        self.notification_handle = None
        self.csv_file = None
        self.csv_writer = None
        if csv_path:
            path = Path(csv_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self.csv_file = path.open("w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(["timestamp_utc", "elapsed_seconds", "emg_raw"])
            self.csv_file.flush()

    def handle_att(self, att):
        opcode = att[0]
        if opcode == ATT_OP_MTU_REQUEST and len(att) >= 3:
            requested = struct.unpack_from("<H", att, 1)[0]
            accepted = min(requested, 158)
            print(f"ATT MTU demandé par M40: {requested}, réponse: {accepted}")
            self.send_att(bytes([ATT_OP_MTU_RESPONSE]) + struct.pack("<H", accepted))
            return

        if opcode == ATT_OP_NOTIFICATION and len(att) >= 3:
            value_handle = struct.unpack_from("<H", att, 1)[0]
            value = att[3:]
            if (
                self.notification_handle is not None
                and value_handle != self.notification_handle
            ):
                print(
                    f"Notification ignorée handle=0x{value_handle:04x}: "
                    f"{value.hex(' ')}"
                )
                return
            try:
                emg = decode_emg(value)
            except ValueError as exc:
                print(f"Trame EMG rejetée: {value.hex(' ')} ({exc})")
                return

            self.notifications += 1
            self.latest_emg = emg
            elapsed = time.monotonic() - self.started_at
            timestamp = now_iso()
            if self.csv_writer is not None:
                self.csv_writer.writerow([timestamp, round(elapsed, 6), emg])
                self.csv_file.flush()
            if time.monotonic() - self.last_print >= self.print_interval:
                print(f"{elapsed:6.2f} s | EMG={emg:4d} / 1023")
                self.last_print = time.monotonic()
            return

        if opcode not in (
            ATT_OP_ERROR_RESPONSE,
            ATT_OP_MTU_RESPONSE,
            ATT_OP_FIND_INFORMATION_RESPONSE,
            ATT_OP_READ_BY_TYPE_RESPONSE,
            ATT_OP_WRITE_RESPONSE,
        ):
            print(f"ATT reçu opcode=0x{opcode:02x}: {att.hex(' ')}")

    def wait_for_att_response(self, accepted_opcodes, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packet = self.receive_packet()
            if packet is None:
                continue
            att = self.process_packet(packet)
            if not att:
                continue
            if att[0] in accepted_opcodes:
                return att
        raise TimeoutError(
            "Pas de réponse ATT "
            + "/".join(f"0x{opcode:02x}" for opcode in accepted_opcodes)
        )

    def exchange_mtu(self):
        self.send_att(bytes([ATT_OP_MTU_REQUEST]) + struct.pack("<H", 158))
        response = self.wait_for_att_response(
            {ATT_OP_MTU_RESPONSE, ATT_OP_ERROR_RESPONSE},
            timeout=5.0,
        )
        if response[0] == ATT_OP_MTU_RESPONSE and len(response) >= 3:
            mtu = struct.unpack_from("<H", response, 1)[0]
            print(f"ATT MTU M40: {mtu}")
        else:
            print(f"MTU refusé: {response.hex(' ')}")

    def read_by_type(self, start_handle, end_handle, uuid16, timeout=5.0):
        self.send_att(
            bytes([ATT_OP_READ_BY_TYPE_REQUEST])
            + struct.pack("<HH", start_handle, end_handle)
            + uuid16_bytes(uuid16)
        )
        return self.wait_for_att_response(
            {ATT_OP_READ_BY_TYPE_RESPONSE, ATT_OP_ERROR_RESPONSE},
            timeout=timeout,
        )

    def find_information(self, start_handle, end_handle, timeout=5.0):
        self.send_att(
            bytes([ATT_OP_FIND_INFORMATION_REQUEST])
            + struct.pack("<HH", start_handle, end_handle)
        )
        return self.wait_for_att_response(
            {ATT_OP_FIND_INFORMATION_RESPONSE, ATT_OP_ERROR_RESPONSE},
            timeout=timeout,
        )

    def discover_characteristics(self):
        print("Découverte ATT ciblée des caractéristiques M40...")
        declarations = []
        start = 0x0001
        while start <= 0xFFFF:
            response = self.read_by_type(start, 0xFFFF, UUID_CHARACTERISTIC)
            if response[0] == ATT_OP_ERROR_RESPONSE:
                break
            if len(response) < 2:
                break
            item_length = response[1]
            if item_length < 7:
                break
            offset = 2
            last_decl = None
            while offset + item_length <= len(response):
                item = response[offset : offset + item_length]
                declaration_handle = struct.unpack_from("<H", item, 0)[0]
                properties = item[2]
                value_handle = struct.unpack_from("<H", item, 3)[0]
                uuid = struct.unpack_from("<H", item, 5)[0]
                declarations.append(
                    {
                        "declaration_handle": declaration_handle,
                        "properties": properties,
                        "value_handle": value_handle,
                        "uuid": uuid,
                    }
                )
                last_decl = declaration_handle
                if uuid in (UUID_ANALOG, UUID_DIGITAL):
                    self.characteristics[uuid] = declarations[-1]
                    print(
                        f"Caractéristique 0x{uuid:04x}: "
                        f"value=0x{value_handle:04x}, props=0x{properties:02x}"
                    )
                offset += item_length
            if last_decl is None or last_decl >= 0xFFFF:
                break
            start = last_decl + 1

        if UUID_ANALOG not in self.characteristics:
            raise RuntimeError("Caractéristique EMG 0x2A58 introuvable.")
        if UUID_DIGITAL not in self.characteristics:
            print("Caractéristique device-id 0x2A56 introuvable.")
        return declarations

    def find_cccd(self, declarations, characteristic):
        value_handle = characteristic["value_handle"]
        next_declarations = [
            item["declaration_handle"]
            for item in declarations
            if item["declaration_handle"] > characteristic["declaration_handle"]
        ]
        end_handle = (min(next_declarations) - 1) if next_declarations else 0xFFFF
        start_handle = value_handle + 1
        if start_handle > end_handle:
            raise RuntimeError("Aucun espace de descripteur CCCD après EMG.")
        response = self.find_information(start_handle, end_handle)
        if response[0] == ATT_OP_ERROR_RESPONSE:
            raise RuntimeError(f"CCCD EMG introuvable: {response.hex(' ')}")
        if len(response) < 2:
            raise RuntimeError("Réponse CCCD invalide.")
        fmt = response[1]
        if fmt != 0x01:
            raise RuntimeError(
                "Format de descripteur inattendu pour le M40: "
                f"0x{fmt:02x}"
            )
        offset = 2
        while offset + 4 <= len(response):
            handle, uuid = struct.unpack_from("<HH", response, offset)
            if uuid == UUID_CCCD:
                print(f"CCCD EMG trouvé: 0x{handle:04x}")
                return handle
            offset += 4
        raise RuntimeError("Descripteur CCCD 0x2902 introuvable pour EMG.")

    def write_request(self, handle, value, label):
        print(f"{label}: écriture handle=0x{handle:04x} {value.hex(' ')}")
        self.send_att(bytes([ATT_OP_WRITE_REQUEST]) + struct.pack("<H", handle) + value)
        response = self.wait_for_att_response(
            {ATT_OP_WRITE_RESPONSE, ATT_OP_ERROR_RESPONSE},
            timeout=5.0,
        )
        if response[0] == ATT_OP_ERROR_RESPONSE:
            raise RuntimeError(f"{label}: écriture refusée {response.hex(' ')}")

    def configure_m40(self):
        self.exchange_mtu()
        declarations = self.discover_characteristics()

        digital = self.characteristics.get(UUID_DIGITAL)
        if digital:
            self.write_request(
                digital["value_handle"],
                encode_device_id(self.device_id),
                "Device ID M40",
            )
        cccd = self.find_cccd(declarations, self.characteristics[UUID_ANALOG])
        self.notification_handle = self.characteristics[UUID_ANALOG]["value_handle"]
        self.write_request(cccd, b"\x01\x00", "Activation notifications EMG")

    def run(self, scan_timeout, connect_timeout, duration):
        self.open()
        try:
            self.reset()
            self.wait_for_advertisement(scan_timeout)
            self.connect(connect_timeout)
            self.configure_m40()
            self.started_at = time.monotonic()
            print(f"Notifications EMG pendant {duration:.1f} s...")
            self.pump(duration, show_progress=True)
            print("Acquisition terminée.")
            print(f"Notifications reçues: {self.notifications}")
        finally:
            self.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Diagnostic ANR M40 direct par HCI/ATT.",
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
    parser.add_argument("--device-id", type=int, default=1)
    parser.add_argument(
        "--print-interval",
        type=float,
        default=0.1,
        help="Intervalle entre deux valeurs affichées; toutes restent en CSV.",
    )
    parser.add_argument("--csv")
    return parser


def main():
    args = build_parser().parse_args()
    client = RawANRM40Client(
        adapter=args.adapter,
        address=args.address,
        address_type=args.address_type,
        csv_path=args.csv,
        device_id=args.device_id,
        print_interval=args.print_interval,
    )
    client.run(
        scan_timeout=args.scan_timeout,
        connect_timeout=args.connect_timeout,
        duration=args.duration,
    )


if __name__ == "__main__":
    main()
