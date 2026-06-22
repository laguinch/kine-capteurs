"""Décodage et calibration des trames du K-Pull / KFORCE Link."""

FRAME_PREFIX = b"\xff\xff\xfe"
FRAME_LENGTH = 17


def parse_raw_frame(data: bytes):
    if len(data) != FRAME_LENGTH or data[:3] != FRAME_PREFIX:
        return None
    return {
        "t": int.from_bytes(data[3:5], "big", signed=False),
        "raw_force": int.from_bytes(data[5:8], "big", signed=True),
        "raw_aux_1": int.from_bytes(data[8:11], "big", signed=True),
        "raw_aux_2": int.from_bytes(data[11:14], "big", signed=True),
        "raw_aux_3": int.from_bytes(data[14:17], "big", signed=True),
    }


def calibrate_sample(raw_sample, tare_offset, counts_per_kg=None):
    force_counts = raw_sample["raw_force"] - tare_offset
    result = {
        **raw_sample,
        "tare_offset": tare_offset,
        "force_counts": force_counts,
    }
    if counts_per_kg is not None:
        if counts_per_kg <= 0:
            raise ValueError("Le coefficient counts_per_kg doit être positif.")
        force_kg = force_counts / counts_per_kg
        result["force_kg"] = force_kg
        result["force_n"] = force_kg * 9.81
    else:
        result["force_kg"] = None
        result["force_n"] = None
    return result


def compute_counts_per_kg(tare_offset, loaded_raw_force, known_load_kg):
    if known_load_kg <= 0:
        raise ValueError("La charge connue doit être strictement positive.")
    delta = abs(loaded_raw_force - tare_offset)
    if delta == 0:
        raise ValueError("La charge connue n'a produit aucune variation.")
    return delta / known_load_kg
