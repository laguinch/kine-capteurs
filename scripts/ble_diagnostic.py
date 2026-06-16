import argparse
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakClient, BleakScanner


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_hex(value: str) -> bytes:
    cleaned = value.replace("0x", "").replace(",", " ").replace(":", " ")
    cleaned = " ".join(cleaned.split())
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Valeur hexadecimale invalide: {value!r}"
        ) from exc


def print_device(device, advertisement_data=None) -> None:
    name = device.name or "(sans nom)"
    print(f"{device.address} | {name}")
    if advertisement_data is None:
        return

    if advertisement_data.rssi is not None:
        print(f"  RSSI: {advertisement_data.rssi} dBm")
    if advertisement_data.local_name:
        print(f"  Local name: {advertisement_data.local_name}")
    if advertisement_data.service_uuids:
        print("  Services annonces:")
        for uuid in advertisement_data.service_uuids:
            print(f"    - {uuid}")
    if advertisement_data.manufacturer_data:
        print("  Manufacturer data:")
        for company_id, payload in advertisement_data.manufacturer_data.items():
            print(f"    - {company_id}: {payload.hex(' ')}")
    if advertisement_data.service_data:
        print("  Service data:")
        for uuid, payload in advertisement_data.service_data.items():
            print(f"    - {uuid}: {payload.hex(' ')}")


async def scan(timeout: float) -> None:
    print(f"Scan BLE pendant {timeout:.1f} s...")
    try:
        results = await BleakScanner.discover(
            timeout=timeout,
            return_adv=True,
        )
    except TypeError:
        devices = await BleakScanner.discover(timeout=timeout)
        for device in devices:
            print_device(device)
        return

    for device, advertisement_data in results.values():
        print_device(device, advertisement_data)


async def choose_device(name_filter: str, timeout: float):
    target = name_filter.lower()
    print(f"Recherche d'un appareil contenant {name_filter!r} pendant {timeout:.1f} s...")
    devices = await BleakScanner.discover(timeout=timeout)
    matches = [
        device
        for device in devices
        if device.name and target in device.name.lower()
    ]

    if not matches:
        raise SystemExit(f"Aucun appareil trouve avec le nom contenant {name_filter!r}")
    if len(matches) > 1:
        print("Plusieurs appareils trouves, precise --address avec l'un de ceux-ci:")
        for device in matches:
            print_device(device)
        raise SystemExit(2)

    return matches[0]


async def find_device_by_address(address: str, timeout: float):
    target = address.lower()
    print(f"Recherche de {address} pendant {timeout:.1f} s avant connexion...")
    devices = await BleakScanner.discover(timeout=timeout)
    for device in devices:
        if device.address.lower() == target:
            name = device.name or "(sans nom)"
            print(f"Appareil trouve: {device.address} | {name}")
            return device

    print("Appareil non retrouve pendant le scan, tentative avec l'adresse directe.")
    return address


async def list_services(client: BleakClient) -> None:
    await asyncio.sleep(1.0)
    if hasattr(client, "get_services"):
        services = await client.get_services()
    else:
        services = client.services

    services = list(services)
    if not services:
        print("Aucun service GATT remonte par Bleak/BlueZ.")
        print("Essaie de relancer la commande, ou de redemarrer le Bluetooth:")
        print("  sudo systemctl restart bluetooth")
        return

    for service in services:
        print(f"\nService {service.uuid}")
        if service.description:
            print(f"  Description: {service.description}")
        for char in service.characteristics:
            props = ", ".join(char.properties)
            print(f"  Characteristic {char.uuid}")
            print(f"    Properties: {props}")
            if char.description:
                print(f"    Description: {char.description}")
            if char.descriptors:
                print("    Descriptors:")
                for descriptor in char.descriptors:
                    print(f"      - {descriptor.uuid} handle={descriptor.handle}")


def make_notify_callback(csv_writer=None, csv_file=None):
    def callback(sender, data: bytearray) -> None:
        timestamp = now_iso()
        payload = bytes(data)
        print(f"{timestamp} | {sender} | len={len(payload)} | {payload.hex(' ')}")

        if csv_writer is not None:
            csv_writer.writerow([timestamp, str(sender), len(payload), payload.hex(" ")])
            csv_file.flush()

    return callback


