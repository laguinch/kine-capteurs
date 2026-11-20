"""Initialisation du chemin V2 2.34 observé le 15 juin 2026.

Les réponses de contrôle font avancer chaque plate indépendamment. Les étapes
sans réponse identifiée conservent les délais déjà issus de cette capture.
Voir docs/protocol_kinvent.md pour les preuves et les limites de ce chemin.
"""

from dataclasses import dataclass


class PlateInitializationError(Exception):
    """Arrêt du protocole, sans déclencher les anciennes boucles de reconnexion."""


@dataclass(frozen=True)
class InitializationStep:
    command: bytes
    delay: float = 0.0
    response: bytes | None = None


# 2 s est la borne des instructions de lecture/fréquence de l'application.
# C'est une borne d'échec locale, jamais un délai à attendre après une réponse.
RESPONSE_TIMEOUT = 2.0
INITIALIZATION_STEPS = (
    InitializationStep(b"\x09", response=b"KINVENT FW 2.34V"),
    InitializationStep(b"\x76", 0.30),
    InitializationStep(b"\x11", 0.16),
    InitializationStep(b"\x10", 0.01),
    InitializationStep(b"\x10", 0.38),
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

    @property
    def done(self):
        return self.index == len(INITIALIZATION_STEPS)

    def feed(self, value):
        if self.index < 0 or self.done:
            return
        expected = INITIALIZATION_STEPS[self.index].response
        if expected is None or self.acknowledged:
            return
        # Une réponse peut être répartie entre plusieurs notifications UART.
        # Le tampon appartient uniquement à l'instruction en cours.
        self.buffer.extend(value)
        self.acknowledged = expected in self.buffer
        if len(self.buffer) > 100:
            del self.buffer[:-100]

    def advance(self, now, send):
        if self.done:
            return
        if self.index >= 0:
            step = INITIALIZATION_STEPS[self.index]
            if step.response is not None and not self.acknowledged:
                if now >= self.deadline:
                    raise PlateInitializationError(
                        f"Initialisation {self.side}: réponse attendue "
                        f"{step.response.hex(' ')} après {step.command.hex(' ')} "
                        f"absente ou non prise en charge; reçu "
                        f"{bytes(self.buffer).hex(' ') or 'aucun octet'}. "
                        "Séquence arrêtée sans relance automatique."
                    )
                return
            if step.response is None and now < self.deadline:
                return
        self.index += 1
        self.buffer.clear()
        self.acknowledged = False
        if self.done:
            self.deadline = None
            return
        step = INITIALIZATION_STEPS[self.index]
        self.deadline = now + (
            RESPONSE_TIMEOUT if step.response is not None else step.delay
        )
        send(step.command)
