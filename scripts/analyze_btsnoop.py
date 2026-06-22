"""Affiche les échanges ATT de contrôle d'une capture Android btsnoop."""

import argparse
import struct
from datetime import datetime, timezone


UNIX_EPOCH_US = 0x00DCDDB30F2F8000


def format_address(value):
    return ":".join(f"{byte:02X}" for byte in reversed(value))


def format_time(value):
    unix_us = value - UNIX_EPOCH_US
    return datetime.fromtimestamp(
        unix_us / 1_000_000,
        timezone.utc,
    ).isoformat()


def read_records(path):
    records = []
    with open(path, "rb") as source:
        if source.read(8) != b"btsnoop\x00":
            raise ValueError("Format btsnoop invalide")
        source.read(8)
        while header := source.read(24):
            if len(header) < 24:
                break
            _, included, flags, _, stamp = struct.unpack(">IIIIQ", header)
            records.append((format_time(stamp), flags, source.read(included)))
    return records


def parse_connections(records):
    connections = {}
    for when, _, packet in records:
        if not packet or packet[0] != 0x04 or len(packet) < 4:
            continue
        if packet[1] != 0x3E:
            continue
        params = packet[3:]
        if params and params[0] in (0x01, 0x0A) and len(params) >= 12:
            status = params[1]
            if status == 0:
                handle = struct.unpack_from("<H", params, 2)[0] & 0x0FFF
                connections[handle] = (
                    format_address(params[6:12]),
                    when,
                )
    return connections


def parse_att(records, connections):
    fragments = {}
    packets = []
    for when, flags, packet in records:
        if not packet or packet[0] != 0x02 or len(packet) < 9:
            continue
        handle_flags, acl_length = struct.unpack_from("<HH", packet, 1)
        handle = handle_flags & 0x0FFF
        boundary = (handle_flags >> 12) & 0x03
        payload = packet[5:5 + acl_length]
        if boundary in (0, 2):
            if len(payload) < 4:
                continue
            expected, cid = struct.unpack_from("<HH", payload)
            fragments[handle] = [
                expected,
                cid,
                bytearray(payload[4:]),
                when,
                flags,
            ]
        elif handle in fragments:
            fragments[handle][2].extend(payload)
        else:
            continue

        expected, cid, body, started, initial_flags = fragments[handle]
        if len(body) >= expected:
            if cid == 0x0004:
                address = connections.get(handle, ("?", None))[0]
                packets.append(
                    (
                        started,
                        initial_flags,
                        handle,
                        address,
                        bytes(body[:expected]),
                    )
                )
            del fragments[handle]
    return packets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture")
    parser.add_argument("--address")
    parser.add_argument("--notifications", action="store_true")
    args = parser.parse_args()

    records = read_records(args.capture)
    connections = parse_connections(records)
    packets = parse_att(records, connections)
    address_filter = args.address.upper() if args.address else None

    print("Connexions:")
    for handle, (address, when) in connections.items():
        print(f"{when} handle=0x{handle:04x} address={address}")
    print("Échanges ATT:")
    for when, flags, handle, address, payload in packets:
        if address_filter and address != address_filter:
            continue
        if not args.notifications and payload[0] in (0x1B, 0x1D):
            continue
        direction = "RX" if flags & 1 else "TX"
        print(
            f"{when} {direction} handle=0x{handle:04x} "
            f"{address} {payload.hex(' ')}"
        )


if __name__ == "__main__":
    main()
