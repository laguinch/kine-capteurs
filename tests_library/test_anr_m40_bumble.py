import asyncio
import unittest

from scripts.anr_m40_bumble import (
    ANR_ANALOG_CCCD_HANDLE,
    ANR_ANALOG_VALUE_HANDLE,
    ANR_BATTERY_VALUE_HANDLE,
    ANR_DEVICE_ID_VALUE_HANDLE,
    ANRM40BumbleClient,
)


class FakeGattClient:
    def __init__(self):
        self.notification_subscribers = {}
        self.writes = []
        self.reads = []

    async def write_value(self, handle, value, with_response=False):
        self.writes.append((handle, value, with_response))

    async def read_value(self, handle):
        self.reads.append(handle)
        return b"\x1e"


class AnrM40BumbleTest(unittest.TestCase):
    def test_fixed_handles_match_raw_hci_discovery(self):
        self.assertEqual(ANR_ANALOG_VALUE_HANDLE, 0x001E)
        self.assertEqual(ANR_ANALOG_CCCD_HANDLE, 0x001F)
        self.assertEqual(ANR_DEVICE_ID_VALUE_HANDLE, 0x0021)
        self.assertEqual(ANR_BATTERY_VALUE_HANDLE, 0x0025)

    def test_configures_m40_without_gatt_discovery(self):
        client = ANRM40BumbleClient(
            transport="usb:0",
            address="68:23:B0:B6:AF:F3",
            address_type="public",
            device_id=1,
        )
        gatt = FakeGattClient()

        asyncio.run(client.configure_fixed_handles(gatt))

        self.assertIn(
            client.handle_emg,
            gatt.notification_subscribers[ANR_ANALOG_VALUE_HANDLE],
        )
        self.assertEqual(gatt.reads, [ANR_BATTERY_VALUE_HANDLE])
        self.assertEqual(
            gatt.writes,
            [
                (ANR_DEVICE_ID_VALUE_HANDLE, b"\x01", True),
                (ANR_ANALOG_CCCD_HANDLE, b"\x01\x00", True),
            ],
        )


if __name__ == "__main__":
    unittest.main()
