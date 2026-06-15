import asyncio
from bleak import BleakScanner

async def scan(timeout: float = 8.0):
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        print(f"{d.name} | {d.address}")

if __name__ == "__main__":
    asyncio.run(scan())
