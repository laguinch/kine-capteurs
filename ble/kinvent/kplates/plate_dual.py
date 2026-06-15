import asyncio
import csv
import time
from pathlib import Path

from ble.common.devices import KPLATE_LEFT, KPLATE_RIGHT
from ble.kinvent.kplates.driver import KPlateDriver

EXPORT_DIR = Path("storage/raw_data")
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


async def main():
    left = KPlateDriver(KPLATE_LEFT, "Plateforme gauche")
    right = KPlateDriver(KPLATE_RIGHT, "Plateforme droite")

    await asyncio.gather(
        left.connect(),
        right.connect(),
    )

    filename = EXPORT_DIR / f"kplates_dual_{int(time.time())}.csv"

    print("Streaming double plateforme... Ctrl+C pour arrêter")
    print(f"Enregistrement : {filename}")

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "left_t",
            "right_t",
            "left_kg",
            "right_kg",
            "total_kg",
            "left_n",
            "right_n",
            "total_n",
        ])

        try:
            while True:
                left_sample = left.get_latest_sample()
                right_sample = right.get_latest_sample()

                if left_sample and right_sample:
                    left_kg = left_sample["force_kg"]
                    right_kg = right_sample["force_kg"]
                    total_kg = left_kg + right_kg

                    left_n = left_kg * 9.81
                    right_n = right_kg * 9.81
                    total_n = left_n + right_n

                    now = time.time()

                    print(
                        f"G={left_kg:.1f} kg | "
                        f"D={right_kg:.1f} kg | "
                        f"TOTAL={total_kg:.1f} kg"
                    )

                    writer.writerow([
                        now,
                        left_sample["t"],
                        right_sample["t"],
                        round(left_kg, 3),
                        round(right_kg, 3),
                        round(total_kg, 3),
                        round(left_n, 2),
                        round(right_n, 2),
                        round(total_n, 2),
                    ])

                    f.flush()

                await asyncio.sleep(0.02)

        except KeyboardInterrupt:
            print("Arrêt demandé.")

        finally:
            await asyncio.gather(
                left.disconnect(),
                right.disconnect(),
            )


if __name__ == "__main__":
    asyncio.run(main())