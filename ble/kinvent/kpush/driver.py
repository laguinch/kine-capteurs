import asyncio
from bleak import BleakClient

ADDRESS = "A7C04967-91F3-7D19-9734-CA10AE769FBC"

UART = "49535343-1e4d-4bd9-ba61-23c647249616"
ALT_NOTIFY = "49535343-4c8a-39b3-2f49-511cff073b7e"

OFFSET = 28600
COUNTS_PER_KG = 10000

def show_data(sender, data):
    if len(data) == 17 and data[0:3] == b"\xff\xff\xfe":
        t = int.from_bytes(data[3:5], "big", signed=False)
        raw3 = int.from_bytes(data[5:8], "big", signed=True)

        force_kg = (raw3 - OFFSET) / COUNTS_PER_KG
        force_n = force_kg * 9.81

        print(f"t={t} | raw={raw3} | {force_kg:.2f} kg | {force_n:.1f} N")

async def send(client, data, label):
    print("SEND", label, ":", data.hex(" "))
    await client.write_gatt_char(UART, data, response=False)
    await asyncio.sleep(0.5)

async def main():
    async with BleakClient(ADDRESS) as client:
        print("Connecté :", client.is_connected)

        await client.start_notify(UART, show_data)
        await client.start_notify(ALT_NOTIFY, show_data)

        sequence = [
            (b"\x10", "init 10"),
            (b"\x09", "init 09"),
            (b"\x21", "init 21"),
            (b"\x76", "freq/status 76"),
            (b"\x11", "start 11"),
            (b"\x10", "init 10"),
            (b"\x10", "init 10"),
            (b"\x56", "mode force 56"),
            (bytes.fromhex("ac 00 54 f8"), "commande calibration/force"),
            (b"\x11", "stream 11"),
        ]

        for data, label in sequence:
            await send(client, data, label)

        print("Pousse sur le K-Push pendant 30 secondes...")
        await asyncio.sleep(30)

asyncio.run(main())