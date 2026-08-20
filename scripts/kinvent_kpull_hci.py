"""Diagnostic et calibration du K-Pull via HCI direct."""

import argparse
import csv
import struct
import sys
import time
from pathlib import Path
from statistics import median


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.common.devices import KPULL  # noqa: E402
from ble.kinvent.kpull.protocol import (  # noqa: E402
    calibrate_sample,
    compute_stable_calibration,
    parse_raw_frame,
)
from scripts.kinvent_raw_hci import (  # noqa: E402
    ATT_OP_ERROR_RESPONSE,
    ATT_OP_FIND_BY_TYPE_VALUE_REQUEST,
    ATT_OP_MTU_REQUEST,
    ATT_OP_MTU_RESPONSE,
    ATT_OP_NOTIFICATION,
    RawKinventClient,
    now_iso,
    parse_adapter,
)


class KPullHciClient(RawKinventClient):
    def __init__(
        self,
        adapter,
        address,
        csv_path=None,
        tare_duration=2.0,
        print_interval=0.1,
        counts_per_kg=None,
        known_load_kg=None,
    ):
        super().__init__(
            adapter=adapter,
            address=address,
            address_type="public",
            csv_path=None,
            tare_duration=tare_duration,
            print_interval=print_interval,
        )
        self.tare_values = []
        self.tare_offset = None
        self.counts_per_kg = counts_per_kg
        self.known_load_kg = known_load_kg
        self.maximum_raw_force = None
        self.maximum_delta = 0
        self.force_deltas = []
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
                    "raw_force",
                    "tare_offset",
                    "force_counts",
                    "force_kg",
                    "force_n",
                ]
            )

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

        raw_sample = parse_raw_frame(att[3:])
        if raw_sample is None:
            return
        self.notifications += 1
        if self.tare_offset is None:
            if self.tare_started_at is None:
                self.tare_started_at = time.monotonic()
                print(
                    f"Tare K-Pull pendant {self.tare_duration:.1f} s: "
                    "laisser le câble sans tension."
                )
            self.tare_values.append(raw_sample["raw_force"])
            if time.monotonic() - self.tare_started_at < self.tare_duration:
                return
            self.tare_offset = round(median(self.tare_values))
            print(f"Tare K-Pull terminée: offset={self.tare_offset}.")

        sample = calibrate_sample(
            raw_sample,
            self.tare_offset,
            self.counts_per_kg,
        )
        delta = abs(sample["force_counts"])
        self.force_deltas.append(sample["force_counts"])
        if delta > self.maximum_delta:
            self.maximum_delta = delta
            self.maximum_raw_force = raw_sample["raw_force"]

        timestamp = now_iso()
        if self.csv_writer:
            self.csv_writer.writerow(
                [
                    timestamp,
                    sample["t"],
                    sample["raw_force"],
                    sample["tare_offset"],
                    sample["force_counts"],
                    round(sample["force_kg"], 6)
                    if sample["force_kg"] is not None
                    else "",
                    round(sample["force_n"], 6)
                    if sample["force_n"] is not None
                    else "",
                ]
            )
            self.csv_file.flush()

        if (
            time.monotonic() - self.last_measurement_print
            >= self.print_interval
        ):
            if sample["force_kg"] is None:
                print(
                    f"{timestamp} | delta={sample['force_counts']:+d} comptes | "
                    f"MAX={self.maximum_delta} comptes"
                )
            else:
                print(
                    f"{timestamp} | {sample['force_kg']:.2f} kg | "
                    f"{sample['force_n']:.1f} N"
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

    def calibration_result(self):
        if (
            self.known_load_kg is None
            or not self.force_deltas
        ):
            return None
        return compute_stable_calibration(
            self.force_deltas,
            self.known_load_kg,
        )

    def session_ready(self):
        return self.tare_offset is not None

    def ready_message(self):
        return "K-Pull prêt; liaison Bluetooth conservée."


def build_parser():
    parser = argparse.ArgumentParser(
        description="Diagnostic direct du K-Pull / KFORCE Link.",
    )
    parser.add_argument("--adapter", type=parse_adapter, default=0)
    parser.add_argument("--address", default=KPULL)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--tare-duration", type=float, default=2.0)
    parser.add_argument("--scan-timeout", type=float, default=15.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--write-delay", type=float, default=0.5)
    parser.add_argument("--print-interval", type=float, default=0.1)
    parser.add_argument("--counts-per-kg", type=float)
    parser.add_argument("--known-load-kg", type=float)
    parser.add_argument("--csv")
    parser.add_argument("--control-file")
    parser.add_argument("--hci-fd", type=int)
    parser.add_argument("--connect-attempts", type=int, default=1)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    return parser


def main():
    args = build_parser().parse_args()
    client = KPullHciClient(
        adapter=args.adapter,
        address=args.address,
        csv_path=args.csv,
        tare_duration=args.tare_duration,
        print_interval=args.print_interval,
        counts_per_kg=args.counts_per_kg,
        known_load_kg=args.known_load_kg,
    )
    if args.hci_fd is not None:
        client.attach_hci_fd(args.hci_fd)
    if args.control_file:
        client.run_persistent(
            scan_timeout=args.scan_timeout,
            connect_timeout=args.connect_timeout,
            write_delay=args.write_delay,
            control_file=args.control_file,
            connect_attempts=max(1, args.connect_attempts),
            retry_delay=args.retry_delay,
        )
    else:
        client.run(
            scan_timeout=args.scan_timeout,
            connect_timeout=args.connect_timeout,
            duration=args.duration,
            write_delay=args.write_delay,
        )
    print(f"Variation maximale: {client.maximum_delta} comptes.")
    calibration = client.calibration_result()
    if calibration is not None:
        print(
            "Palier stable retenu: "
            f"{calibration['stable_counts']:.0f} comptes "
            f"sur {calibration['stable_samples']} mesures."
        )
        print(
            "Calibration calculée: "
            f"{calibration['counts_per_kg']:.6f} comptes/kg."
        )


if __name__ == "__main__":
    main()
