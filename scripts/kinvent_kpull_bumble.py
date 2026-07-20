"""Acquisition K-Pull via Bumble et contrôleur HCI USB.

Le transport Bluetooth est assuré par Bumble, mais le protocole capteur reste
celui observé dans les captures officielles Kinvent : handles UART fixes,
activation CCCD officielle et mêmes commandes d'initialisation.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from pathlib import Path
from statistics import median


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.common.devices import KPULL  # noqa: E402
from ble.kinvent.bumble_backend import (  # noqa: E402
    DEFAULT_BUMBLE_TRANSPORT,
    BumbleBackendError,
    require_bumble,
)
from ble.kinvent.kpull.protocol import (  # noqa: E402
    calibrate_sample,
    compute_stable_calibration,
    parse_raw_frame,
)
from scripts.kinvent_kpush_bumble import make_remote_address  # noqa: E402
from scripts.kinvent_raw_hci import (  # noqa: E402
    INIT_COMMANDS,
    UART_CCCD_HANDLE,
    UART_VALUE_HANDLE,
    now_iso,
)


CSV_FIELDS = [
    "timestamp_utc",
    "sensor_time",
    "raw_force",
    "tare_offset",
    "force_counts",
    "force_kg",
    "force_n",
]


class KPullBumbleClient:
    def __init__(
        self,
        transport,
        address,
        address_type,
        csv_path=None,
        tare_duration=2.0,
        print_interval=0.1,
        counts_per_kg=None,
        known_load_kg=None,
        write_delay=0.05,
        keepalive_interval=10.0,
    ):
        self.transport = transport
        self.address = address
        self.address_type = address_type
        self.tare_duration = tare_duration
        self.print_interval = print_interval
        self.counts_per_kg = counts_per_kg
        self.known_load_kg = known_load_kg
        self.write_delay = write_delay
        self.keepalive_interval = keepalive_interval
        self.tare_started_at = None
        self.tare_values = []
        self.tare_offset = None
        self.latest = None
        self.maximum_raw_force = None
        self.maximum_delta = 0
        self.force_deltas = []
        self.last_measurement_print = 0.0
        self.notification_count = 0
        self.csv_path = Path(csv_path) if csv_path else None
        self.csv_file = None
        self.csv_writer = None

        if self.csv_path:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            self.csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(CSV_FIELDS)
            self.csv_file.flush()

    def close(self):
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None

    def handle_payload(self, payload):
        raw_sample = parse_raw_frame(bytes(payload))
        if raw_sample is None:
            return
        self.notification_count += 1

        if self.tare_offset is None:
            if self.tare_started_at is None:
                self.tare_started_at = time.monotonic()
                print(
                    f"Tare K-Pull pendant {self.tare_duration:.1f} s: "
                    "laisser le câble sans tension.",
                    flush=True,
                )
            self.tare_values.append(raw_sample["raw_force"])
            if time.monotonic() - self.tare_started_at < self.tare_duration:
                return
            self.tare_offset = round(median(self.tare_values))
            print(f"Tare K-Pull terminée: offset={self.tare_offset}.", flush=True)

        sample = calibrate_sample(
            raw_sample,
            self.tare_offset,
            self.counts_per_kg,
        )
        self.latest = sample
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

        if time.monotonic() - self.last_measurement_print >= self.print_interval:
            if sample["force_kg"] is None:
                print(
                    f"{timestamp} | delta={sample['force_counts']:+d} comptes | "
                    f"MAX={self.maximum_delta} comptes",
                    flush=True,
                )
            else:
                print(
                    f"{timestamp} | {sample['force_kg']:.2f} kg | "
                    f"{sample['force_n']:.1f} N",
                    flush=True,
                )
            self.last_measurement_print = time.monotonic()

    async def write(self, client, value, with_response=False):
        print(f"SEND {value.hex(' ')}", flush=True)
        await client.write_value(
            UART_VALUE_HANDLE,
            value,
            with_response=with_response,
        )
        await asyncio.sleep(self.write_delay)

    async def run(self, duration, connect_timeout=15.0):
        require_bumble()
        from bumble.device import Device
        from bumble.hci import Address
        from bumble.transport import open_transport

        async with await open_transport(self.transport) as hci_transport:
            device = Device.with_hci(
                "Kine Capteurs Bumble",
                Address("F0:F1:F2:F3:F4:F5"),
                hci_transport.source,
                hci_transport.sink,
            )
            await device.power_on()
            remote_address = make_remote_address(
                self.address,
                self.address_type,
                Address,
            )
            print(
                f"Connexion K-Pull Bumble à {remote_address} "
                f"via {self.transport}...",
                flush=True,
            )
            connection = await asyncio.wait_for(
                device.connect(remote_address),
                timeout=connect_timeout,
            )
            print("Connexion BLE établie.", flush=True)

            client = connection.gatt_client
            client.notification_subscribers.setdefault(
                UART_VALUE_HANDLE,
                set(),
            ).add(self.handle_payload)

            # Séquence observée dans les captures Android et dans le pilote HCI.
            await self.write(client, b"\x10")
            print(
                f"Activation notification UART sur 0x{UART_CCCD_HANDLE:04x}...",
                flush=True,
            )
            await client.write_value(
                UART_CCCD_HANDLE,
                b"\x01\x00",
                with_response=True,
            )

            for command in INIT_COMMANDS:
                await self.write(client, command)

            print("Acquisition K-Pull Bumble démarrée.", flush=True)
            start = time.monotonic()
            deadline = start + duration
            next_keepalive = start + self.keepalive_interval
            next_progress = start

            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                now = time.monotonic()
                if self.keepalive_interval > 0 and now >= next_keepalive:
                    await self.write(client, b"\xff")
                    next_keepalive = now + self.keepalive_interval
                if now >= next_progress:
                    remaining = max(0.0, deadline - now)
                    print(f"Temps restant: {remaining:4.1f} s", flush=True)
                    next_progress = now + 5.0

            await connection.disconnect()

    def calibration_result(self):
        if self.known_load_kg is None or not self.force_deltas:
            return None
        return compute_stable_calibration(
            self.force_deltas,
            self.known_load_kg,
        )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Acquisition K-Pull via Bumble.",
    )
    parser.add_argument("--transport", default=DEFAULT_BUMBLE_TRANSPORT)
    parser.add_argument("--address", default=KPULL)
    parser.add_argument(
        "--address-type",
        choices=["public", "random"],
        default="public",
    )
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--tare-duration", type=float, default=2.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--write-delay", type=float, default=0.05)
    parser.add_argument("--print-interval", type=float, default=0.1)
    parser.add_argument("--keepalive-interval", type=float, default=10.0)
    parser.add_argument("--counts-per-kg", type=float)
    parser.add_argument("--known-load-kg", type=float)
    parser.add_argument("--csv")
    return parser


def main():
    args = build_parser().parse_args()
    client = KPullBumbleClient(
        transport=args.transport,
        address=args.address,
        address_type=args.address_type,
        csv_path=args.csv,
        tare_duration=args.tare_duration,
        print_interval=args.print_interval,
        counts_per_kg=args.counts_per_kg,
        known_load_kg=args.known_load_kg,
        write_delay=args.write_delay,
        keepalive_interval=args.keepalive_interval,
    )
    try:
        asyncio.run(client.run(args.duration, args.connect_timeout))
    except BumbleBackendError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        client.close()
    print(f"Variation maximale: {client.maximum_delta} comptes.", flush=True)
    calibration = client.calibration_result()
    if calibration is not None:
        print(
            "Palier stable retenu: "
            f"{calibration['stable_counts']:.0f} comptes "
            f"sur {calibration['stable_samples']} mesures.",
            flush=True,
        )
        print(
            "Calibration calculée: "
            f"{calibration['counts_per_kg']:.6f} comptes/kg.",
            flush=True,
        )


if __name__ == "__main__":
    main()
