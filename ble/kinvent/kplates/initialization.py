"""Initialisation du chemin V2 2.34 observé le 15 juin 2026.

Les réponses de contrôle font avancer chaque plate indépendamment. Les étapes
sans réponse identifiée conservent les délais déjà issus de cette capture.
Voir docs/protocol_kinvent.md pour les preuves et les limites de ce chemin.
"""

from dataclasses import dataclass
import time
import re


class PlateInitializationError(Exception):
    """Arrêt du protocole, sans déclencher les anciennes boucles de reconnexion."""


@dataclass(frozen=True)
class InitializationStep:
    command: bytes
    delay: float = 0.0
    response: bytes | None = None
    condition: str = ""


# 2 s est la borne des instructions de lecture/fréquence de l'application.
# C'est une borne d'échec locale, jamais un délai à attendre après une réponse.
RESPONSE_TIMEOUT = 2.0
INITIALIZATION_STEPS = (
    InitializationStep(b"\x09", response=b"KINVENT FW 2.34V"),
    InitializationStep(b"\x76", 0.20, response=b"F="),
    InitializationStep(b"\x11", condition="battery_samples"),
    InitializationStep(b"\x10"),
    InitializationStep(b"\x10", condition="drain"),
    InitializationStep(bytes.fromhex("60 00 19 00 4b 0d 0a"), 0.01),
    InitializationStep(b"\x66", response=b"F=25Hz\x00\x00"),
    InitializationStep(b"\x56", response=b"F=25Hz\x00\x00"),
    InitializationStep(bytes.fromhex("ac 00 54 f8"),
                       response=bytes.fromhex("ac 00 54 aa 52")),
    InitializationStep(bytes.fromhex("ac 01 04 a9"),
                       response=bytes.fromhex("ac 01 04 cc 65")),
    InitializationStep(b"\x11", 0.20),
)


class PlateInitialization:
    def __init__(self, side):
        self.side = side
        self.index = -1
        self.deadline = None
        self.buffer = bytearray()
        self.acknowledged = False
        self.ready_at = None
        self.last_received = None
        self.sample_buffer = bytearray()
        self.sample_count = 0
        self.battery_deadline = None
        self.received_bytes = 0
        self.received_tail = bytearray()

    @property
    def done(self):
        return self.index == len(INITIALIZATION_STEPS)

    def feed(self, value, now=None):
        if self.index < 0 or self.done:
            return
        now = time.monotonic() if now is None else now
        step = INITIALIZATION_STEPS[self.index]
        self.received_bytes += len(value)
        self.received_tail.extend(value)
        del self.received_tail[:-100]
        if step.condition == "drain":
            self.last_received = now
            return
        if step.condition == "battery_samples":
            self.sample_buffer.extend(value)
            marker = b"\xff\xff\xfe"
            while True:
                start = self.sample_buffer.find(marker)
                if start < 0:
                    self.sample_buffer[:] = self.sample_buffer[-2:]
                    break
                del self.sample_buffer[:start]
                end = self.sample_buffer.find(marker, 3)
                if end < 0:
                    if len(self.sample_buffer) > 14:
                        self.sample_buffer[:] = self.sample_buffer[-2:]
                    break
                if end == 11:
                    self.sample_count += 1
                del self.sample_buffer[:end]
                if self.sample_count >= 5:
                    self.acknowledged = True
                    break
            return
        expected = step.response
        if expected is None or self.acknowledged:
            return
        self.buffer.extend(value)
        self.acknowledged = (
            re.search(rb"F=[0-9]+Hz", self.buffer) is not None
            if step.command == b"\x76" else expected in self.buffer
        )
        if self.acknowledged and step.delay:
            self.ready_at = now + step.delay
            self.deadline = self.ready_at
        if len(self.buffer) > 100:
            del self.buffer[:-100]

    def advance(self, now, send):
        if self.done:
            return
        if self.index >= 0:
            step = INITIALIZATION_STEPS[self.index]
            if step.condition == "drain":
                if now < self.deadline:
                    return
                if now - self.last_received <= 0.300:
                    self.deadline = now + 0.080
                    return
            if (step.response is not None or step.condition == "battery_samples") and not self.acknowledged:
                if now >= self.deadline:
                    raise PlateInitializationError(
                        f"Initialisation {self.side}: réponse attendue "
                        f"{step.response.hex(' ') if step.response else '5 échantillons batterie'} après {step.command.hex(' ')} "
                        f"absente ou non prise en charge; reçu "
                        f"{bytes(self.received_tail).hex(' ') or 'aucun octet'}. "
                        f"Octets reçus à cette étape: {self.received_bytes}; "
                        f"échantillons batterie complets: {self.sample_count}/5. "
                        "Séquence arrêtée sans relance automatique."
                    )
                return
            if step.response is not None and self.acknowledged and step.delay:
                if self.ready_at is None:
                    self.ready_at = now + step.delay
                    self.deadline = self.ready_at
                if now < self.ready_at:
                    return
            if not step.condition and step.response is None and now < self.deadline:
                return
        self.index += 1
        self.received_bytes = 0
        self.received_tail.clear()
        self.buffer.clear()
        self.acknowledged = False
        self.ready_at = None
        self.sample_buffer.clear()
        self.sample_count = 0
        self.last_received = now
        if self.done:
            self.deadline = None
            return
        step = INITIALIZATION_STEPS[self.index]
        self.deadline = now + (
            RESPONSE_TIMEOUT if step.response is not None or step.condition == "battery_samples"
            else 0.080 if step.condition == "drain" else step.delay
        )
        if step.command == b"\x76":
            self.battery_deadline = now + RESPONSE_TIMEOUT
        elif step.condition == "battery_samples":
            self.deadline = self.battery_deadline
        send(step.command)
