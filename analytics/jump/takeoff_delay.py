def detect_takeoff_delay_ms(left_takeoff_ms: float, right_takeoff_ms: float) -> float:
    return right_takeoff_ms - left_takeoff_ms
