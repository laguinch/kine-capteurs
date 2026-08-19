"""Diagnostic ANR M40 via Bumble et controleur HCI USB.

Le M40 expose un profil GATT standard. Ce script evite BlueZ/Bleak et utilise
Bumble directement sur le dongle HCI USB, puis reutilise le decodeur ANR deja
present dans le projet.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.anr.protocol import (  # noqa: E402
    decode_battery,
    decode_emg,
    encode_device_id,
)
from ble.kinvent.bumble_backend import (  # noqa: E402
    DEFAULT_BUMBLE_TRANSPORT,
    BumbleBackendError,
    require_bumble,
)
from scripts.kinvent_kpush_bumble import make_remote_address  # noqa: E402


NRF52840_STABLE_RANDOM_OWN_ADDRESS_TYPE = 1
CONNECT_SCAN_INTERVAL_MS = 10
CONNECT_SCAN_WINDOW_MS = 10
ANR_ANALOG_VALUE_HANDLE = 0x001E
ANR_ANALOG_CCCD_HANDLE = 0x001F
ANR_DEVICE_ID_VALUE_HANDLE = 0x0021
ANR_BATTERY_VALUE_HANDLE = 0x0025
HCI_REASON_LABELS = {
    0x08: "supervision timeout",
    0x13: "remote user terminated",
    0x16: "local host terminated",
    0x22: "LMP response timeout",
    0x3E: "connection failed to establish",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def decode_text(value):
    return bytes(value).decode("utf-8", errors="replace").strip("\x00")


def control_requests_disconnect(control_file):
    if not control_file:
        return False
    try:
        payload = json.loads(Path(control_file).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return payload.get("action") == "disconnect"


def advertisement_address(args):
    if not args:
        return None
    first = args[0]
    if hasattr(first, "address"):
        return first.address
    return first


def advertisement_text(args):
    parts = []
    for item in args:
        for attr in ("name", "local_name", "complete_name"):
            value = getattr(item, attr, None)
            if value:
                parts.append(str(value))
        for attr in ("data", "advertising_data"):
            value = getattr(item, attr, None)
            if value:
                parts.append(str(value))
    return " ".join(parts)


def normalize_address(value):
    if value is None:
        return ""
    return str(value).split("/")[0].upper()


def format_hci_reason(reason):
    try:
        value = int(reason)
    except (TypeError, ValueError):
        return repr(reason)
    label = HCI_REASON_LABELS.get(value)
    if label:
        return f"0x{value:02x} ({label})"
    return f"0x{value:02x}"


def looks_like_m40(args):
    text = advertisement_text(args).upper()
    # Le scan Bumble n'expose pas toujours les manufacturer data sous la meme
    # forme selon la version. On privilegie les noms visibles et le Company ID
    # ANR en little endian quand il apparait dans la representation texte.
    return (
        "M40" in text
        or "ANR" in text
        or "05DA" in text
        or "DA 05" in text
        or "0X05DA" in text
    )


async def discover_m40(device, timeout):
    from bumble.hci import HCI_LE_1M_PHY

    loop = asyncio.get_running_loop()
    found = []
    seen = set()

    def on_advertisement(*args):
        address = normalize_address(advertisement_address(args))
        if not address or address in seen or not looks_like_m40(args):
            return
        seen.add(address)
        text = advertisement_text(args)
        print(f"M40 possible: {address} {text}".strip(), flush=True)
        found.append((address, text))

    handler = device.on("advertisement", on_advertisement)
    try:
        await device.start_scanning(
            legacy=True,
            active=True,
            scan_interval=CONNECT_SCAN_INTERVAL_MS,
            scan_window=CONNECT_SCAN_WINDOW_MS,
            own_address_type=NRF52840_STABLE_RANDOM_OWN_ADDRESS_TYPE,
            filter_duplicates=False,
            scanning_phys=(HCI_LE_1M_PHY,),
        )
        await asyncio.sleep(timeout)
    finally:
        if handler is not None:
            device.remove_listener("advertisement", handler)
        await device.stop_scanning()
    return found


class ANRM40BumbleClient:
    def __init__(
        self,
        transport,
        address,
        address_type,
        csv_path=None,
        device_id=1,
        print_interval=0.1,
        control_file=None,
    ):
        self.transport = transport
        self.address = address
        self.address_type = address_type
        self.device_id = device_id
        self.print_interval = print_interval
        self.control_file = control_file
        self.notification_count = 0
        self.latest_emg = None
        self.started_at = None
        self.last_print = 0.0
        self.csv_path = Path(csv_path) if csv_path else None
        self.csv_file = None
        self.csv_writer = None
        self.disconnect_reason = None

        if self.csv_path:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            self.csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(["timestamp_utc", "elapsed_seconds", "emg_raw"])
            self.csv_file.flush()

    def close(self):
        if self.csv_file:
            self.csv_file.close()
            self.csv_file = None

    def handle_emg(self, value):
        self.notification_count += 1
        try:
            emg = decode_emg(bytes(value))
        except ValueError as exc:
            print(f"Trame EMG rejetee: {bytes(value).hex(' ')} ({exc})", flush=True)
            return

        self.latest_emg = emg
        elapsed = 0.0 if self.started_at is None else time.monotonic() - self.started_at
        timestamp = now_iso()

        if self.csv_writer:
            self.csv_writer.writerow([timestamp, round(elapsed, 6), emg])
            self.csv_file.flush()

        if time.monotonic() - self.last_print >= self.print_interval:
            print(f"{elapsed:6.2f} s | EMG={emg:4d} / 1023", flush=True)
            self.last_print = time.monotonic()

    def register_disconnect_logger(self, connection):
        def log_disconnection(reason=None, *args, **kwargs):
            self.disconnect_reason = reason
            print(
                f"Déconnexion ANR M40: {format_hci_reason(reason)}",
                flush=True,
            )

        on_event = getattr(connection, "on", None)
        if not callable(on_event):
            return
        for event_name in ("disconnection", "disconnect"):
            try:
                on_event(event_name, log_disconnection)
            except Exception:
                continue

    async def configure_fixed_handles(self, client):
        client.notification_subscribers.setdefault(
            ANR_ANALOG_VALUE_HANDLE,
            set(),
        ).add(self.handle_emg)

        print(
            f"Device ID M40: écriture handle=0x{ANR_DEVICE_ID_VALUE_HANDLE:04x} "
            f"{self.device_id:02x}",
            flush=True,
        )
        await client.write_value(
            ANR_DEVICE_ID_VALUE_HANDLE,
            encode_device_id(self.device_id),
            with_response=True,
        )

        print(
            f"Batterie M40: lecture handle=0x{ANR_BATTERY_VALUE_HANDLE:04x}",
            flush=True,
        )
        try:
            battery_value = await client.read_value(ANR_BATTERY_VALUE_HANDLE)
            print(f"Batterie M40: {decode_battery(bytes(battery_value))} %", flush=True)
        except Exception as exc:
            print(f"Batterie M40: non disponible ({type(exc).__name__}).", flush=True)

        print(
            f"Activation notifications EMG: écriture handle=0x{ANR_ANALOG_CCCD_HANDLE:04x} "
            "01 00",
            flush=True,
        )
        await client.write_value(
            ANR_ANALOG_CCCD_HANDLE,
            b"\x01\x00",
            with_response=True,
        )

    async def run(self, duration, scan_timeout=10.0, connect_timeout=15.0):
        require_bumble()
        import importlib

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

            address = self.address
            if not address:
                print(f"Recherche des ANR M40 pendant {scan_timeout:.1f} s...")
                found = await discover_m40(device, scan_timeout)
                if not found:
                    raise RuntimeError("Aucun ANR M40 detecte via Bumble.")
                address = found[0][0]
                print(f"Utilisation automatique de {address}.")

            remote_address = make_remote_address(address, self.address_type, Address)
            print(
                f"Connexion ANR M40 Bumble a {remote_address} "
                f"via {self.transport}...",
                flush=True,
            )
            bumble_device = importlib.import_module("bumble.device")
            original_connect_scan_interval = (
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL
            )
            original_connect_scan_window = (
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_WINDOW
            )
            try:
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL = (
                    CONNECT_SCAN_INTERVAL_MS
                )
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_WINDOW = (
                    CONNECT_SCAN_WINDOW_MS
                )
                connection = await device.connect(
                    remote_address,
                    own_address_type=NRF52840_STABLE_RANDOM_OWN_ADDRESS_TYPE,
                    timeout=connect_timeout,
                )
            finally:
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL = (
                    original_connect_scan_interval
                )
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_WINDOW = (
                    original_connect_scan_window
                )
            print("Connexion BLE etablie.", flush=True)
            self.register_disconnect_logger(connection)
            client = connection.gatt_client

            await self.configure_fixed_handles(client)
            self.started_at = time.monotonic()
            print("ANR M40 prêt; liaison Bluetooth conservée.", flush=True)
            if duration > 0:
                print(f"Notifications EMG pendant {duration:.1f} s...", flush=True)
            else:
                print("Flux ANR M40 actif.", flush=True)
            start = time.monotonic()
            deadline = None if duration <= 0 else start + duration
            next_progress = start

            while deadline is None or time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                if control_requests_disconnect(self.control_file):
                    print(
                        "Déconnexion ANR M40 demandée par le gestionnaire.",
                        flush=True,
                    )
                    break
                now = time.monotonic()
                if deadline is not None and now >= next_progress:
                    remaining = max(0.0, deadline - now)
                    print(f"Temps restant: {remaining:4.1f} s", flush=True)
                    next_progress = now + 5.0

            if deadline is None:
                print("Flux ANR M40 conservé au repos.", flush=True)
            await connection.disconnect()


def build_parser():
    parser = argparse.ArgumentParser(description="Diagnostic ANR M40 via Bumble.")
    parser.add_argument("--transport", default=DEFAULT_BUMBLE_TRANSPORT)
    parser.add_argument("--address")
    parser.add_argument(
        "--address-type",
        choices=["public", "random"],
        default="public",
    )
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--device-id", type=int, default=1)
    parser.add_argument("--print-interval", type=float, default=0.1)
    parser.add_argument("--csv")
    parser.add_argument("--control-file")
    return parser


def main():
    args = build_parser().parse_args()
    client = ANRM40BumbleClient(
        transport=args.transport,
        address=args.address,
        address_type=args.address_type,
        csv_path=args.csv,
        device_id=args.device_id,
        print_interval=args.print_interval,
        control_file=args.control_file,
    )
    try:
        asyncio.run(
            client.run(
                args.duration,
                scan_timeout=args.scan_timeout,
                connect_timeout=args.connect_timeout,
            )
        )
    except BumbleBackendError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        client.close()
    print(f"Notifications recues: {client.notification_count}", flush=True)


if __name__ == "__main__":
    main()
