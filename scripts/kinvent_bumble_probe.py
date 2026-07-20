"""Diagnostic Kinvent via Bumble.

Cet outil sert à valider le futur contrôleur nRF52840/Bumble sans modifier le
service clinique actuel. Il utilise la pile GATT de Bumble, mais conserve les
commandes Kinvent déjà extraites des captures officielles.
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

from ble.kinvent.bumble_backend import (  # noqa: E402
    DEFAULT_BUMBLE_TRANSPORT,
    BumbleBackendError,
    require_bumble,
)
from scripts.kinvent_raw_hci import INIT_COMMANDS as FORCE_INIT_COMMANDS  # noqa: E402
from scripts.kinvent_raw_hci import (  # noqa: E402
    ALT_NOTIFY_CCCD_HANDLE,
    UART_CCCD_HANDLE,
    UART_VALUE_HANDLE,
)
from scripts.kinvent_kmove_hci import INIT_COMMANDS as KMOVE_INIT_COMMANDS  # noqa: E402


KINVENT_NOTIFY_UUIDS = [
    "49535343-1e4d-4bd9-ba61-23c647249616",
    "49535343-4c8a-39b3-2f49-511cff073b7e",
]
KINVENT_WRITE_UUID = "49535343-8841-43f4-a8d4-ecbe34729bb3"
ALT_NOTIFY_VALUE_HANDLE = ALT_NOTIFY_CCCD_HANDLE - 1


def official_commands(profile):
    if profile == "force":
        return [(command, 0.05) for command in FORCE_INIT_COMMANDS]
    if profile == "kmove":
        return KMOVE_INIT_COMMANDS
    return []


def now_seconds():
    return time.time()


async def discover_characteristics(client):
    await client.discover_services()
    for service in client.services:
        await service.discover_characteristics()
        for characteristic in service.characteristics:
            await characteristic.discover_descriptors()


def characteristic_by_uuid(client, uuid):
    matches = client.get_characteristics_by_uuid(uuid)
    return matches[0] if matches else None


def make_remote_address(address, address_type, Address):
    if address_type == "public":
        return Address(address, Address.PUBLIC_DEVICE_ADDRESS)
    return Address(address, Address.RANDOM_DEVICE_ADDRESS)


async def run_probe(args):
    require_bumble()
    from bumble.device import Device
    from bumble.hci import Address
    from bumble.transport import open_transport

    csv_file = None
    writer = None
    if args.csv:
        csv_path = Path(args.csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_file = csv_path.open("w", newline="", encoding="utf-8")
        writer = csv.writer(csv_file)
        writer.writerow(["elapsed_seconds", "source", "payload_hex"])

    started_at = now_seconds()

    def on_notify(source):
        def callback(value):
            elapsed = now_seconds() - started_at
            payload = bytes(value)
            print(f"{elapsed:8.3f}s {source}: {payload.hex(' ')}", flush=True)
            if writer:
                writer.writerow([f"{elapsed:.6f}", source, payload.hex(" ")])
                csv_file.flush()

        return callback

    async with await open_transport(args.transport) as hci_transport:
        device = Device.with_hci(
            "Kine Capteurs Bumble",
            Address("F0:F1:F2:F3:F4:F5"),
            hci_transport.source,
            hci_transport.sink,
        )
        await device.power_on()
        remote_address = make_remote_address(args.address, args.address_type, Address)
        print(
            "Connexion Bumble à "
            f"{remote_address} via {args.transport} "
            f"(timeout {args.connect_timeout:.1f} s)...",
            flush=True,
        )
        try:
            connection = await asyncio.wait_for(
                device.connect(remote_address),
                timeout=args.connect_timeout,
            )
        except TimeoutError as exc:
            raise SystemExit(
                "Connexion Bumble expirée. Le contrôleur nRF est ouvert, "
                "mais le capteur n'a pas accepté la connexion BLE. Vérifiez "
                "que le capteur est allumé, non connecté ailleurs, et que "
                "l'adresse/type correspondent aux captures officielles."
            ) from exc
        print("Connexion BLE établie.", flush=True)
        client = connection.gatt_client

        if args.gatt_mode == "official-handles":
            client.notification_subscribers.setdefault(
                UART_VALUE_HANDLE,
                set(),
            ).add(on_notify(f"handle-0x{UART_VALUE_HANDLE:04x}"))
            client.notification_subscribers.setdefault(
                ALT_NOTIFY_VALUE_HANDLE,
                set(),
            ).add(on_notify(f"handle-0x{ALT_NOTIFY_VALUE_HANDLE:04x}"))

            print(
                f"SEND 10 avant CCCD, comme dans la capture officielle.",
                flush=True,
            )
            await client.write_value(UART_VALUE_HANDLE, b"\x10", with_response=False)
            await asyncio.sleep(args.write_delay)

            print(
                f"Activation notification UART sur 0x{UART_CCCD_HANDLE:04x}...",
                flush=True,
            )
            await client.write_value(
                UART_CCCD_HANDLE,
                b"\x01\x00",
                with_response=True,
            )
            write_target = UART_VALUE_HANDLE
            commands = official_commands(args.profile)
        else:
            await asyncio.wait_for(
                discover_characteristics(client),
                timeout=args.discovery_timeout,
            )
            notify_count = 0
            for uuid in KINVENT_NOTIFY_UUIDS:
                characteristic = characteristic_by_uuid(client, uuid)
                if characteristic:
                    await characteristic.subscribe(on_notify(uuid))
                    notify_count += 1
                    print(f"Notifications activées: {uuid}", flush=True)

            write_characteristic = characteristic_by_uuid(client, KINVENT_WRITE_UUID)
            if write_characteristic:
                write_target = write_characteristic
                print(f"Écriture via caractéristique {KINVENT_WRITE_UUID}", flush=True)
            else:
                write_target = UART_VALUE_HANDLE
                print(
                    "Caractéristique d'écriture non trouvée par UUID; "
                    f"écriture sur handle officiel 0x{UART_VALUE_HANDLE:04x}.",
                    flush=True,
                )

            if notify_count == 0:
                print(
                    "Aucune caractéristique de notification Kinvent trouvée.",
                    flush=True,
                )
            commands = official_commands(args.profile)

        for command, delay in commands:
            print(f"SEND {command.hex(' ')}", flush=True)
            await client.write_value(write_target, command, with_response=False)
            await asyncio.sleep(delay)

        if args.wake:
            print("SEND 11", flush=True)
            await client.write_value(write_target, b"\x11", with_response=False)

        end_at = started_at + args.duration
        while now_seconds() < end_at:
            if args.keepalive_interval > 0:
                await asyncio.sleep(min(args.keepalive_interval, end_at - now_seconds()))
                if now_seconds() < end_at:
                    print("SEND ff", flush=True)
                    await client.write_value(write_target, b"\xff", with_response=False)
            else:
                await asyncio.sleep(0.2)

        await connection.disconnect()

    if csv_file:
        csv_file.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default=DEFAULT_BUMBLE_TRANSPORT)
    parser.add_argument("--address", required=True)
    parser.add_argument(
        "--address-type",
        choices=["public", "random"],
        default="public",
        help=(
            "Type d'adresse BLE du capteur. Les pilotes HCI Kinvent validés "
            "utilisent public par défaut."
        ),
    )
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--discovery-timeout", type=float, default=15.0)
    parser.add_argument("--write-delay", type=float, default=0.05)
    parser.add_argument(
        "--gatt-mode",
        choices=["official-handles", "discovery"],
        default="official-handles",
        help=(
            "official-handles utilise les handles Kinvent observés dans les "
            "captures HCI; discovery lance une découverte GATT Bumble complète."
        ),
    )
    parser.add_argument(
        "--profile",
        choices=["none", "force", "kmove"],
        default="none",
        help=(
            "Séquence officielle à envoyer: force pour K-Push/K-Pull/"
            "K-Force Plate seule, kmove pour K-Move, none pour découverte seule."
        ),
    )
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--csv")
    parser.add_argument(
        "--wake",
        action="store_true",
        help="Envoie 0x11 après l'initialisation, comme au démarrage de test observé.",
    )
    parser.add_argument(
        "--keepalive-interval",
        type=float,
        default=0.0,
        help=(
            "Intervalle d'envoi de 0xFF. Laisser à 0 sauf reproduction "
            "explicite d'une capture officielle."
        ),
    )
    args = parser.parse_args()
    try:
        asyncio.run(run_probe(args))
    except BumbleBackendError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
