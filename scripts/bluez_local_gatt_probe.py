import argparse
import asyncio

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method


BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
GATT_MANAGER = "org.bluez.GattManager1"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHARACTERISTIC = "org.bluez.GattCharacteristic1"

APP_PATH = "/com/kinecapteurs/kinvent"
SERVICE_PATH = APP_PATH + "/service0"

KINVENT_SERVICE_UUID = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
KINVENT_NOTIFY_CHAR = "49535343-1e4d-4bd9-ba61-23c647249616"
KINVENT_WRITE_CHAR = "49535343-8841-43f4-a8d4-ecbe34729bb3"
KINVENT_ALT_NOTIFY_CHAR = "49535343-4c8a-39b3-2f49-511cff073b7e"


def unwrap(value):
    if isinstance(value, Variant):
        return unwrap(value.value)
    if isinstance(value, list):
        return [unwrap(item) for item in value]
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    return value


class Application(ServiceInterface):
    def __init__(self, objects):
        super().__init__(OBJECT_MANAGER)
        self.objects = objects

    @method()
    def GetManagedObjects(self) -> "a{oa{sa{sv}}}":
        return {
            path: {
                interface: {
                    key: Variant(signature, value)
                    for key, signature, value in props
                }
                for interface, props in interfaces.items()
            }
            for path, interfaces in self.objects.items()
        }


class LocalService(ServiceInterface):
    def __init__(self, index, uuid, primary=True):
        super().__init__(GATT_SERVICE)
        self.path = f"{SERVICE_PATH}"
        self.index = index
        self.uuid = uuid
        self.primary = primary

    @dbus_property()
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property()
    def Primary(self) -> "b":
        return self.primary

    @dbus_property()
    def Characteristics(self) -> "ao":
        return [
            f"{SERVICE_PATH}/char0",
            f"{SERVICE_PATH}/char1",
            f"{SERVICE_PATH}/char2",
        ]


class LocalCharacteristic(ServiceInterface):
    def __init__(self, service_path, index, uuid, flags):
        super().__init__(GATT_CHARACTERISTIC)
        self.path = f"{service_path}/char{index}"
        self.uuid = uuid
        self.flags = flags
        self.service_path = service_path
        self.value = bytearray()
        self.notifying = False

    @dbus_property()
    def UUID(self) -> "s":
        return self.uuid

    @dbus_property()
    def Service(self) -> "o":
        return self.service_path

    @dbus_property()
    def Flags(self) -> "as":
        return self.flags

    @dbus_property()
    def Notifying(self) -> "b":
        return self.notifying

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        print(f"ReadValue {self.uuid} options={unwrap(options)}")
        return self.value

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}") -> "":
        self.value = bytearray(value)
        print(f"WriteValue {self.uuid}: {bytes(value).hex(' ')} options={unwrap(options)}")

    @method()
    def StartNotify(self) -> "":
        self.notifying = True
        print(f"StartNotify {self.uuid}")

    @method()
    def StopNotify(self) -> "":
        self.notifying = False
        print(f"StopNotify {self.uuid}")


async def get_interface(bus, path, interface):
    introspection = await bus.introspect(BLUEZ, path)
    proxy = bus.get_proxy_object(BLUEZ, path, introspection)
    return proxy.get_interface(interface)


async def get_managed_objects(bus):
    manager = await get_interface(bus, "/", OBJECT_MANAGER)
    return unwrap(await manager.call_get_managed_objects())


def find_adapter(objects):
    for path, interfaces in objects.items():
        if ADAPTER in interfaces and GATT_MANAGER in interfaces:
            return path
    for path, interfaces in objects.items():
        if ADAPTER in interfaces:
            raise SystemExit(
                f"Adaptateur trouve ({path}), mais GattManager1 est absent. "
                "BlueZ doit peut-etre etre lance avec les fonctions experimentales."
            )
    raise SystemExit("Aucun adaptateur Bluetooth trouve.")


def find_device(objects, address):
    target = address.lower()
    for path, interfaces in objects.items():
        props = interfaces.get(DEVICE)
        if props and str(props.get("Address", "")).lower() == target:
            return path
    return None


