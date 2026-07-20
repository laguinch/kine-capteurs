"""Acquisition K-Move via Bumble et contrôleur HCI USB.

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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.common.devices import KMOVE  # noqa: E402
from ble.kinvent.bumble_backend import (  # noqa: E402
    DEFAULT_BUMBLE_TRANSPORT,
    BumbleBackendError,
    require_bumble,
)
from ble.kinvent.kmove.protocol import (  # noqa: E402
    parse_quaternion_frame,
    quaternion_to_euler_degrees,
    relative_quaternion,
)
from scripts.kinvent_kmove_hci import INIT_COMMANDS  # noqa: E402
from scripts.kinvent_kpush_bumble import make_remote_address  # noqa: E402
from scripts.kinvent_raw_hci import (  # noqa: E402
    UART_CCCD_HANDLE,
    UART_VALUE_HANDLE,
    now_iso,
)


CSV_FIELDS = [
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


class KMoveBumbleClient:
    def __init__(
        self,
        transport,
        address,
        address_type,
        csv_path=None,
        reference_duration=2.0,
        print_interval=0.1,
        write_delay=0.05,
        keepalive_interval=10.0,
    ):
        self.transport = transport
        self.address = address
        self.address_type = address_type
        self.reference_duration = reference_duration
        self.print_interval = print_interval
        self.write_delay = write_delay
        self.keepalive_interval = keepalive_interval
        self.reference_started_at = None
        self.reference_quaternion = None
        self.reference_samples = []
        self.latest = None
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
        sample = parse_quaternion_frame(bytes(payload))
        if sample is None:
            return
        self.notification_count += 1
        quaternion = sample["quaternion"]

        if self.reference_quaternion is None:
            if self.reference_started_at is None:
                self.reference_started_at = time.monotonic()
                print(
                    f"Référence K-Move pendant {self.reference_duration:.1f} s: "
                    "maintenir le capteur immobile.",
                    flush=True,
                )
            self.reference_samples.append(quaternion)
            if time.monotonic() - self.reference_started_at < self.reference_duration:
                return
            average = tuple(
                sum(values) / len(self.reference_samples)
                for values in zip(*self.reference_samples)
            )
            norm = sum(value * value for value in average) ** 0.5
            self.reference_quaternion = tuple(value / norm for value in average)
            print("Référence K-Move enregistrée. Les trois axes sont à zéro.", flush=True)

        relative = relative_quaternion(self.reference_quaternion, quaternion)
        angles = quaternion_to_euler_degrees(relative)
        self.latest = {**sample, **angles}
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

        if time.monotonic() - self.last_measurement_print >= self.print_interval:
            print(
                f"{timestamp} | "
                f"X={angles['rotation_x_deg']:+7.2f}° | "
                f"Y={angles['rotation_y_deg']:+7.2f}° | "
                f"Z={angles['rotation_z_deg']:+7.2f}° | "
                f"batterie={sample['battery_pct']}%",
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
                f"Connexion K-Move Bumble à {remote_address} "
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

            for command, delay in INIT_COMMANDS:
                await self.write(client, command)
                await asyncio.sleep(max(0.0, delay - self.write_delay))

            # Dans la capture officielle K-Move, 0x11 ouvre le flux utilisé
            # par la prise de référence.
            await self.write(client, b"\x11")

            print("Acquisition K-Move Bumble démarrée.", flush=True)
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


def build_parser():
    parser = argparse.ArgumentParser(
        description="Acquisition K-Move via Bumble.",
    )
    parser.add_argument("--transport", default=DEFAULT_BUMBLE_TRANSPORT)
    parser.add_argument("--address", default=KMOVE)
    parser.add_argument(
        "--address-type",
        choices=["public", "random"],
        default="public",
    )
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--reference-duration", type=float, default=2.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--write-delay", type=float, default=0.05)
    parser.add_argument("--print-interval", type=float, default=0.1)
    parser.add_argument("--keepalive-interval", type=float, default=10.0)
    parser.add_argument("--csv")
    return parser


def main():
    args = build_parser().parse_args()
    client = KMoveBumbleClient(
        transport=args.transport,
        address=args.address,
        address_type=args.address_type,
        csv_path=args.csv,
        reference_duration=args.reference_duration,
        print_interval=args.print_interval,
        write_delay=args.write_delay,
        keepalive_interval=args.keepalive_interval,
    )
    try:
        asyncio.run(client.run(args.duration, args.connect_timeout))
    except BumbleBackendError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        client.close()
    print(f"Notifications: {client.notification_count}", flush=True)


if __name__ == "__main__":
    main()
