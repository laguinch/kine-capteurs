"""Décodage des orientations du K-Move / KFORCE Sens."""

import math


FRAME_PREFIX = b"\xff\xff\xfe"
QUATERNION_FRAME_LENGTH = 20
QUATERNION_SCALE = 16384.0


def _centered_u16(data):
    return int.from_bytes(data, "big", signed=False) - 0x8000


def normalize_quaternion(quaternion):
    norm = math.sqrt(sum(value * value for value in quaternion))
    if norm < 1e-9:
        return None
    return tuple(value / norm for value in quaternion)


def parse_quaternion_frame(data):
    if (
        len(data) != QUATERNION_FRAME_LENGTH
        or data[:3] != FRAME_PREFIX
    ):
        return None
    quaternion = normalize_quaternion(
        tuple(
            _centered_u16(data[offset:offset + 2]) / QUATERNION_SCALE
            for offset in range(5, 13, 2)
        )
    )
    if quaternion is None:
        return None
    return {
        "t": int.from_bytes(data[3:5], "big", signed=False),
        "quaternion": quaternion,
        "accel_x_raw": _centered_u16(data[13:15]),
        "accel_y_raw": _centered_u16(data[15:17]),
        "accel_z_raw": _centered_u16(data[17:19]),
        "battery_pct": data[19],
    }


def conjugate(quaternion):
    w, x, y, z = quaternion
    return w, -x, -y, -z


def multiply(left, right):
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def relative_quaternion(reference, current):
    result = multiply(conjugate(reference), current)
    normalized = normalize_quaternion(result)
    if normalized is None:
        raise ValueError("Quaternion relatif invalide.")
    return normalized


def quaternion_to_euler_degrees(quaternion):
    """Retourne les rotations intrinsèques X, Y et Z en degrés."""
    w, x, y, z = quaternion
    sin_x = 2.0 * (w * x + y * z)
    cos_x = 1.0 - 2.0 * (x * x + y * y)
    rotation_x = math.atan2(sin_x, cos_x)

    sin_y = 2.0 * (w * y - z * x)
    rotation_y = math.copysign(
        math.pi / 2.0,
        sin_y,
    ) if abs(sin_y) >= 1.0 else math.asin(sin_y)

    sin_z = 2.0 * (w * z + x * y)
    cos_z = 1.0 - 2.0 * (y * y + z * z)
    rotation_z = math.atan2(sin_z, cos_z)

    return {
        "rotation_x_deg": math.degrees(rotation_x),
        "rotation_y_deg": math.degrees(rotation_y),
        "rotation_z_deg": math.degrees(rotation_z),
    }
