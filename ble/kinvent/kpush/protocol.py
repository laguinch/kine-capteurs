"""Décodage des trames de force du K-Push / KFORCE Muscle."""

COUNTS_PER_KG = 10_000
FRAME_PREFIX = b"\xff\xff\xfe"
FORCE_FRAME_LENGTH = 17


def parse_raw_frame(data: bytes):
    """Retourne la trame brute de force, ou ``None`` si elle est technique."""
    if len(data) != FORCE_FRAME_LENGTH or data[:3] != FRAME_PREFIX:
        return None
    return {
        "t": int.from_bytes(data[3:5], "big", signed=False),
        "raw_force": int.from_bytes(data[5:8], "big", signed=True),
        "raw_aux_1": int.from_bytes(data[8:11], "big", signed=True),
        "raw_aux_2": int.from_bytes(data[11:14], "big", signed=True),
        "raw_aux_3": int.from_bytes(data[14:17], "big", signed=True),
    }


def calibrate_sample(raw_sample: dict, tare_offset: int):
    force_counts = raw_sample["raw_force"] - tare_offset
    force_kg = force_counts / COUNTS_PER_KG
    return {
        **raw_sample,
        "tare_offset": tare_offset,
        "force_counts": force_counts,
        "force_kg": force_kg,
        "force_n": force_kg * 9.81,
    }
