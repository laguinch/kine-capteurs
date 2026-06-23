"""Diagnostic du goniomètre K-Move via HCI direct."""

import argparse
import csv
import struct
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.common.devices import KMOVE  # noqa: E402
from ble.kinvent.kmove.protocol import (  # noqa: E402
    parse_quaternion_frame,
    quaternion_to_euler_degrees,
    relative_quaternion,
)
from scripts.kinvent_raw_hci import (  # noqa: E402
    ATT_OP_ERROR_RESPONSE,
    ATT_OP_FIND_BY_TYPE_VALUE_REQUEST,
    ATT_OP_MTU_REQUEST,
    ATT_OP_MTU_RESPONSE,
    ATT_OP_NOTIFICATION,
    EVT_LE_ADVERTISING_REPORT,
    EVT_LE_META_EVENT,
    HCI_EVENT_PKT,
    RawKinventClient,
    UART_CCCD_HANDLE,
    address_to_le_bytes,
    le_bytes_to_address,
    now_iso,
    parse_adapter,
)


INIT_COMMANDS = [
    (b"\x10", 0.30),
    (b"\x09", 0.06),
    (b"\x76", 0.29),
    (b"\x11", 0.16),
    (b"\x10", 0.01),
    (b"\x10", 0.42),
    (bytes.fromhex("ac 00 54 f8"), 0.05),
    (b"\xb6", 0.06),
    (b"\xb0", 0.20),
]


