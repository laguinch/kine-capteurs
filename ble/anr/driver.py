"""Pilote GATT asynchrone de l'ANR M40."""

from bleak import BleakClient

from ble.anr.protocol import (
    ANALOG_CHAR,
    BATTERY_LEVEL_CHAR,
    DIGITAL_CHAR,
    FIRMWARE_REVISION_CHAR,
    HARDWARE_REVISION_CHAR,
    MODEL_NUMBER_CHAR,
    SERIAL_NUMBER_CHAR,
    SOFTWARE_REVISION_CHAR,
    decode_battery,
    decode_emg,
    encode_device_id,
)


class ANRDriver:
    def __init__(self, address):
        self.address = address
        self.client = None
        self.latest_emg = None

    async def connect(self):
        self.client = BleakClient(self.address)
        await self.client.connect()

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    async def read_identity(self):
        fields = {
            "model": MODEL_NUMBER_CHAR,
            "serial": SERIAL_NUMBER_CHAR,
            "firmware": FIRMWARE_REVISION_CHAR,
            "hardware": HARDWARE_REVISION_CHAR,
            "software": SOFTWARE_REVISION_CHAR,
        }
        result = {}
        for name, uuid in fields.items():
            try:
                value = await self.client.read_gatt_char(uuid)
            except Exception:
                result[name] = None
            else:
                result[name] = bytes(value).decode("utf-8", errors="replace").strip(
                    "\x00"
                )
        return result

    async def read_battery(self):
        try:
            value = await self.client.read_gatt_char(BATTERY_LEVEL_CHAR)
        except Exception:
            return None
        return decode_battery(bytes(value))

    async def set_device_id(self, device_id):
        value = encode_device_id(device_id)
        await self.client.write_gatt_char(DIGITAL_CHAR, value, response=True)
        return device_id

    async def start_emg(self, callback):
        def notify(_sender, value):
            self.latest_emg = decode_emg(bytes(value))
            callback(self.latest_emg)

        await self.client.start_notify(ANALOG_CHAR, notify)

    async def stop_emg(self):
        if self.client and self.client.is_connected:
            await self.client.stop_notify(ANALOG_CHAR)
