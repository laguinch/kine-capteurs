import argparse
import asyncio

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus


BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHARACTERISTIC = "org.bluez.GattCharacteristic1"
GATT_DESCRIPTOR = "org.bluez.GattDescriptor1"


def unwrap(value):
    if isinstance(value, Variant):
        return unwrap(value.value)
    if isinstance(value, list):
        return [unwrap(item) for item in value]
    if isinstance(value, dict):
        return {key: unwrap(item) for key, item in value.items()}
    return value


async def get_interface(bus, path, interface):
    introspection = await bus.introspect(BLUEZ, path)
    proxy = bus.get_proxy_object(BLUEZ, path, introspection)
    return proxy.get_interface(interface)


async def get_managed_objects(bus):
    manager = await get_interface(bus, "/", OBJECT_MANAGER)
    objects = await manager.call_get_managed_objects()
    return unwrap(objects)


def find_adapter(objects):
    for path, interfaces in objects.items():
        if ADAPTER in interfaces:
            return path
    raise SystemExit("Aucun adaptateur Bluetooth trouve dans BlueZ.")


def find_device(objects, address):
    target = address.lower()
    for path, interfaces in objects.items():
        props = interfaces.get(DEVICE)
        if props and str(props.get("Address", "")).lower() == target:
            return path
    return None


def print_gatt(objects, device_path):
    found = False
    for path, interfaces in sorted(objects.items()):
        if not path.startswith(device_path + "/"):
            continue

        service = interfaces.get(GATT_SERVICE)
        if service:
            found = True
            print(f"Service {service.get('UUID')} path={path}")
            print(f"  Primary: {service.get('Primary')}")

        characteristic = interfaces.get(GATT_CHARACTERISTIC)
        if characteristic:
            found = True
            print(f"Characteristic {characteristic.get('UUID')} path={path}")
            print(f"  Service: {characteristic.get('Service')}")
            print(f"  Flags: {', '.join(characteristic.get('Flags', []))}")

        descriptor = interfaces.get(GATT_DESCRIPTOR)
        if descriptor:
            found = True
            print(f"Descriptor {descriptor.get('UUID')} path={path}")
            print(f"  Characteristic: {descriptor.get('Characteristic')}")

    return found


def print_gatt_interface(path, interfaces):
    service = interfaces.get(GATT_SERVICE)
    if service:
        print(f"Service {service.get('UUID')} path={path}")
        print(f"  Primary: {service.get('Primary')}")
        return True

    characteristic = interfaces.get(GATT_CHARACTERISTIC)
    if characteristic:
        print(f"Characteristic {characteristic.get('UUID')} path={path}")
        print(f"  Service: {characteristic.get('Service')}")
        print(f"  Flags: {', '.join(characteristic.get('Flags', []))}")
        return True

    descriptor = interfaces.get(GATT_DESCRIPTOR)
    if descriptor:
        print(f"Descriptor {descriptor.get('UUID')} path={path}")
        print(f"  Characteristic: {descriptor.get('Characteristic')}")
        return True

    return False


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


async def probe(args):
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    objects = await get_managed_objects(bus)
    adapter_path = find_adapter(objects)
    print(f"Adaptateur: {adapter_path}")

    adapter = await get_interface(bus, adapter_path, ADAPTER)
    adapter_props = await get_interface(bus, adapter_path, PROPERTIES)
    await adapter_props.call_set(ADAPTER, "Powered", Variant("b", True))

    device_path = find_device(objects, args.address)
    if args.remove and device_path:
        print(f"Suppression cache appareil: {device_path}")
        await adapter.call_remove_device(device_path)
        device_path = None

    if not device_path:
        device_path = await wait_for_device(bus, adapter, args.address, args.scan_timeout)

    if not device_path:
        raise SystemExit(f"Appareil introuvable: {args.address}")

    manager = await get_interface(bus, "/", OBJECT_MANAGER)
    saw_gatt = False

    def on_interfaces_added(path, interfaces):
        nonlocal saw_gatt
        interfaces = unwrap(interfaces)
        if not path.startswith(device_path + "/"):
            return
        if print_gatt_interface(path, interfaces):
            saw_gatt = True

    manager.on_interfaces_added(on_interfaces_added)

    device = await get_interface(bus, device_path, DEVICE)
    device_props = await get_interface(bus, device_path, PROPERTIES)

    def on_properties_changed(interface_name, changed, invalidated):
        changed = unwrap(changed)
        if interface_name == DEVICE:
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

    print(f"Observation GATT pendant {args.duration:.1f} s...")
    deadline = asyncio.get_running_loop().time() + args.duration
    while asyncio.get_running_loop().time() < deadline:
        objects = await get_managed_objects(bus)
        if print_gatt(objects, device_path):
            saw_gatt = True
            break
        await asyncio.sleep(0.1)

    if not saw_gatt:
        print("Aucun objet GATT vu dans BlueZ pendant l'observation.")

    try:
        await device.call_disconnect()
    except Exception:
        pass


def build_parser():
    parser = argparse.ArgumentParser(
        description="Sonde BlueZ bas niveau pour voir les services GATT Kinvent.",
    )
    parser.add_argument("--address", required=True, help="Adresse BLE/MAC.")
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Supprimer le cache BlueZ de cet appareil avant le test.",
    )
    return parser


async def main():
    args = build_parser().parse_args()
    await probe(args)


if __name__ == "__main__":
    asyncio.run(main())
