OFFSET_TOTAL = 141300
COUNTS_PER_KG = 10360
MIN_VALID_KG = 5.0

# Mapping validé provisoirement sur les logs de plateforme gauche.
# À confirmer pour chaque plateforme.
def parse_frame(data: bytes):
    if len(data) != 17 or data[0:3] != b"\xff\xff\xfe":
        return None

    t = int.from_bytes(data[3:5], "big", signed=False)
    v1 = int.from_bytes(data[5:8], "big", signed=True)
    v2 = int.from_bytes(data[8:11], "big", signed=True)
    v3 = int.from_bytes(data[11:14], "big", signed=True)
    v4 = int.from_bytes(data[14:17], "big", signed=True)

    av_d = v4
    av_g = v1
    ar_g = v2
    ar_d = v3

    total = av_d + av_g + ar_g + ar_d
    force_kg = (total - OFFSET_TOTAL) / COUNTS_PER_KG
    force_n = force_kg * 9.81

    return {
        "t": t,
        "av_d": av_d,
        "av_g": av_g,
        "ar_g": ar_g,
        "ar_d": ar_d,
        "total": total,
        "force_kg": force_kg,
        "force_n": force_n,
    }


def compute_distribution(sample: dict):
    total = sample["total"]
    if total == 0:
        return None

    av_d = sample["av_d"] / total * 100
    av_g = sample["av_g"] / total * 100
    ar_g = sample["ar_g"] / total * 100
    ar_d = sample["ar_d"] / total * 100

    cop_x = ((sample["av_d"] + sample["ar_d"]) - (sample["av_g"] + sample["ar_g"])) / total
    cop_y = ((sample["av_d"] + sample["av_g"]) - (sample["ar_d"] + sample["ar_g"])) / total

    return {
        "av_d_pct": av_d,
        "av_g_pct": av_g,
        "ar_g_pct": ar_g,
        "ar_d_pct": ar_d,
        "cop_x": cop_x,
        "cop_y": cop_y,
    }
