"""Découverte et diagnostic GATT de l'ANR M40 via BlueZ/Bleak."""

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakScanner


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.anr.driver import ANRDriver  # noqa: E402
from ble.anr.protocol import is_m40_manufacturer_data  # noqa: E402


def now_iso():
    return datetime.now(timezone.utc).isoformat()


async def discover_m40(timeout):
    print(f"Recherche des ANR M40 pendant {timeout:.1f} s...")
    discovered = await BleakScanner.discover(
        timeout=timeout,
        return_adv=True,
    )
    matches = []
    for address, item in discovered.items():
        device, advertisement = item
        if is_m40_manufacturer_data(advertisement.manufacturer_data):
            matches.append(
                {
                    "address": address,
                    "name": device.name or advertisement.local_name or "ANR M40",
                    "rssi": advertisement.rssi,
                }
            )
    return matches


async def run(args):
    if args.address:
        address = args.address
    else:
        devices = await discover_m40(args.scan_timeout)
        if not devices:
            raise RuntimeError("Aucun ANR M40 détecté (Company ID 0x05DA).")
        for index, device in enumerate(devices, start=1):
            print(
                f"{index}. {device['name']} | {device['address']} | "
                f"RSSI={device['rssi']} dBm"
            )
        address = devices[0]["address"]
        print(f"Utilisation automatique de {address}.")

    driver = ANRDriver(address)
    rows = []
    try:
        print(f"Connexion au M40 {address}...")
        await driver.connect()
        identity = await driver.read_identity()
        battery = await driver.read_battery()
        print(
            "Identité: "
            + " | ".join(
                f"{key}={value or 'indisponible'}"
                for key, value in identity.items()
            )
        )
        print(
            f"Batterie: {battery} %"
            if battery is not None
            else "Batterie: non disponible (firmware antérieur à v1.5 possible)"
        )
        await driver.set_device_id(args.device_id)
        print(f"Couleur/Device ID réglé sur {args.device_id}.")

        started = asyncio.get_running_loop().time()

        def on_emg(value):
            elapsed = asyncio.get_running_loop().time() - started
            rows.append([now_iso(), round(elapsed, 3), value])
            print(f"{elapsed:6.2f} s | EMG={value:4d} / 1023")

        await driver.start_emg(on_emg)
        print(f"Notifications EMG pendant {args.duration:.1f} s...")
        await asyncio.sleep(args.duration)
        await driver.stop_emg()
    finally:
        await driver.disconnect()

    if args.csv:
        path = Path(args.csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerow(["timestamp_utc", "elapsed_seconds", "emg_raw"])
            writer.writerows(rows)
        print(f"CSV enregistré: {path}")
    print(f"Notifications reçues: {len(rows)}")


def build_parser():
    parser = argparse.ArgumentParser(description="Diagnostic de l'ANR M40.")
    parser.add_argument("--address")
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--device-id", type=int, default=1)
    parser.add_argument("--csv")
    return parser


def main():
    args = build_parser().parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
