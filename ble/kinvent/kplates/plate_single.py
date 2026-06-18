"""Script de test pour une seule plateforme K-Force Plate.
À lancer en ligne de commande pendant la phase de validation BLE.
"""

import asyncio
from bleak import BleakClient
from ble.kinvent.kplates.protocol import parse_frame, compute_distribution, MIN_VALID_KG

UART_CHAR = "49535343-1e4d-4bd9-ba61-23c647249616"
ALT_NOTIFY_CHAR = "49535343-4c8a-39b3-2f49-511cff073b7e"

INIT_COMMANDS = [b"\x10", b"\x09", b"\x21", b"\x76", b"\x11", b"\x10", b"\x10", b"\x56", bytes.fromhex("ac 00 54 f8"), b"\x11"]


def show_data(sender, data: bytes):
    sample = parse_frame(data)
    if sample is None:
        print(f"len={len(data)} | {data.hex(' ')}")
        return

    dist = compute_distribution(sample)
    kg = sample["force_kg"]
    if kg < MIN_VALID_KG or dist is None:
        print(f"t={sample['t']} | {kg:.1f} kg | hors appui")
        return

    print(
        f"t={sample['t']} | {kg:.1f} kg | "
        f"AV_D={dist['av_d_pct']:.1f}% AV_G={dist['av_g_pct']:.1f}% "
        f"AR_G={dist['ar_g_pct']:.1f}% AR_D={dist['ar_d_pct']:.1f}% | "
        f"COP_x={dist['cop_x']:.3f} COP_y={dist['cop_y']:.3f}"
    )


async def main(address: str):
    async with BleakClient(address) as client:
        print("Connecté :", client.is_connected)
        await client.start_notify(UART_CHAR, show_data)
        await client.start_notify(ALT_NOTIFY_CHAR, show_data)
        for cmd in INIT_COMMANDS:
            await client.write_gatt_char(UART_CHAR, cmd, response=False)
            await asyncio.sleep(0.5)
        print("Streaming... Ctrl+C pour arrêter")
        await asyncio.sleep(30)


if __name__ == "__main__":
    # Remplacer par l'adresse gauche ou droite.
    ADDRESS = "E8:EB:1B:6F:A7:5F"
    asyncio.run(main(ADDRESS))