class KMoveHciClient(RawKinventClient):
    def __init__(
        self,
        adapter,
        address,
        csv_path=None,
        reference_duration=2.0,
        print_interval=0.1,
    ):
        super().__init__(
            adapter=adapter,
            address=address,
            address_type="public",
            csv_path=None,
            tare_duration=reference_duration,
            print_interval=print_interval,
        )
        self.reference_duration = reference_duration
        self.reference_started_at = None
        self.reference_quaternion = None
        self.reference_samples = []
        self.keepalive_interval = 10.0
        self.next_keepalive_at = None
        self.csv_path = Path(csv_path) if csv_path else None
        if self.csv_path:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            self.csv_file = self.csv_path.open(
                "w",
                newline="",
                encoding="utf-8",
            )
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(
                [
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
            )

    def reset(self):
        """Tolère une seule transition lente du dongle après les plateformes."""
        try:
            super().reset()
        except TimeoutError as exc:
            if "0x0c03" not in str(exc):
                raise
            print(
                "Contrôleur encore en cours de libération; "
                "nouvel essai HCI Reset..."
            )
            time.sleep(1.0)
            super().reset()

    @staticmethod
    def _advertised_name(data):
        offset = 0
        while offset < len(data):
            length = data[offset]
            if length == 0 or offset + length >= len(data):
                break
            ad_type = data[offset + 1]
            value = data[offset + 2:offset + 1 + length]
            if ad_type in (0x08, 0x09):
                return value.decode("utf-8", errors="replace")
            offset += length + 1
        return None

    def wait_for_advertisement(self, timeout):
        print(
            f"Recherche du K-Move {self.address} ou KFORCESens "
            f"pendant {timeout:.1f} s..."
        )
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
                    address_type = payload[offset + 1]
                    address = le_bytes_to_address(
                        payload[offset + 2:offset + 8]
                    )
                    data_length = payload[offset + 8]
                    data = payload[
                        offset + 9:offset + 9 + data_length
                    ]
                    name = self._advertised_name(data)
                    if address == self.address or (
                        name and name.startswith("KFORCESens")
                    ):
                        self.address = address
                        self.address_le = address_to_le_bytes(address)
                        self.address_type = address_type
                        print(
                            f"K-Move trouvé: {name or 'nom inconnu'} "
                            f"({address})"
                        )
                        found = True
                        break
                    offset += 10 + data_length
                if found:
                    break
        finally:
            self.stop_scan()

        if not found:
            raise TimeoutError(
                "K-Move introuvable. Vérifiez qu'il est allumé et qu'il "
                "n'est plus connecté au téléphone."
            )

    def start_stream(self, write_delay):
        self.send_write_command(b"\x10")
        time.sleep(write_delay)
        print(f"Activation notification UART sur 0x{UART_CCCD_HANDLE:04x}...")
        self.send_write_request(UART_CCCD_HANDLE, b"\x01\x00")
        for command, delay in INIT_COMMANDS:
            self.send_write_command(command)
            self.pump(delay)

    def handle_att(self, att):
        opcode = att[0]
        if opcode == ATT_OP_MTU_REQUEST and len(att) >= 3:
            requested = struct.unpack_from("<H", att, 1)[0]
            self.send_att(
                bytes([ATT_OP_MTU_RESPONSE])
                + struct.pack("<H", min(requested, 158))
            )
            return
        if opcode == ATT_OP_FIND_BY_TYPE_VALUE_REQUEST and len(att) >= 5:
            start_handle = struct.unpack_from("<H", att, 1)[0]
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
        if opcode != ATT_OP_NOTIFICATION or len(att) < 3:
            return

        sample = parse_quaternion_frame(att[3:])
        if sample is None:
            return
        self.notifications += 1
        quaternion = sample["quaternion"]
        if self.reference_quaternion is None:
            if self.reference_started_at is None:
                self.reference_started_at = time.monotonic()
                print(
                    f"Référence K-Move pendant {self.reference_duration:.1f} s: "
                    "maintenir le capteur immobile."
                )
            self.reference_samples.append(quaternion)
            if (
                time.monotonic() - self.reference_started_at
                < self.reference_duration
            ):
                return
            average = tuple(
                sum(values) / len(self.reference_samples)
                for values in zip(*self.reference_samples)
            )
            norm = sum(value * value for value in average) ** 0.5
            self.reference_quaternion = tuple(value / norm for value in average)
            print("Référence K-Move enregistrée. Les trois axes sont à zéro.")

        relative = relative_quaternion(self.reference_quaternion, quaternion)
        angles = quaternion_to_euler_degrees(relative)
        timestamp = now_iso()
        if self.csv_writer:
            self.csv_writer.writerow(
                [
                    timestamp,
                    sample["t"],
                    *[round(value, 8) for value in quaternion],
                    round(angles["rotation_x_deg"], 6),
                    round(angles["rotation_y_deg"], 6),
                    round(angles["rotation_z_deg"], 6),
                    sample["accel_x_raw"],
                    sample["accel_y_raw"],
                    sample["accel_z_raw"],
                    sample["battery_pct"],
                ]
            )
            self.csv_file.flush()
        if (
            time.monotonic() - self.last_measurement_print
            >= self.print_interval
        ):
            print(
                f"{timestamp} | "
                f"X={angles['rotation_x_deg']:+7.2f}° | "
                f"Y={angles['rotation_y_deg']:+7.2f}° | "
                f"Z={angles['rotation_z_deg']:+7.2f}° | "
                f"batterie={sample['battery_pct']}%"
            )
            self.last_measurement_print = time.monotonic()

    def pump(self, duration, show_progress=False):
        persistent = duration <= 0
        deadline = None if persistent else time.monotonic() + duration
        next_progress = time.monotonic()
        if self.next_keepalive_at is None:
            self.next_keepalive_at = time.monotonic() + self.keepalive_interval
        while persistent or time.monotonic() < deadline:
            packet = self.receive_packet()
            if packet is not None:
                self.process_packet(packet)
            if time.monotonic() >= self.next_keepalive_at:
                self.send_write_command(b"\xff")
                self.next_keepalive_at = (
                    time.monotonic() + self.keepalive_interval
                )
            if (
                show_progress
                and not persistent
                and time.monotonic() >= next_progress
            ):
                print(f"Temps restant: {max(0, deadline-time.monotonic()):.1f} s")
                next_progress = time.monotonic() + 5.0

    def session_ready(self):
        return self.reference_quaternion is not None

    def ready_message(self):
        return "K-Move prêt; liaison Bluetooth conservée."


def build_parser():
    parser = argparse.ArgumentParser(
        description="Diagnostic direct du K-Move / KFORCE Sens.",
    )
    parser.add_argument("--adapter", type=parse_adapter, default=0)
    parser.add_argument("--address", default=KMOVE)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--reference-duration", type=float, default=2.0)
    parser.add_argument("--scan-timeout", type=float, default=30.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--write-delay", type=float, default=0.5)
    parser.add_argument("--print-interval", type=float, default=0.1)
    parser.add_argument("--csv")
    parser.add_argument("--control-file")
    parser.add_argument("--hci-fd", type=int)
    return parser


def main():
    args = build_parser().parse_args()
    client = KMoveHciClient(
        adapter=args.adapter,
        address=args.address,
        csv_path=args.csv,
        reference_duration=args.reference_duration,
        print_interval=args.print_interval,
    )
    if args.hci_fd is not None:
        client.attach_hci_fd(args.hci_fd)
    if args.control_file:
        client.run_persistent(
            scan_timeout=args.scan_timeout,
            connect_timeout=args.connect_timeout,
            write_delay=args.write_delay,
            control_file=args.control_file,
        )
    else:
        client.run(
            scan_timeout=args.scan_timeout,
            connect_timeout=args.connect_timeout,
            duration=args.duration,
            write_delay=args.write_delay,
        )


if __name__ == "__main__":
    main()
