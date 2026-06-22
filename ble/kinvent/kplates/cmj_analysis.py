import csv
from pathlib import Path
from statistics import median


def analyze_cmj_csv(path):
    streams = {"gauche": [], "droite": []}
    with Path(path).open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            try:
                side = row["source"]
                if side in streams:
                    streams[side].append(
                        (float(row["elapsed_s"]), float(row["source_kg"]))
                    )
            except (KeyError, TypeError, ValueError):
                continue
    if min(len(values) for values in streams.values()) < 15:
        raise ValueError("Acquisition CMJ trop courte.")

    for values in streams.values():
        values.sort()
    start_time = max(values[0][0] for values in streams.values())
    end_time = min(values[-1][0] for values in streams.values())
    if end_time - start_time < 1.5:
        raise ValueError("Acquisition CMJ trop courte.")

    def interpolate(values, target, index):
        while index + 1 < len(values) and values[index + 1][0] < target:
            index += 1
        if index + 1 >= len(values):
            return values[index][1], index
        before_t, before_value = values[index]
        after_t, after_value = values[index + 1]
        if after_t <= before_t:
            return after_value, index
        ratio = (target - before_t) / (after_t - before_t)
        return before_value + (after_value - before_value) * ratio, index

    rows = []
    indices = {"gauche": 0, "droite": 0}
    target = start_time
    while target <= end_time:
        left_kg, indices["gauche"] = interpolate(
            streams["gauche"], target, indices["gauche"]
        )
        right_kg, indices["droite"] = interpolate(
            streams["droite"], target, indices["droite"]
        )
        rows.append(
            {
                "t": target - start_time,
                "left_kg": max(0.0, left_kg),
                "right_kg": max(0.0, right_kg),
                "total_kg": max(0.0, left_kg) + max(0.0, right_kg),
            }
        )
        target += 0.01

    baseline_rows = [row for row in rows if row["t"] <= 1.0]
    if len(baseline_rows) < 10:
        raise ValueError("Référence debout insuffisante au début du test.")
    body_mass_kg = median(row["total_kg"] for row in baseline_rows)
    if body_mass_kg < 20:
        raise ValueError("Le patient doit être debout au début du test.")

    flight_threshold = max(5.0, body_mass_kg * 0.05)
    takeoff_index = landing_index = None
    start = None
    for index, row in enumerate(rows):
        if row["t"] < 1.0:
            continue
        if row["total_kg"] <= flight_threshold:
            start = index if start is None else start
            if row["t"] - rows[start]["t"] >= 0.10:
                takeoff_index = start
                break
        else:
            start = None
    if takeoff_index is None:
        raise ValueError("Aucune phase de vol détectée.")

    for index in range(takeoff_index + 1, len(rows)):
        if rows[index]["total_kg"] >= body_mass_kg * 0.20:
            landing_index = index
            break
    if landing_index is None:
        raise ValueError("Atterrissage non détecté.")

    def crossing_time(before, after, threshold):
        force_delta = after["total_kg"] - before["total_kg"]
        if abs(force_delta) < 1e-9:
            return after["t"]
        ratio = (threshold - before["total_kg"]) / force_delta
        ratio = max(0.0, min(1.0, ratio))
        return before["t"] + (after["t"] - before["t"]) * ratio

    takeoff_time = crossing_time(
        rows[max(0, takeoff_index - 1)],
        rows[takeoff_index],
        flight_threshold,
    )
    landing_threshold = body_mass_kg * 0.20
    landing_time = crossing_time(
        rows[max(takeoff_index, landing_index - 1)],
        rows[landing_index],
        landing_threshold,
    )
    flight_time = landing_time - takeoff_time
    preflight = rows[:takeoff_index]
    peak = max(preflight, key=lambda row: row["total_kg"])
    minimum = min(preflight, key=lambda row: row["total_kg"])

    return {
        "body_mass_kg": body_mass_kg,
        "takeoff_time_s": takeoff_time,
        "landing_time_s": landing_time,
        "flight_time_s": flight_time,
        "jump_height_cm": 9.81 * flight_time * flight_time / 8.0 * 100,
        "peak_force_kg": peak["total_kg"],
        "peak_force_n": peak["total_kg"] * 9.81,
        "minimum_force_kg": minimum["total_kg"],
        "left_takeoff_kg": rows[takeoff_index]["left_kg"],
        "right_takeoff_kg": rows[takeoff_index]["right_kg"],
        "sample_count": len(rows),
        "resampled_rate_hz": 100.0,
        "left_source_rate_hz": len(streams["gauche"])
        / max(end_time - start_time, 0.001),
        "right_source_rate_hz": len(streams["droite"])
        / max(end_time - start_time, 0.001),
        "raw_event_count": sum(len(values) for values in streams.values()),
    }
