OFFSET_AV_D = 36050
OFFSET_AV_G = 35950
OFFSET_AR_G = 33500
OFFSET_AR_D = 34050
COUNTS_PER_KG = 10360
MIN_VALID_KG = 5.0


def parse_frame(data: bytes):
    if len(data) != 17 or data[0:3] != b"\xff\xff\xfe":
        return None

    t = int.from_bytes(data[3:5], "big", signed=False)
    v1 = int.from_bytes(data[5:8], "big", signed=True)
    v2 = int.from_bytes(data[8:11], "big", signed=True)
    v3 = int.from_bytes(data[11:14], "big", signed=True)
    v4 = int.from_bytes(data[14:17], "big", signed=True)

    raw_av_d = v4
    raw_av_g = v1
    raw_ar_g = v2
    raw_ar_d = v3

    av_d = raw_av_d - OFFSET_AV_D
    av_g = raw_av_g - OFFSET_AV_G
    ar_g = raw_ar_g - OFFSET_AR_G
    ar_d = raw_ar_d - OFFSET_AR_D
    total = av_d + av_g + ar_g + ar_d
    force_kg = total / COUNTS_PER_KG
    force_n = force_kg * 9.81

    return {
        "t": t,
        "raw_av_d": raw_av_d,
        "raw_av_g": raw_av_g,
        "raw_ar_g": raw_ar_g,
        "raw_ar_d": raw_ar_d,
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
    if total <= 0:
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
