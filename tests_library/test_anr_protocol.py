import unittest

from ble.anr.protocol import (
    ANR_COMPANY_ID,
    decode_battery,
    decode_emg,
    encode_device_id,
    is_m40_manufacturer_data,
)


class AnrProtocolTest(unittest.TestCase):
    def test_detects_anr_company_id(self):
        self.assertTrue(
            is_m40_manufacturer_data({ANR_COMPANY_ID: b"\x01\x02"})
        )
        self.assertFalse(is_m40_manufacturer_data({0x004C: b"\x01"}))

    def test_decodes_little_endian_emg(self):
        self.assertEqual(decode_emg(bytes([0xFF, 0x03])), 1023)
        self.assertEqual(decode_emg(bytes([0x00, 0x02])), 512)

    def test_rejects_emg_outside_ten_bit_range(self):
        with self.assertRaises(ValueError):
            decode_emg(bytes([0x00, 0x04]))

    def test_decodes_battery_percentage(self):
        self.assertEqual(decode_battery(bytes([87])), 87)

    def test_encodes_device_color_id(self):
        self.assertEqual(encode_device_id(1), b"\x01")
        self.assertEqual(encode_device_id(24), b"\x18")
        with self.assertRaises(ValueError):
            encode_device_id(25)


if __name__ == "__main__":
    unittest.main()
