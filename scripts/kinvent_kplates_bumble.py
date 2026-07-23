"""Acquisition double K-Force Plates via Bumble et contrôleur HCI USB.

Le transport Bluetooth est assuré par Bumble, mais le protocole capteur reste
celui observé dans les captures officielles Kinvent : handles UART fixes,
activation CCCD officielle, réglage radio officiel et mêmes commandes
d'initialisation.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
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
from ble.kinvent.kplates.cmj_analysis import detect_stable_body_mass  # noqa: E402
from scripts.kinvent_dual_hci import (  # noqa: E402
    KPLATE_INIT_STEPS,
    KPLATE_PARK_DELAY,
    DualKinventClient,
    now_iso,
)
from scripts.kinvent_kpush_bumble import make_remote_address  # noqa: E402
from scripts.kinvent_raw_hci import (  # noqa: E402
    UART_CCCD_HANDLE,
    UART_VALUE_HANDLE,
)


KPLATE_MODEL_NUMBER_HANDLE = 0x0016
OFFICIAL_CONNECT_INTERVAL_MIN_MS = 30
OFFICIAL_CONNECT_INTERVAL_MAX_MS = 50
OFFICIAL_CONNECT_SUPERVISION_TIMEOUT_MS = 5000
OFFICIAL_CONNECT_SCAN_INTERVAL_MS = 10
OFFICIAL_CONNECT_SCAN_WINDOW_MS = 10
# L'application Android officielle utilise une adresse locale non nulle. Le
# nRF52840 HCI USB Zephyr expose en revanche une adresse publique nulle
# (00:00:00:00:00:00). Bumble initialise une adresse random statique
# F0:F1:F2:F3:F4:F5; on l'utilise donc pour obtenir une identité centrale
# stable et non nulle, au plus proche du comportement effectif d'Android.
NRF52840_STABLE_RANDOM_OWN_ADDRESS_TYPE = 1
OFFICIAL_INITIAL_DISCONNECT_REASON = 0x3E
OFFICIAL_GATT_SETTLE_AFTER_CONNECT_S = 1.47
HCI_REASON_LABELS = {
    0x08: "supervision timeout",
    0x13: "remote user terminated",
    0x16: "local host terminated",
    0x22: "LMP response timeout",
    0x3E: "connection failed to establish",
}


def connected_sides(plates):
    return ", ".join(plate.side for plate in plates)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def format_hci_reason(reason):
    try:
        value = int(reason)
    except (TypeError, ValueError):
        return repr(reason)
    label = HCI_REASON_LABELS.get(value)
    if label:
        return f"0x{value:02x} ({label})"
    return f"0x{value:02x}"


def is_hci_command_disallowed(exc):
    return "COMMAND_DISALLOWED" in str(exc)


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
        self.disconnect_reasons = {}

    def close(self):
        self.dual.close()

    def handle_payload(self, plate, payload):
        plate.notifications += 1
        plate.last_notification_at = time.monotonic()
        sample = plate.decode(bytes(payload))
        if sample:
            if self.dual.acquisition_mode == "cmj":
                self.dual.write_cmj_event(plate)
            else:
                self.dual.pair_samples()

    def connected_side_names(self):
        return [
            plate.side
            for plate in self.dual.plates
            if plate.handle is not None and plate not in self.disconnected
        ]

    def connected_side_names_except(self, missing_sides):
        missing = set(missing_sides)
        return [
            side
            for side in self.connected_side_names()
            if side not in missing
        ]

    def missing_connected_sides(self, plates):
        return [
            plate.side
            for plate in plates
            if plate.handle is None or plate in self.disconnected
        ]

    def require_connected_plates(self, plates, context):
        missing = [
            plate for plate in plates if plate.handle is None or plate in self.disconnected
        ]
        if missing:
            details = []
            for plate in missing:
                reason = self.disconnect_reasons.get(plate)
                if reason is None:
                    details.append(plate.side)
                else:
                    details.append(f"{plate.side} {format_hci_reason(reason)}")
            raise RuntimeError(
                f"{context}: plateforme(s) déconnectée(s): "
                f"{', '.join(details)}."
            )

    def subscribe_measurement_notifications(self, clients):
        for plate, client in clients.items():
            client.notification_subscribers.setdefault(
                UART_VALUE_HANDLE,
                set(),
            ).add(lambda payload, item=plate: self.handle_payload(item, payload))

    def register_disconnect_logger(self, connection, plate):
        def log_disconnection(reason=None, *args, **kwargs):
            if reason is None and args:
                reason = args[0]
            formatted_reason = format_hci_reason(reason)
            print(
                f"Déconnexion Bumble {plate.side}: {formatted_reason}",
                flush=True,
            )
            plate.handle = None
            self.disconnected.add(plate)
            try:
                self.disconnect_reasons[plate] = int(reason)
            except (TypeError, ValueError):
                self.disconnect_reasons[plate] = reason

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

    async def write_side_sequence(self, clients, side_values, delay):
        plates_by_side = {plate.side: plate for plate in clients}
        for side, value in side_values:
            plate = plates_by_side.get(side)
            if (
                plate is None
                or plate in self.disconnected
                or plate.handle is None
            ):
                continue
            await self.write_plate(clients[plate], plate, value)
        await asyncio.sleep(delay)

    async def write_timed_side_sequence(self, clients, side_values):
        plates_by_side = {plate.side: plate for plate in clients}
        for side, value, delay_after in side_values:
            plate = plates_by_side.get(side)
            if (
                plate is not None
                and plate not in self.disconnected
                and plate.handle is not None
            ):
                await self.write_plate(clients[plate], plate, value)
            await asyncio.sleep(delay_after)

    async def connect_plate(self, device, plate, Address, connect_timeout):
        from bumble.device import ConnectionParametersPreferences
        from bumble.hci import HCI_LE_1M_PHY

        bumble_device = importlib.import_module("bumble.device")

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
            # Paramètres initiaux observés dans la capture officielle Android:
            # HCI_LE_Create_Connection scan=0x0010/0x0010,
            # min=0x0018, max=0x0028, supervision=0x01f4. Bumble exprime les
            # durées en ms et les convertit ensuite en unités HCI. Son API
            # expose l'adresse propre et les paramètres de connexion, mais pas
            # le scan interval/window d'initiation; on aligne donc
            # temporairement ses constantes sur les valeurs officielles, puis
            # on les restaure aussitôt après. Pour l'adresse locale, on utilise
            # la random statique Bumble, car le contrôleur nRF52840 n'a pas
            # d'adresse publique valide.
            connection_preferences = {
                HCI_LE_1M_PHY: ConnectionParametersPreferences(
                    connection_interval_min=OFFICIAL_CONNECT_INTERVAL_MIN_MS,
                    connection_interval_max=OFFICIAL_CONNECT_INTERVAL_MAX_MS,
                    max_latency=0,
                    supervision_timeout=OFFICIAL_CONNECT_SUPERVISION_TIMEOUT_MS,
                    min_ce_length=0,
                    max_ce_length=0,
                )
            }
            original_connect_scan_interval = (
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL
            )
            original_connect_scan_window = (
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_WINDOW
            )
            try:
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_INTERVAL = (
                    OFFICIAL_CONNECT_SCAN_INTERVAL_MS
                )
                bumble_device.DEVICE_DEFAULT_CONNECT_SCAN_WINDOW = (
                    OFFICIAL_CONNECT_SCAN_WINDOW_MS
                )
                connection = await device.connect(
                    remote_address,
                    connection_parameters_preferences=connection_preferences,
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
        self.disconnected.discard(plate)
        self.disconnect_reasons.pop(plate, None)
        print(
            f"Plateforme {plate.side} connectée, handle Bumble "
            f"0x{plate.handle:04x}.",
            flush=True,
        )
        return connection

    async def wait_for_official_advertisement(
        self,
        device,
        plate,
        timeout=8.0,
        forbid_other_plates=False,
    ):
        from bumble.hci import HCI_LE_1M_PHY

        loop = asyncio.get_running_loop()
        found = loop.create_future()
        seen = {}

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

        def on_advertisement(*args):
            address = advertisement_address(args)
            normalized = normalize_address(address)
            text = advertisement_text(args)
            marker = f"{normalized} {text}".upper()
            known_addresses = {
                known_plate.address.upper() for known_plate in self.dual.plates
            }
            if (
                normalized not in seen
                and (
                    normalized in known_addresses
                    or "KFORCE" in marker
                    or "K-FORCE" in marker
                    or "KINV" in marker
                )
            ):
                seen[normalized] = text
                print(
                    "Publicité vue pendant recherche "
                    f"{plate.side}: {normalized} {text}".strip(),
                    flush=True,
                )
            if (
                forbid_other_plates
                and normalized in known_addresses
                and normalized != plate.address.upper()
                and not found.done()
            ):
                found.set_exception(
                    RuntimeError(
                        "Mauvaise plateforme visible pendant la phase "
                        f"{plate.side} seule: {normalized}. "
                        "Éteins complètement l'autre plateforme avant de "
                        "relancer cette phase."
                    )
                )
            if normalized == plate.address.upper() and not found.done():
                found.set_result(address)

        print(
            f"Recherche officielle Bumble {plate.side}: {plate.address}...",
            flush=True,
        )
        handler = device.on("advertisement", on_advertisement)
        try:
            await device.start_scanning(
                legacy=True,
                active=True,
                scan_interval=OFFICIAL_CONNECT_SCAN_INTERVAL_MS,
                scan_window=OFFICIAL_CONNECT_SCAN_WINDOW_MS,
                own_address_type=NRF52840_STABLE_RANDOM_OWN_ADDRESS_TYPE,
                filter_duplicates=False,
                scanning_phys=(HCI_LE_1M_PHY,),
            )
            await asyncio.wait_for(found, timeout=timeout)
            print(
                f"Publicité {plate.side} trouvée avant connexion.",
                flush=True,
            )
        except TimeoutError:
            seen_list = (
                ", ".join(sorted(address for address in seen if address))
                or "aucune publicité Kinvent vue"
            )
            raise TimeoutError(
                f"Publicité {plate.side} introuvable après {timeout:.1f} s "
                f"(attendue {plate.address}). Publicités utiles vues: {seen_list}."
            )
        finally:
            if handler is not None:
                device.remove_listener("advertisement", handler)
            await device.stop_scanning()

    async def complete_initial_official_discovery(
        self,
        device,
        plate,
        Address,
        connection,
        all_clients,
        started_discoveries,
        connect_timeout,
    ):
        # Dans la capture officielle des deux plateformes, seule la première
        # connexion de la plateforme droite tombe avec la raison HCI 0x3e
        # avant que l'application continue. Kinvent reconnecte alors cette
        # même plateforme, puis reprend le pré-vol GATT officiel. On limite
        # donc cette reprise à ce cas initial précis, sans mécanisme de retry
        # général ni extension à la plateforme gauche.
        #
        # La même capture montre aussi que la plateforme initie ses premiers
        # échanges ATT environ 0,11 à 0,13 s après la connexion, avant que
        # l'application poursuive la découverte GATT. On respecte cette amorce
        # pour éviter d'envoyer la découverte immédiatement après l'évènement
        # de connexion.
        await self.wait_official_gatt_settle(plate)
        discovery = asyncio.create_task(
            self.discover_official_services(
                plate,
                connection.gatt_client,
            )
        )
        try:
            await discovery
        except BaseException as exc:
            if self.disconnect_reasons.get(plate) != OFFICIAL_INITIAL_DISCONNECT_REASON:
                raise
            if plate.side != "droite":
                raise RuntimeError(
                    "Déconnexion initiale HCI 0x3e sur la plateforme "
                    f"{plate.side}: ce cas n'apparaît pas dans la capture "
                    "officielle Kinvent, donc il n'est pas rejoué."
                ) from exc
            print(
                f"Reconnexion officielle initiale {plate.side} "
                "après déconnexion HCI 0x3e.",
                flush=True,
            )
            all_clients.pop(plate, None)
            self.connections.pop(plate, None)
            connection = await self.connect_plate(
                device,
                plate,
                Address,
                connect_timeout,
            )
            self.connections[plate] = connection
            self.register_disconnect_logger(connection, plate)
            all_clients[plate] = connection.gatt_client
            await self.wait_official_gatt_settle(plate)
            discovery = asyncio.create_task(
                self.discover_official_services(
                    plate,
                    connection.gatt_client,
                )
            )
            try:
                await discovery
            except BaseException as retry_exc:
                if self.disconnect_reasons.get(plate) == OFFICIAL_INITIAL_DISCONNECT_REASON:
                    raise RuntimeError(
                        "La reconnexion officielle initiale de la plateforme "
                        f"{plate.side} s'est encore interrompue en HCI 0x3e. "
                        "La capture officielle Kinvent ne montre qu'une seule "
                        "coupure initiale sur la droite, puis une découverte "
                        "GATT réussie; ce comportement Bumble/nRF n'est donc "
                        "pas conforme à la capture."
                    ) from retry_exc
                raise
        started_discoveries[plate] = discovery

    async def wait_official_gatt_settle(self, plate):
        await asyncio.sleep(OFFICIAL_GATT_SETTLE_AFTER_CONNECT_S)

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

        # Comme dans le pilote HCI direct validé par les captures Kinvent:
        # après les réponses CCCD, l'application réarme d'abord le protocole
        # avec 0x10 sur chaque plateforme, laisse respirer la liaison, puis
        # applique le réglage radio final. Le faire avant ce second 0x10 peut
        # provoquer un COMMAND_DISALLOWED côté contrôleur Bumble/nRF52840.
        await self.write_all(clients, b"\x10", 0.25)

        # Réglage radio final observé officiellement:
        # intervalle 0x0009-0x0018, latence 0, supervision 0x0200.
        for plate in connected:
            connection = self.connections[plate]
            if plate in self.disconnected:
                continue
            print(f"Réglage radio {plate.side}...", flush=True)
            try:
                await connection.update_parameters(0x0009, 0x0018, 0, 0x0200)
            except Exception as exc:
                if is_hci_command_disallowed(exc):
                    print(
                        "Réglage radio "
                        f"{plate.side} refusé par le contrôleur "
                        "(COMMAND_DISALLOWED); séquence officielle conservée.",
                        flush=True,
                    )
                    continue
                print(
                    "Échec réglage radio "
                    f"{plate.side}: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                raise

        # Séquence double-plateforme observée dans la capture officielle
        # Android. Les commandes sont les mêmes que KPLATE_INIT_STEPS, mais
        # leur ordre par côté n'est pas un simple "droite puis gauche" pour
        # toute la suite: après 0x76, l'application termine d'abord certaines
        # étapes côté gauche avant de faire la droite.
        await self.write_all(clients, b"\x09", 0.05)
        await self.write_all(clients, b"\x76", 0.30)
        await self.write_side_sequence(
            clients,
            [("gauche", b"\x11"), ("droite", b"\x11")],
            0.16,
        )
        await self.write_timed_side_sequence(
            clients,
            [
                ("gauche", b"\x10", 0.008),
                ("gauche", b"\x10", 0.046),
                ("droite", b"\x10", 0.006),
                ("droite", b"\x10", 0.354),
                ("gauche", bytes.fromhex("60 00 19 00 4b 0d 0a"), 0.012),
                ("gauche", b"\x66", 0.020),
                ("droite", bytes.fromhex("60 00 19 00 4b 0d 0a"), 0.009),
                ("droite", b"\x66", 1.646),
                ("gauche", b"\x56", 0.023),
                ("droite", b"\x56", 0.033),
                ("gauche", bytes.fromhex("ac 00 54 f8"), 0.025),
                ("droite", bytes.fromhex("ac 00 54 f8"), 0.035),
                ("gauche", bytes.fromhex("ac 01 04 a9"), 0.027),
                ("droite", bytes.fromhex("ac 01 04 a9"), 0.034),
                ("gauche", b"\x11", 0.027),
                ("droite", b"\x11", 0.20),
            ],
        )

        for plate in connected:
            print(f"Flux {plate.side} démarré.", flush=True)

    async def wait_for_initial_tare(self, clients, timeout=8.0):
        connected = list(clients.keys())
        if all(plate.offsets is not None for plate in connected):
            return
        print("Tare initiale des plateformes: laissez-les vides.", flush=True)
        deadline = time.monotonic() + timeout
        while (
            time.monotonic() < deadline
            and any(plate.offsets is None for plate in connected)
        ):
            self.require_connected_plates(
                connected,
                "Tare initiale interrompue",
            )
            await asyncio.sleep(0.05)
        missing = [
            plate.side
            for plate in connected
            if plate.offsets is None
        ]
        if missing:
            raise RuntimeError(
                "Tare initiale incomplète pour : " + ", ".join(missing)
            )
        self.dual.save_calibration()

    async def settle_initial_streams(self, clients, duration=2.0):
        """Vérifie que les flux Bumble produisent vraiment des mesures."""
        await self.wait_for_initial_tare(clients)
        print(
            "Stabilisation initiale des flux pendant "
            f"{duration:.0f} secondes...",
            flush=True,
        )
        connected = list(clients.keys())
        before = {
            plate.side: (
                plate.notifications,
                plate.measurements,
                plate.rejected_frames,
            )
            for plate in connected
        }
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self.require_connected_plates(
                connected,
                "Stabilisation initiale interrompue",
            )
            await asyncio.sleep(0.05)
        missing = []
        for plate in connected:
            (
                previous_notifications,
                previous_measurements,
                previous_rejected,
            ) = before[plate.side]
            notification_delta = plate.notifications - previous_notifications
            measurement_delta = plate.measurements - previous_measurements
            rejected_delta = plate.rejected_frames - previous_rejected
            print(
                f"Flux initial {plate.side}: "
                f"{notification_delta} notification(s), "
                f"{measurement_delta} mesure(s) valide(s), "
                f"{rejected_delta} trame(s) rejetée(s).",
                flush=True,
            )
            if measurement_delta == 0:
                missing.append(plate.side)
        if not missing:
            self.dual.validate_loaded_calibration_at_rest()
        return missing

    async def validate_live_streams(self, clients, timeout=3.0):
        """Exige une nouvelle mesure valide avant de lancer une acquisition."""
        connected = list(clients.keys())
        self.require_connected_plates(
            connected,
            "Validation du flux impossible",
        )
        before_measurements = {
            plate.side: plate.measurements for plate in connected
        }
        await self.wake_measurement_streams(clients)
        deadline = time.monotonic() + timeout
        if all(
            plate.measurements > before_measurements[plate.side]
            for plate in connected
        ):
            return
        while time.monotonic() < deadline:
            self.require_connected_plates(
                connected,
                "Validation du flux interrompue",
            )
            if all(
                plate.measurements > before_measurements[plate.side]
                for plate in connected
            ):
                return
            await asyncio.sleep(0.05)
        silent = [
            plate.side
            for plate in connected
            if plate.measurements <= before_measurements[plate.side]
        ]
        if not silent:
            return
        raise RuntimeError(
            "Flux de mesure absent: " + ", ".join(silent) + "."
        )

    async def park_measurement_streams(self, clients, commands=3):
        connected = [
            plate
            for plate in clients
            if plate not in self.disconnected and plate.handle is not None
        ]
        if not connected:
            return

        print(
            "Mise au repos officielle du flux Bumble "
            f"pour {connected_sides(connected)}...",
            flush=True,
        )
        for _ in range(commands):
            await self.write_all(clients, b"\x10", KPLATE_PARK_DELAY)
        print("Flux de mesure au repos; connexions Bluetooth conservées.")

    async def wake_measurement_streams(self, clients):
        connected = [
            plate
            for plate in clients
            if plate not in self.disconnected and plate.handle is not None
        ]
        if not connected:
            raise RuntimeError("Aucune plateforme connectée à réveiller.")

        print(
            "Relance officielle du flux Bumble "
            f"pour {connected_sides(connected)}...",
            flush=True,
        )
        # Au démarrage d'un nouveau test, la capture officielle des plateformes
        # montre 0x90 sur chaque plateforme, environ 700 ms d'attente, puis
        # 0x11 sur chaque plateforme.
        await self.write_all(clients, b"\x90", 0.70)
        await self.write_all(clients, b"\x11", 0.25)

    async def disconnect_connected_plates(self):
        for plate, connection in reversed(list(self.connections.items())):
            if plate in self.disconnected or plate.handle is None:
                continue
            try:
                await connection.disconnect()
                await asyncio.sleep(0.10)
            except Exception as exc:
                print(
                    f"Déconnexion finale ignorée {plate.side}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

    async def hold_links(self, clients, duration, label):
        if duration <= 0:
            return

        print(f"{label} pendant {duration:.1f} s...", flush=True)
        start = time.monotonic()
        deadline = start + duration
        next_keepalive = start + self.dual.keepalive_interval
        next_progress = start

        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            self.require_connected_plates(
                list(clients.keys()),
                f"Lien Bluetooth interrompu pendant {label}",
            )
            now = time.monotonic()
            if self.dual.keepalive_interval > 0 and now >= next_keepalive:
                await self.write_all(clients, b"\xff", 0.0)
                next_keepalive = now + self.dual.keepalive_interval
            if now >= next_progress:
                remaining = max(0.0, deadline - now)
                print(f"{label} restant: {remaining:4.1f} s", flush=True)
                next_progress = now + 5.0

    async def acquire_once(self, clients, duration, cycle_number=1, cycle_count=1):
        if cycle_count > 1:
            print(
                f"Cycle Bumble {cycle_number}/{cycle_count}.",
                flush=True,
            )
        print(f"Acquisition double Bumble pendant {duration:.1f} s...", flush=True)
        start = time.monotonic()
        deadline = start + duration
        next_keepalive = start + self.dual.keepalive_interval
        next_progress = start

        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            self.require_connected_plates(
                list(clients.keys()),
                "Flux de plateforme interrompu pendant l'acquisition",
            )
            now = time.monotonic()
            if self.dual.keepalive_interval > 0 and now >= next_keepalive:
                await self.write_all(clients, b"\xff", 0.0)
                next_keepalive = now + self.dual.keepalive_interval
            if now >= next_progress:
                remaining = max(0.0, deadline - now)
                print(f"Temps restant: {remaining:4.1f} s", flush=True)
                next_progress = now + 5.0

    async def acquire_once_managed(
        self,
        clients,
        duration,
        stop_requested,
        stream_silence_timeout=3.0,
    ):
        print(f"Acquisition double Bumble pendant {duration:.1f} s...", flush=True)
        start = time.monotonic()
        deadline = start + duration
        next_keepalive = start + self.dual.keepalive_interval
        next_progress = start
        if self.dual.acquisition_mode == "cmj":
            last_measure_count = self.dual.cmj_samples
            silence_label = "aucun événement CMJ reçu"
        else:
            last_measure_count = self.dual.paired_samples
            silence_label = "aucune paire synchronisée reçue"
        last_measure_at = start
        completed = True

        while time.monotonic() < deadline:
            if stop_requested():
                completed = False
                break
            if len(self.connected_side_names()) != 2:
                completed = False
                print(
                    "Flux de plateforme interrompu pendant l'acquisition.",
                    flush=True,
                )
                break
            await asyncio.sleep(0.05)
            now = time.monotonic()
            current_measure_count = (
                self.dual.cmj_samples
                if self.dual.acquisition_mode == "cmj"
                else self.dual.paired_samples
            )
            if current_measure_count > last_measure_count:
                last_measure_count = current_measure_count
                last_measure_at = now
            if now - last_measure_at > stream_silence_timeout:
                raise RuntimeError(
                    f"Flux de mesure absent: {silence_label}."
                )
            if self.dual.keepalive_interval > 0 and now >= next_keepalive:
                await self.write_all(clients, b"\xff", 0.0)
                next_keepalive = now + self.dual.keepalive_interval
            if now >= next_progress:
                remaining = max(0.0, deadline - now)
                print(f"Temps restant: {remaining:4.1f} s", flush=True)
                next_progress = now + 5.0
        return completed

    async def wait_for_cmj_preparation(
        self,
        clients,
        csv_path,
        stop_requested,
        preparation_timeout=60.0,
        stream_silence_timeout=3.0,
    ):
        print(
            "Préparation CMJ Bumble: montez sur les plateformes et "
            "restez immobile.",
            flush=True,
        )
        start = time.monotonic()
        deadline = start + preparation_timeout
        next_keepalive = start + self.dual.keepalive_interval
        next_progress = start
        last_sample_count = self.dual.cmj_samples
        last_sample_at = start

        while time.monotonic() < deadline:
            if stop_requested():
                return False
            self.require_connected_plates(
                list(clients.keys()),
                "Préparation CMJ interrompue",
            )
            await asyncio.sleep(0.05)
            now = time.monotonic()
            if self.dual.cmj_samples > last_sample_count:
                last_sample_count = self.dual.cmj_samples
                last_sample_at = now
            if now - last_sample_at > stream_silence_timeout:
                raise RuntimeError(
                    "Flux de mesure absent: aucun événement CMJ reçu."
                )
            preparation = detect_stable_body_mass(csv_path)
            if preparation.get("ready"):
                print(
                    "Préparation CMJ validée: "
                    f"{preparation['body_mass_kg']:.1f} kg stable.",
                    flush=True,
                )
                return True
            if self.dual.keepalive_interval > 0 and now >= next_keepalive:
                await self.write_all(clients, b"\xff", 0.0)
                next_keepalive = now + self.dual.keepalive_interval
            if now >= next_progress:
                print(
                    "Préparation CMJ en attente: "
                    f"{preparation.get('status', 'waiting_presence')}.",
                    flush=True,
                )
                next_progress = now + 5.0

        raise RuntimeError(
            "Préparation CMJ expirée: aucun poids stable n'a été détecté."
        )

    async def discover_official_services(self, plate, client):
        print(f"Découverte GATT {plate.side}...", flush=True)
        await client.discover_services()

    async def read_official_model(self, plate, client):
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

    async def run_official_gatt_preflight(
        self,
        clients,
        started_discoveries=None,
        announce=True,
        device=None,
        Address=None,
        connect_timeout=15.0,
    ):
        # Dans la capture Android Kinvent des deux plateformes, l'application
        # effectue une découverte GATT complète puis lit le modèle au handle
        # 0x0016 avant d'écrire 0x10 et d'activer le CCCD UART.
        # Ce pré-vol reste du GATT standard; il ne remplace ni n'ajoute de
        # commande Kinvent propriétaire.
        #
        # Dans la capture double-plateforme, Android connecte d'abord la
        # droite puis la gauche. La découverte GATT de la droite démarre avant
        # celle de la gauche; les deux lectures 0x0016 sont ensuite quasi
        # simultanées. La seule reprise acceptée ici est la reconnexion
        # initiale HCI 0x3e observée officiellement.
        started_discoveries = started_discoveries or {}
        connected = list(clients.keys())
        if announce:
            print(
                "Pré-vol GATT officiel pour "
                f"{connected_sides(connected)}...",
                flush=True,
            )

        for plate in list(clients.keys()):
            if plate in started_discoveries:
                await started_discoveries[plate]
            else:
                connection = self.connections.get(plate)
                if device is None or Address is None or connection is None:
                    await self.discover_official_services(plate, clients[plate])
                    continue
                await self.complete_initial_official_discovery(
                    device,
                    plate,
                    Address,
                    connection,
                    clients,
                    started_discoveries,
                    connect_timeout,
                )

        await asyncio.gather(
            *(self.read_official_model(plate, client) for plate, client in clients.items())
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
        gatt_preflight="official-discovery",
        hold_after=0.0,
        cycles=1,
        rest_between_cycles=0.0,
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
            all_clients = {}
            started_discoveries = {}
            preflight_announced = False
            for plate in selected:
                if len(selected) == 1:
                    await self.wait_for_official_advertisement(
                        device,
                        plate,
                        min(8.0, connect_timeout),
                        forbid_other_plates=True,
                    )
                connection = await self.connect_plate(
                    device,
                    plate,
                    Address,
                    connect_timeout,
                )
                self.connections[plate] = connection
                self.register_disconnect_logger(connection, plate)
                all_clients[plate] = connection.gatt_client

            if diagnostic == "connect-only":
                self.require_connected_plates(
                    selected,
                    "Diagnostic connexion seule incomplet",
                )
                print(
                    f"Diagnostic connexion seule pendant {duration:.1f} s...",
                    flush=True,
                )
                start = time.monotonic()
                while time.monotonic() - start < duration:
                    await asyncio.sleep(0.5)
                    self.require_connected_plates(
                        selected,
                        "Diagnostic connexion seule interrompu",
                    )
                await self.disconnect_connected_plates()
                return

            if gatt_preflight == "official-discovery":
                await self.run_official_gatt_preflight(
                    all_clients,
                    started_discoveries=started_discoveries,
                    announce=not preflight_announced,
                    device=device,
                    Address=Address,
                    connect_timeout=connect_timeout,
                )

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
                clients[plate] = client

            self.subscribe_measurement_notifications(clients)
            await self.configure_streams(clients)

            for cycle_index in range(1, cycles + 1):
                if cycle_index > 1:
                    await self.wake_measurement_streams(clients)
                await self.acquire_once(
                    clients,
                    duration,
                    cycle_number=cycle_index,
                    cycle_count=cycles,
                )
                await self.park_measurement_streams(clients, commands=3)
                if cycle_index < cycles:
                    await self.hold_links(
                        clients,
                        rest_between_cycles,
                        "Repos Bluetooth entre cycles",
                    )

            await self.hold_links(
                clients,
                hold_after,
                "Maintien Bluetooth Bumble après acquisition",
            )

            await self.disconnect_connected_plates()

    async def run_persistent(self, control_file, state_file, connect_timeout=15.0):
        require_bumble()
        from bumble.device import Device
        from bumble.hci import Address
        from bumble.transport import open_transport

        control_path = Path(control_file)
        state_path = Path(state_file)
        generation = None
        clients = {}
        connected = False
        streams_active = False
        next_idle_keepalive = time.monotonic() + self.dual.keepalive_interval
        next_idle_state_refresh = time.monotonic()

        def write_state(phase, **state):
            self.dual.write_worker_state(state_path, phase=phase, **state)

        write_state("starting")
        async with await open_transport(self.transport) as hci_transport:
            device = Device.with_hci(
                "Kine Capteurs Bumble",
                Address("F0:F1:F2:F3:F4:F5"),
                hci_transport.source,
                hci_transport.sink,
            )
            await device.power_on()
            write_state("disconnected")

            while True:
                command = read_json(control_path)
                action = command.get("action")
                requested = command.get("generation")

                if action == "disconnect":
                    await self.park_measurement_streams(clients, commands=3)
                    for plate, connection in list(self.connections.items()):
                        if plate in self.disconnected or plate.handle is None:
                            continue
                        try:
                            await connection.disconnect()
                        except Exception as exc:
                            print(
                                f"Déconnexion Bumble ignorée {plate.side}: "
                                f"{type(exc).__name__}: {exc}",
                                flush=True,
                            )
                    self.connections.clear()
                    clients.clear()
                    self.disconnected.clear()
                    connected = False
                    streams_active = False
                    generation = requested or generation
                    write_state("disconnected", generation=generation)
                    return

                if action == "connect" and requested and requested != generation:
                    generation = requested
                    write_state("connecting", generation=generation)
                    try:
                        selected = self.selected_plates("both", "right-first")
                        print(
                            "Ordre de connexion Bumble: "
                            f"{connected_sides(selected)}",
                            flush=True,
                        )
                        all_clients = {}
                        started_discoveries = {}
                        for plate in selected:
                            connection = await self.connect_plate(
                                device,
                                plate,
                                Address,
                                connect_timeout,
                            )
                            self.connections[plate] = connection
                            self.register_disconnect_logger(connection, plate)
                            all_clients[plate] = connection.gatt_client
                        await self.run_official_gatt_preflight(
                            all_clients,
                            started_discoveries=started_discoveries,
                            announce=not bool(started_discoveries),
                            device=device,
                            Address=Address,
                            connect_timeout=connect_timeout,
                        )
                        clients = all_clients
                        self.subscribe_measurement_notifications(clients)
                        await self.configure_streams(clients)
                        missing_streams = await self.settle_initial_streams(
                            clients,
                        )
                        sides = self.connected_side_names()
                        if missing_streams:
                            connected = bool(sides)
                            streams_active = False
                            write_state(
                                "degraded" if sides else "disconnected",
                                generation=generation,
                                connected_sides=(
                                    self.connected_side_names_except(
                                        missing_streams
                                    )
                                ),
                                mode="balance",
                                error=(
                                    "Connexion Bluetooth établie, mais "
                                    "aucune mesure initiale reçue pour : "
                                    + ", ".join(missing_streams)
                                    + "."
                                ),
                            )
                            continue
                        if len(sides) != 2:
                            connected = bool(sides)
                            streams_active = False
                            write_state(
                                "degraded" if sides else "disconnected",
                                generation=generation,
                                connected_sides=sides,
                                mode="balance",
                                error=(
                                    "Une plateforme s'est déconnectée pendant "
                                    "l'initialisation. Reconnectez les "
                                    "capteurs."
                                ),
                            )
                            continue
                    except BaseException as exc:
                        self.dual.close_csv()
                        write_state(
                            "disconnected",
                            generation=generation,
                            error=(
                                "Connexion Bumble impossible. "
                                f"Reconnectez les plateformes : {exc}"
                            ),
                        )
                        raise
                    connected = True
                    streams_active = True
                    next_idle_keepalive = time.monotonic() + self.dual.keepalive_interval
                    next_idle_state_refresh = time.monotonic()
                    write_state(
                        "idle",
                        generation=generation,
                        connected_sides=self.connected_side_names(),
                        mode="balance",
                    )
                    continue

                if action == "start" and requested and requested != generation:
                    sides = self.connected_side_names()
                    if not connected or len(sides) != 2:
                        generation = requested
                        write_state(
                            "degraded" if sides else "disconnected",
                            generation=requested,
                            connected_sides=sides,
                            mode="balance",
                            error=(
                                "Les deux plateformes doivent être "
                                "connectées avant de démarrer le jeu."
                            ),
                        )
                        self.dual.consume_control_command(control_path, generation)
                        continue
                    duration = float(command["duration"])
                    mode = command.get("mode", "balance")
                    try:
                        await self.validate_live_streams(clients)
                        self.dual.paired_samples = 0
                        self.dual.cmj_samples = 0
                        self.dual.dropped_samples = {"gauche": 0, "droite": 0}
                        for plate in self.dual.plates:
                            plate.samples.clear()
                        self.dual.open_csv(command["csv_path"], mode)
                        write_state(
                            "active",
                            generation=generation,
                            csv_path=command["csv_path"],
                            started_at=now_iso(),
                            mode=mode,
                            connected_sides=self.connected_side_names(),
                        )
                        streams_active = True
                        def stop_requested():
                            return (
                                read_json(control_path).get("action") == "stop"
                                and read_json(control_path).get("generation")
                                == generation
                            )

                        if mode == "cmj":
                            completed = await self.wait_for_cmj_preparation(
                                clients,
                                command["csv_path"],
                                stop_requested=stop_requested,
                            )
                            if completed:
                                completed = await self.acquire_once_managed(
                                    clients,
                                    duration,
                                    stop_requested=stop_requested,
                                )
                        else:
                            completed = await self.acquire_once_managed(
                                clients,
                                duration,
                                stop_requested=stop_requested,
                            )
                    except RuntimeError as exc:
                        self.dual.close_csv()
                        streams_active = False
                        write_state(
                            "degraded",
                            generation=generation,
                            csv_path=command["csv_path"],
                            mode=mode,
                            connected_sides=self.connected_side_names(),
                            interrupted=True,
                            error=(
                                str(exc)
                                + " Reconnectez les plateformes."
                            ),
                        )
                        self.dual.consume_control_command(control_path, generation)
                        continue
                    self.dual.close_csv()
                    await self.park_measurement_streams(clients, commands=3)
                    streams_active = False
                    sides = self.connected_side_names()
                    if len(sides) == 2:
                        write_state(
                            "idle",
                            generation=generation,
                            csv_path=command["csv_path"],
                            connected_sides=sides,
                            paired_samples=self.dual.paired_samples,
                            cmj_samples=self.dual.cmj_samples,
                            mode=mode,
                            stopped=not completed,
                            result_available=(mode == "cmj"),
                        )
                    else:
                        write_state(
                            "degraded" if sides else "disconnected",
                            generation=generation,
                            csv_path=command["csv_path"],
                            connected_sides=sides,
                            paired_samples=self.dual.paired_samples,
                            cmj_samples=self.dual.cmj_samples,
                            mode=mode,
                            stopped=True,
                            interrupted=True,
                            result_available=False,
                            error=(
                                "Un flux de plateforme ne répond plus. "
                                "Reconnectez les capteurs."
                            ),
                        )
                    self.dual.consume_control_command(control_path, generation)
                    next_idle_keepalive = time.monotonic() + self.dual.keepalive_interval
                    next_idle_state_refresh = time.monotonic()
                    continue

                if connected and time.monotonic() >= next_idle_keepalive:
                    await self.write_all(clients, b"\xff", 0.0)
                    next_idle_keepalive = (
                        time.monotonic() + self.dual.keepalive_interval
                    )

                if connected and time.monotonic() >= next_idle_state_refresh:
                    sides = self.connected_side_names()
                    if len(sides) == 2:
                        write_state(
                            "idle",
                            generation=generation,
                            connected_sides=sides,
                            mode="balance",
                        )
                    elif sides:
                        write_state(
                            "degraded",
                            generation=generation,
                            connected_sides=sides,
                            mode="balance",
                            error=(
                                "Connexion partielle. Reconnectez les "
                                "plateformes."
                            ),
                        )
                    else:
                        connected = False
                        write_state(
                            "disconnected",
                            generation=generation,
                            connected_sides=[],
                            mode="balance",
                            error=(
                                "Plateformes déconnectées. Reconnectez les "
                                "plateformes."
                            ),
                        )
                    next_idle_state_refresh = time.monotonic() + 2.0

                await asyncio.sleep(0.2)


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
        default="official-discovery",
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
    parser.add_argument(
        "--hold-after",
        type=float,
        default=0.0,
        help=(
            "Diagnostic: maintenir les liens Bluetooth après l'acquisition "
            "avec le keepalive officiel 0xff avant la fermeture finale."
        ),
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=1,
        help=(
            "Diagnostic: enchaîner plusieurs acquisitions dans une seule "
            "connexion Bluetooth."
        ),
    )
    parser.add_argument(
        "--rest-between-cycles",
        type=float,
        default=0.0,
        help=(
            "Diagnostic: temps de repos entre cycles avec flux au repos et "
            "keepalive officiel 0xff."
        ),
    )
    parser.add_argument("--sync-tolerance-ms", type=float, default=20.0)
    parser.add_argument("--calibration-file")
    parser.add_argument("--recalibrate", action="store_true")
    parser.add_argument("--csv", default="storage/raw_data/kplates_bumble.csv")
    parser.add_argument("--control-file")
    parser.add_argument("--state-file")
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
        if args.control_file and args.state_file:
            asyncio.run(
                client.run_persistent(
                    args.control_file,
                    args.state_file,
                    connect_timeout=args.connect_timeout,
                )
            )
        else:
            asyncio.run(
                client.run(
                    args.duration,
                    args.connect_timeout,
                    sides=args.sides,
                    connection_order=args.connection_order,
                    diagnostic=args.diagnostic,
                    stream_side=args.stream_side,
                    gatt_preflight=args.gatt_preflight,
                    hold_after=args.hold_after,
                    cycles=args.cycles,
                    rest_between_cycles=args.rest_between_cycles,
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
