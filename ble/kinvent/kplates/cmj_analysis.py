import csv
from pathlib import Path
from statistics import median


def _resampled_rows(path, minimum_source_samples=15):
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
    if min(len(values) for values in streams.values()) < minimum_source_samples:
        raise ValueError("Acquisition CMJ trop courte.")

    for values in streams.values():
        values.sort()
    start_time = max(values[0][0] for values in streams.values())
    end_time = min(values[-1][0] for values in streams.values())
    if end_time <= start_time:
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
    return rows, streams, start_time, end_time


def detect_stable_body_mass(path):
    try:
        rows, _, _, _ = _resampled_rows(path, minimum_source_samples=5)
    except (OSError, ValueError):
        return {"ready": False, "status": "waiting_presence"}

    window_size = 100
    if len(rows) < window_size:
        return {"ready": False, "status": "waiting_presence"}
    for end in range(window_size, len(rows) + 1):
        window = rows[end - window_size:end]
        values = [row["total_kg"] for row in window]
        body_mass_kg = median(values)
        if body_mass_kg < 20:
            continue
        tolerance = max(2.0, body_mass_kg * 0.03)
        if max(abs(value - body_mass_kg) for value in values) <= tolerance:
            return {
                "ready": True,
                "status": "ready",
                "body_mass_kg": body_mass_kg,
                "reference_start_s": window[0]["t"],
                "reference_end_s": window[-1]["t"],
            }
    if max(row["total_kg"] for row in rows) >= 20:
        return {"ready": False, "status": "stabilizing"}
    return {"ready": False, "status": "waiting_presence"}