async def connect_and_diagnose(args) -> None:
    device = None
    if args.name and not args.address:
        device = await choose_device(args.name, args.scan_timeout)
        address = device.address
    else:
        address = args.address

    if not address:
        raise SystemExit("Indique --address ADRESSE ou --name NOM.")

    if device is None:
        device = await find_device_by_address(address, args.scan_timeout)

    print(f"Connexion a {address}...")
    try:
        async with BleakClient(device, timeout=args.connect_timeout) as client:
            print(f"Connecte: {client.is_connected}")

            if args.services:
                await list_services(client)

            for char_uuid, payload in args.write:
                print(f"WRITE {char_uuid}: {payload.hex(' ')}")
                await client.write_gatt_char(char_uuid, payload, response=args.write_response)
                await asyncio.sleep(args.write_delay)

            csv_file = None
            csv_writer = None
            if args.csv:
                csv_path = Path(args.csv)
                csv_path.parent.mkdir(parents=True, exist_ok=True)
                csv_file = csv_path.open("w", newline="", encoding="utf-8")
                csv_writer = csv.writer(csv_file)
                csv_writer.writerow(["timestamp_utc", "sender", "length", "data_hex"])

            notify_started = []
            try:
                if args.notify:
                    callback = make_notify_callback(csv_writer, csv_file)
                    for char_uuid in args.notify:
                        print(f"NOTIFY start {char_uuid}")
                        await client.start_notify(char_uuid, callback)
                        notify_started.append(char_uuid)

                    print(f"Ecoute pendant {args.duration:.1f} s...")
                    await asyncio.sleep(args.duration)
            finally:
                for char_uuid in notify_started:
                    print(f"NOTIFY stop {char_uuid}")
                    await client.stop_notify(char_uuid)
                if csv_file is not None:
                    csv_file.close()
    except TimeoutError:
        print("Connexion impossible: timeout.")
        print("Verifie que le capteur est reveille, proche, et non connecte a l'application Kinvent.")
        print("Tu peux aussi relancer:")
        print("  sudo systemctl restart bluetooth")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnostic BLE pour les capteurs Kinvent et autres peripheriques BLE.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Lister les appareils BLE visibles.",
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=8.0,
        help="Duree du scan BLE en secondes.",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=20.0,
        help="Duree maximale de connexion en secondes.",
    )
    parser.add_argument(
        "--address",
        help="Adresse BLE/MAC de l'appareil a diagnostiquer.",
    )
    parser.add_argument(
        "--name",
        help="Nom ou partie du nom BLE a rechercher si l'adresse est inconnue.",
    )
    parser.add_argument(
        "--services",
        action="store_true",
        help="Lister les services et caracteristiques apres connexion.",
    )
    parser.add_argument(
        "--notify",
        action="append",
        default=[],
        help="UUID d'une caracteristique a ecouter. Option repetable.",
    )
    parser.add_argument(
        "--write",
        nargs=2,
        metavar=("CHAR_UUID", "HEX"),
        action="append",
        default=[],
        type=str,
        help="Envoyer des octets hexadecimaux vers une caracteristique. Option repetable.",
    )
    parser.add_argument(
        "--write-response",
        action="store_true",
        help="Utiliser une ecriture BLE avec reponse.",
    )
    parser.add_argument(
        "--write-delay",
        type=float,
        default=0.3,
        help="Pause entre deux commandes write.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Duree d'ecoute des notifications en secondes.",
    )
    parser.add_argument(
        "--csv",
        help="Chemin d'un fichier CSV pour enregistrer les notifications brutes.",
    )
    return parser


def normalize_args(args) -> None:
    normalized_writes = []
    for char_uuid, hex_value in args.write:
        normalized_writes.append((char_uuid, parse_hex(hex_value)))
    args.write = normalized_writes


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    normalize_args(args)

    if args.scan:
        await scan(args.scan_timeout)
        return

    await connect_and_diagnose(args)


if __name__ == "__main__":
    asyncio.run(main())
