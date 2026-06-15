import asyncio
from bleak import BleakClient

from ble.kinvent.kplates.protocol import parse_frame

NOTIFY_CHAR = "49535343-1e4d-4bd9-ba61-23c647249616"
WRITE_CHAR = "49535343-8841-43f4-a8d4-ecbe34729bb3"

INIT_COMMANDS = [
    b"\x10",
    b"\x09",
    b"\x21",
    b"\x76",
    b"\x11",
    b"\x10",
    b"\x10",
    b"\x56",
    bytes.fromhex("ac 00 54 f8"),
    b"\x11",
]


class KPlateDriver:
    def __init__(self, address: str, name: str):
        self.address = address
        self.name = name
        self.client = None
        self.latest_sample = None

    def _on_notify(self, sender, data: bytes):
        sample = parse_frame(data)
        if sample is not None:
            self.latest_sample = sample

    async def connect(self):
        self.client = BleakClient(self.address)
        await self.client.connect()
        print(f"{self.name} connectée :", self.client.is_connected)

        await self.client.start_notify(NOTIFY_CHAR, self._on_notify)

        for cmd in INIT_COMMANDS:
            await self.client.write_gatt_char(WRITE_CHAR, cmd, response=False)
            await asyncio.sleep(0.2)

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()

    def get_latest_sample(self):
        return self.latest_sample