"""Acquisition double K-Force Plates via Bumble et contrôleur HCI USB.

Le transport Bluetooth est assuré par Bumble, mais le protocole capteur reste
celui observé dans les captures officielles Kinvent : handles UART fixes,
activation CCCD officielle, réglage radio officiel et mêmes commandes
d'initialisation.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ble.common.devices import KPLATE_LEFT, KPLATE_RIGHT  # noqa: E402
from ble.kinvent.bumble_backend import (  # noqa: E402
    DEFAULT_BUMBLE_TRANSPORT,
    BumbleBackendError,
    require_bumble,
)
from scripts.kinvent_dual_hci import (  # noqa: E402
    KPLATE_INIT_STEPS,
    DualKinventClient,
)
from scripts.kinvent_kpush_bumble import make_remote_address  # noqa: E402
from scripts.kinvent_raw_hci import (  # noqa: E402
    UART_CCCD_HANDLE,
    UART_VALUE_HANDLE,
)


KPLATE_MODEL_NUMBER_HANDLE = 0x0016


def connected_sides(plates):
    return ", ".join(plate.side for plate in plates)


class KPlatesBumbleClient:
    def __init__(
        self,
        transport,
        left_address,
        right_address,
        address_type,
        csv_path,
        tare_duration=2.0,
        print_interval=0.5,
        sync_tolerance_ms=20.0,
        calibration_path=None,
        recalibrate=False,
        write_delay=0.05,
        keepalive_interval=10.0,
    ):
        self.transport = transport
        self.address_type = address_type
        self.write_delay = write_delay
        self.dual = DualKinventClient(
            adapter=0,
            left_address=left_address,
            right_address=right_address,
            csv_path=csv_path,
            tare_duration=tare_duration,
            print_interval=print_interval,
            sync_tolerance_ms=sync_tolerance_ms,
            calibration_path=calibration_path,
            recalibrate=recalibrate,
        )
        self.dual.keepalive_interval = keepalive_interval
        self.connections = {}
        self.disconnected = set()

    def close(self):
        self.dual.close()

    def handle_payload(self, plate, payload):
        plate.notifications += 1
        plate.last_notification_at = time.monotonic()
        sample = plate.decode(bytes(payload))
        if sample:
            self.dual.pair_samples()

    def register_disconnect_logger(self, connection, plate):
        def log_disconnection(reason=None, *args, **kwargs):
            if reason is None and args:
                reason = args[0]
            print(
                f"Déconnexion Bumble {plate.side}: {reason!r}",
                flush=True,
            )
            plate.handle = None
            self.disconnected.add(plate)

        on_event = getattr(connection, "on", None)
        if not callable(on_event):
            return
        for event_name in ("disconnection", "disconnect"):
            try:
                on_event(event_name, log_disconnection)
            except Exception:
                continue

    async def write_plate(self, client, plate, value, with_response=False):
        print(f"SEND {plate.side} {value.hex(' ')}", flush=True)
        await client.write_value(
            UART_VALUE_HANDLE,
            value,
            with_response=with_response,
        )

    async def write_all(self, clients, value, delay):
        # Le pilote HCI validé par les captures Kinvent envoie chaque commande
        # plaque par plaque, puis laisse le contrôleur traiter les événements.
        # On garde ce rythme ici au lieu de paralléliser les écritures GATT :
        # la couche Bumble transporte le Bluetooth, mais la cadence capteur
        # reste celle observée officiellement.
        for plate, client in clients.items():
            if plate in self.disconnected or plate.handle is None:
                continue
            await self.write_plate(client, plate, value)
        await asyncio.sleep(delay)

    async def connect_plate(self, device, plate, Address, connect_timeout):
        remote_address = make_remote_address(
            plate.address,
            self.address_type,
            Address,
        )
        print(
            f"Connexion plateforme {plate.side} Bumble à {remote_address}...",
            flush=True,
        )
        try:
            connection = await asyncio.wait_for(
                device.connect(remote_address),
                timeout=connect_timeout,
            )
        except Exception as exc:
            if "MEMORY_CAPACITY_EXCEEDED_ERROR" in str(exc):
                raise RuntimeError(
                    "Le contrôleur nRF52840 refuse une deuxième connexion BLE. "
                    "Reflashez le firmware HCI USB avec "
                    "firmware/nrf52840_hci_usb/prj.conf "
                    "(CONFIG_BT_MAX_CONN=2)."
                ) from exc
            raise
        plate.handle = getattr(connection, "handle", 1)
        print(
            f"Plateforme {plate.side} connectée, handle Bumble "
            f"0x{plate.handle:04x}.",
            flush=True,
        )
        return connection

    async def configure_streams(self, clients):
        connected = list(clients.keys())
        if not connected:
            raise RuntimeError("Aucune plateforme connectée à initialiser.")

        await self.write_all(clients, b"\x10", max(0.02, min(self.write_delay, 0.10)))

        print(
            f"Activation notification UART sur 0x{UART_CCCD_HANDLE:04x} "
            f"pour {connected_sides(connected)}...",
            flush=True,
        )
        for plate, client in clients.items():
            print(f"CCCD {plate.side}...", flush=True)
            try:
                await client.write_value(
                    UART_CCCD_HANDLE,
                    b"\x01\x00",
                    with_response=True,
                )
            except BaseException as exc:
                print(
                    f"Échec CCCD {plate.side}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                raise

        await self.write_all(clients, b"\x10", 0.25)

        # Réglage radio observé dans le pilote HCI officiel :
        # intervalle 0x0009-0x0018, latence 0, supervision 0x0200.
        for plate in connected:
            connection = self.connections[plate]
            if plate in self.disconnected:
                continue
            print(f"Réglage radio {plate.side}...", flush=True)
            await connection.update_parameters(0x0009, 0x0018, 0, 0x0200)

        for command, delay in KPLATE_INIT_STEPS:
            await self.write_all(clients, command, delay)

        for plate in connected:
            print(f"Flux {plate.side} démarré.", flush=True)

    async def run_official_gatt_preflight(self, clients):
        # Dans la capture Android Kinvent des deux plateformes, l'application
        # effectue une découverte GATT complète puis lit le modèle au handle
        # 0x0016 avant d'écrire 0x10 et d'activer le CCCD UART.
        # Ce pré-vol reste du GATT standard; il ne remplace ni n'ajoute de
        # commande Kinvent propriétaire.
        connected = list(clients.keys())
        print(
            "Pré-vol GATT officiel pour "
            f"{connected_sides(connected)}...",
            flush=True,
        )
        for plate, client in clients.items():
            print(f"Découverte GATT {plate.side}...", flush=True)
            await client.discover_services()
        for plate, client in clients.items():
            print(f"Lecture modèle {plate.side} sur 0x0016...", flush=True)
            try:
                value = await client.read_value(KPLATE_MODEL_NUMBER_HANDLE)
            except BaseException as exc:
                print(
                    f"Échec lecture modèle {plate.side}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                raise
            print(
                f"Modèle {plate.side}: {bytes(value).hex(' ')}",
                flush=True,
            )

    def selected_plates(self, sides, connection_order):
        plates_by_side = {plate.side: plate for plate in self.dual.plates}
        if sides == "right":
            return [plates_by_side["droite"]]
        if sides == "left":
            return [plates_by_side["gauche"]]
        if connection_order == "left-first":
            return [plates_by_side["gauche"], plates_by_side["droite"]]
        return self.dual.connection_order()

    async def run(
        self,
        duration,
        connect_timeout=15.0,
        sides="both",
        connection_order="right-first",
        diagnostic="stream",
        stream_side="both",
        gatt_preflight="none",
    ):
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

            selected = self.selected_plates(sides, connection_order)
            print(
                "Ordre de connexion Bumble: "
                f"{connected_sides(selected)}",
                flush=True,
            )
            for plate in selected:
                self.connections[plate] = await self.connect_plate(
                    device,
                    plate,
                    Address,
                    connect_timeout,
                )

            if diagnostic == "connect-only":
                print(
                    f"Diagnostic connexion seule pendant {duration:.1f} s...",
                    flush=True,
                )
                start = time.monotonic()
                while time.monotonic() - start < duration:
                    await asyncio.sleep(0.5)
                return

            all_clients = {}
            for plate, connection in self.connections.items():
                self.register_disconnect_logger(connection, plate)
                all_clients[plate] = connection.gatt_client

            if gatt_preflight == "official-discovery":
                await self.run_official_gatt_preflight(all_clients)

            if stream_side == "right":
                stream_plates = [plate for plate in selected if plate.side == "droite"]
            elif stream_side == "left":
                stream_plates = [plate for plate in selected if plate.side == "gauche"]
            else:
                stream_plates = selected
            if not stream_plates:
                raise RuntimeError("Aucune plateforme sélectionnée pour le flux.")
            print(
                "Flux Bumble initialisé pour: "
                f"{connected_sides(stream_plates)}",
                flush=True,
            )

            clients = {}
            for plate, client in all_clients.items():
                if plate not in stream_plates:
                    continue
                client.notification_subscribers.setdefault(
                    UART_VALUE_HANDLE,
                    set(),
                ).add(lambda payload, item=plate: self.handle_payload(item, payload))
                clients[plate] = client

            await self.configure_streams(clients)

            print(f"Acquisition double Bumble pendant {duration:.1f} s...", flush=True)
            start = time.monotonic()
            deadline = start + duration
            next_keepalive = start + self.dual.keepalive_interval
            next_progress = start

            while time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                now = time.monotonic()
                if self.dual.keepalive_interval > 0 and now >= next_keepalive:
                    await self.write_all(clients, b"\xff", 0.0)
                    next_keepalive = now + self.dual.keepalive_interval
                if now >= next_progress:
                    remaining = max(0.0, deadline - now)
                    print(f"Temps restant: {remaining:4.1f} s", flush=True)
                    next_progress = now + 5.0

            for plate, connection in self.connections.items():
                if plate in self.disconnected or plate.handle is None:
                    continue
                try:
                    await connection.disconnect()
                except Exception as exc:
                    print(
                        f"Déconnexion finale ignorée {plate.side}: "
                        f"{type(exc).__name__}: {exc}",
                        flush=True,
                    )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Acquisition double K-Force Plates via Bumble.",
    )
    parser.add_argument("--transport", default=DEFAULT_BUMBLE_TRANSPORT)
    parser.add_argument("--left-address", default=KPLATE_LEFT)
    parser.add_argument("--right-address", default=KPLATE_RIGHT)
    parser.add_argument(
        "--address-type",
        choices=["public", "random"],
        default="public",
    )
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument(
        "--sides",
        choices=["both", "right", "left"],
        default="both",
        help="Diagnostic: connecter les deux plateformes ou une seule.",
    )
    parser.add_argument(
        "--connection-order",
        choices=["right-first", "left-first"],
        default="right-first",
        help="Diagnostic: inverser l'ordre de connexion sans changer les commandes.",
    )
    parser.add_argument(
        "--diagnostic",
        choices=["stream", "connect-only"],
        default="stream",
        help="Diagnostic: connexion seule ou initialisation complète du flux.",
    )
    parser.add_argument(
        "--stream-side",
        choices=["both", "right", "left"],
        default="both",
        help=(
            "Diagnostic: avec les plateformes connectées, démarrer le flux "
            "des deux plateformes ou d'un seul côté."
        ),
    )
    parser.add_argument(
        "--gatt-preflight",
        choices=["none", "official-discovery"],
        default="none",
        help=(
            "Diagnostic: reproduire la découverte GATT et la lecture modèle "
            "observées dans la capture officielle avant le flux UART."
        ),
    )
    parser.add_argument("--tare-duration", type=float, default=2.0)
    parser.add_argument("--connect-timeout", type=float, default=15.0)
    parser.add_argument("--write-delay", type=float, default=0.05)
    parser.add_argument("--print-interval", type=float, default=0.5)
    parser.add_argument("--keepalive-interval", type=float, default=10.0)
    parser.add_argument("--sync-tolerance-ms", type=float, default=20.0)
    parser.add_argument("--calibration-file")
    parser.add_argument("--recalibrate", action="store_true")
    parser.add_argument("--csv", default="storage/raw_data/kplates_bumble.csv")
    return parser


def main():
    args = build_parser().parse_args()
    client = KPlatesBumbleClient(
        transport=args.transport,
        left_address=args.left_address,
        right_address=args.right_address,
        address_type=args.address_type,
        csv_path=args.csv,
        tare_duration=args.tare_duration,
        print_interval=args.print_interval,
        sync_tolerance_ms=args.sync_tolerance_ms,
        calibration_path=args.calibration_file,
        recalibrate=args.recalibrate,
        write_delay=args.write_delay,
        keepalive_interval=args.keepalive_interval,
    )
    try:
        asyncio.run(
            client.run(
                args.duration,
                args.connect_timeout,
                sides=args.sides,
                connection_order=args.connection_order,
                diagnostic=args.diagnostic,
                stream_side=args.stream_side,
                gatt_preflight=args.gatt_preflight,
            )
        )
    except BumbleBackendError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        client.close()

    left, right = client.dual.plates
    print(
        "Acquisition double terminée.",
        flush=True,
    )
    print(
        f"gauche: {left.notifications} notifications, "
        f"{left.measurements} mesure(s), {left.rejected_frames} rejet(s)",
        flush=True,
    )
    print(
        f"droite: {right.notifications} notifications, "
        f"{right.measurements} mesure(s), {right.rejected_frames} rejet(s)",
        flush=True,
    )
    print(
        f"Paires synchronisées: {client.dual.paired_samples} | "
        f"écartées gauche={client.dual.dropped_samples['gauche']}, "
        f"droite={client.dual.dropped_samples['droite']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
