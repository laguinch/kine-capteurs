"""Constantes et décodage du protocole GATT de l'ANR M40."""

ANR_COMPANY_ID = 0x05DA

DEVICE_INFO_SERVICE = "0000180a-0000-1000-8000-00805f9b34fb"
MODEL_NUMBER_CHAR = "00002a24-0000-1000-8000-00805f9b34fb"
SERIAL_NUMBER_CHAR = "00002a25-0000-1000-8000-00805f9b34fb"
FIRMWARE_REVISION_CHAR = "00002a26-0000-1000-8000-00805f9b34fb"
HARDWARE_REVISION_CHAR = "00002a27-0000-1000-8000-00805f9b34fb"
SOFTWARE_REVISION_CHAR = "00002a28-0000-1000-8000-00805f9b34fb"

AUTOMATION_IO_SERVICE = "00001815-0000-1000-8000-00805f9b34fb"
ANALOG_CHAR = "00002a58-0000-1000-8000-00805f9b34fb"
DIGITAL_CHAR = "00002a56-0000-1000-8000-00805f9b34fb"

BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"


def is_m40_manufacturer_data(manufacturer_data):
    return ANR_COMPANY_ID in (manufacturer_data or {})


def decode_emg(value: bytes):
    if len(value) < 2:
        raise ValueError("La valeur EMG M40 doit contenir au moins 2 octets.")
    reading = int.from_bytes(value[:2], "little", signed=False)
    if not 0 <= reading <= 1023:
        raise ValueError(f"Valeur EMG M40 hors plage: {reading}")
    return reading


def decode_battery(value: bytes):
    if not value:
        raise ValueError("Valeur de batterie M40 vide.")
    reading = value[0]
    if reading > 100:
        raise ValueError(f"Batterie M40 hors plage: {reading}")
    return reading


def encode_device_id(device_id: int):
    if not 1 <= device_id <= 24:
        raise ValueError("L'identifiant couleur M40 doit être compris entre 1 et 24.")
    return bytes([device_id])
