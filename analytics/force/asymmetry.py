def asymmetry_percent(left: float, right: float) -> float:
    if max(left, right) == 0:
        return 0.0
    return abs(left - right) / max(left, right) * 100