def analyze_cmj_csv(path):
    rows, streams, start_time, end_time = _resampled_rows(path)
    if end_time - start_time < 1.5:
        raise ValueError("Acquisition CMJ trop courte.")

    preparation = detect_stable_body_mass(path)
    if not preparation["ready"]:
        raise ValueError(
            "Aucun poids stable n'a été enregistré avant le saut."
        )
    body_mass_kg = preparation["body_mass_kg"]
    search_after = preparation["reference_end_s"]

    flight_threshold = max(5.0, body_mass_kg * 0.05)
    takeoff_index = landing_index = None
    start = None
    for index, row in enumerate(rows):
        if row["t"] <= search_after:
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
    propulsion = [
        row
        for row in rows
        if search_after <= row["t"] <= takeoff_time
    ]
    left_peak = max(row["left_kg"] for row in propulsion)
    right_peak = max(row["right_kg"] for row in propulsion)
    peak_difference_kg = abs(left_peak - right_peak)
    peak_average = (left_peak + right_peak) / 2
    peak_asymmetry_pct = (
        peak_difference_kg / peak_average * 100
        if peak_average > 0
        else 0.0
    )

    stable_rows = [
        row
        for row in rows
        if preparation["reference_start_s"]
        <= row["t"]
        <= preparation["reference_end_s"]
    ]
    left_reference_kg = median(row["left_kg"] for row in stable_rows)
    right_reference_kg = median(row["right_kg"] for row in stable_rows)

    def side_crossing_time(
        field,
        threshold,
        start_index,
        direction,
        sustain_s=0.06,
    ):
        candidate = None
        for index in range(max(1, start_index), len(rows)):
            value = rows[index][field]
            reached = (
                value <= threshold
                if direction == "down"
                else value >= threshold
            )
            if reached:
                candidate = index if candidate is None else candidate
                if rows[index]["t"] - rows[candidate]["t"] >= sustain_s:
                    before = rows[max(0, candidate - 1)]
                    after = rows[candidate]
                    force_delta = after[field] - before[field]
                    if abs(force_delta) < 1e-9:
                        return after["t"]
                    ratio = (threshold - before[field]) / force_delta
                    ratio = max(0.0, min(1.0, ratio))
                    return before["t"] + (
                        after["t"] - before["t"]
                    ) * ratio
            else:
                candidate = None
        raise ValueError(
            "Impossible de distinguer les événements gauche et droite."
        )

    search_index = next(
        index for index, row in enumerate(rows)
        if row["t"] >= search_after
    )
    left_takeoff_time = side_crossing_time(
        "left_kg",
        max(2.0, left_reference_kg * 0.10),
        search_index,
        "down",
    )
    right_takeoff_time = side_crossing_time(
        "right_kg",
        max(2.0, right_reference_kg * 0.10),
        search_index,
        "down",
    )
    landing_search_index = next(
        index for index, row in enumerate(rows)
        if row["t"] >= max(left_takeoff_time, right_takeoff_time) + 0.05
    )
    left_landing_time = side_crossing_time(
        "left_kg",
        max(5.0, left_reference_kg * 0.20),
        landing_search_index,
        "up",
        sustain_s=0.03,
    )
    right_landing_time = side_crossing_time(
        "right_kg",
        max(5.0, right_reference_kg * 0.20),
        landing_search_index,
        "up",
        sustain_s=0.03,
    )

    def first_side(left_time, right_time):
        if abs(left_time - right_time) < 1e-9:
            return "simultané"
        return "gauche" if left_time < right_time else "droite"

    left_rate = len(streams["gauche"]) / max(end_time - start_time, 0.001)
    right_rate = len(streams["droite"]) / max(end_time - start_time, 0.001)
    effective_rate = min(left_rate, right_rate)
    temporal_resolution_ms = 1000.0 / max(effective_rate, 0.001)
    takeoff_difference_ms = abs(
        left_takeoff_time - right_takeoff_time
    ) * 1000
    landing_difference_ms = abs(
        left_landing_time - right_landing_time
    ) * 1000

    return {
        "body_mass_kg": body_mass_kg,
        "weight_reference_start_s": preparation["reference_start_s"],
        "weight_reference_end_s": preparation["reference_end_s"],
        "takeoff_time_s": takeoff_time,
        "landing_time_s": landing_time,
        "flight_time_s": flight_time,
        "jump_height_cm": 9.81 * flight_time * flight_time / 8.0 * 100,
        "peak_force_kg": peak["total_kg"],
        "peak_force_n": peak["total_kg"] * 9.81,
        "left_peak_force_kg": left_peak,
        "right_peak_force_kg": right_peak,
        "left_peak_force_n": left_peak * 9.81,
        "right_peak_force_n": right_peak * 9.81,
        "peak_force_difference_kg": peak_difference_kg,
        "peak_force_difference_n": peak_difference_kg * 9.81,
        "peak_force_asymmetry_pct": peak_asymmetry_pct,
        "minimum_force_kg": minimum["total_kg"],
        "left_takeoff_kg": rows[takeoff_index]["left_kg"],
        "right_takeoff_kg": rows[takeoff_index]["right_kg"],
        "left_takeoff_time_s": left_takeoff_time,
        "right_takeoff_time_s": right_takeoff_time,
        "takeoff_first_side": first_side(
            left_takeoff_time,
            right_takeoff_time,
        ),
        "takeoff_difference_ms": takeoff_difference_ms,
        "takeoff_difference_reliable": (
            takeoff_difference_ms >= temporal_resolution_ms
        ),
        "left_landing_time_s": left_landing_time,
        "right_landing_time_s": right_landing_time,
        "landing_first_side": first_side(
            left_landing_time,
            right_landing_time,
        ),
        "landing_difference_ms": landing_difference_ms,
        "landing_difference_reliable": (
            landing_difference_ms >= temporal_resolution_ms
        ),
        "sample_count": len(rows),
        "resampled_rate_hz": 100.0,
        "left_source_rate_hz": left_rate,
        "right_source_rate_hz": right_rate,
        "temporal_resolution_ms": temporal_resolution_ms,
        "raw_event_count": sum(len(values) for values in streams.values()),
    }