async def wait_for_device(bus, adapter, address, timeout):
    print(f"Scan BlueZ pendant {timeout:.1f} s pour {address}...")
    try:
        await adapter.call_start_discovery()
    except Exception as exc:
        print(f"StartDiscovery: {exc}")

    deadline = asyncio.get_running_loop().time() + timeout
    device_path = None
    while asyncio.get_running_loop().time() < deadline:
        objects = await get_managed_objects(bus)
        device_path = find_device(objects, address)
        if device_path:
            print(f"Appareil trouve: {device_path}")
            break
        await asyncio.sleep(0.3)

    try:
        await adapter.call_stop_discovery()
    except Exception as exc:
        print(f"StopDiscovery: {exc}")

    return device_path


async def register_local_gatt(bus, adapter_path):
    service = LocalService(0, KINVENT_SERVICE_UUID)
    chars = [
        LocalCharacteristic(
            SERVICE_PATH,
            0,
            KINVENT_NOTIFY_CHAR,
            ["read", "write", "write-without-response", "notify"],
        ),
        LocalCharacteristic(
            SERVICE_PATH,
            1,
            KINVENT_WRITE_CHAR,
            ["read", "write", "write-without-response"],
        ),
        LocalCharacteristic(
            SERVICE_PATH,
            2,
            KINVENT_ALT_NOTIFY_CHAR,
            ["read", "write", "write-without-response", "notify"],
        ),
    ]

    objects = {
        SERVICE_PATH: {
            GATT_SERVICE: [
                ("UUID", "s", service.uuid),
                ("Primary", "b", service.primary),
                ("Characteristics", "ao", [char.path for char in chars]),
            ],
        }
    }
    for char in chars:
        objects[char.path] = {
            GATT_CHARACTERISTIC: [
                ("UUID", "s", char.uuid),
                ("Service", "o", char.service_path),
                ("Flags", "as", char.flags),
                ("Notifying", "b", char.notifying),
            ],
        }

    app = Application(objects)
    bus.export(APP_PATH, app)
    bus.export(SERVICE_PATH, service)
    for char in chars:
        bus.export(char.path, char)

    gatt_manager = await get_interface(bus, adapter_path, GATT_MANAGER)
    print(f"Enregistrement service GATT local {KINVENT_SERVICE_UUID}...")
    await gatt_manager.call_register_application(APP_PATH, {})
    print("Service GATT local enregistre.")


async def probe(args):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    objects = await get_managed_objects(bus)
    adapter_path = find_adapter(objects)
    print(f"Adaptateur: {adapter_path}")

    adapter = await get_interface(bus, adapter_path, ADAPTER)
    adapter_props = await get_interface(bus, adapter_path, PROPERTIES)
    await adapter_props.call_set(ADAPTER, "Powered", Variant("b", True))

    await register_local_gatt(bus, adapter_path)

    objects = await get_managed_objects(bus)
    device_path = find_device(objects, args.address)
    if args.remove and device_path:
        print(f"Suppression cache appareil: {device_path}")
        await adapter.call_remove_device(device_path)
        device_path = None

    if not device_path:
        device_path = await wait_for_device(bus, adapter, args.address, args.scan_timeout)
    if not device_path:
        raise SystemExit(f"Appareil introuvable: {args.address}")

    device = await get_interface(bus, device_path, DEVICE)
    device_props = await get_interface(bus, device_path, PROPERTIES)

    def on_properties_changed(interface_name, changed, invalidated):
        changed = unwrap(changed)
        if interface_name != DEVICE:
            return
        interesting = {
            key: changed[key]
            for key in ("Connected", "ServicesResolved", "RSSI", "Name")
            if key in changed
        }
        if interesting:
            print(f"Device change: {interesting}")

    device_props.on_properties_changed(on_properties_changed)

    print(f"Connexion a {args.address}...")
    try:
        await device.call_connect()
    except Exception as exc:
        print(f"Connect: {exc}")

    print(f"Observation pendant {args.duration:.1f} s...")
    await asyncio.sleep(args.duration)

    try:
        await device.call_disconnect()
    except Exception:
        pass


def build_parser():
    parser = argparse.ArgumentParser(
        description="Enregistre un service GATT local Kinvent puis connecte le capteur.",
    )
    parser.add_argument("--address", required=True, help="Adresse BLE/MAC.")
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--remove", action="store_true")
    return parser


async def main():
    args = build_parser().parse_args()
    await probe(args)


if __name__ == "__main__":
    asyncio.run(main())
