"""Préparation du backend Bluetooth Bumble pour Kinvent.

Ce module ne remplace pas encore les pilotes Kinvent HCI validés par les
captures officielles. Il fournit uniquement la plomberie Bumble commune et des
garde-fous pour migrer capteur par capteur sans modifier les séquences
Kinvent observées dans ``bug_report/``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


RAW_HCI_BACKEND = "raw-hci"
BUMBLE_BACKEND = "bumble"
SUPPORTED_BACKENDS = {RAW_HCI_BACKEND, BUMBLE_BACKEND}

DEFAULT_BUMBLE_TRANSPORT = "usb:0"


class BumbleBackendError(RuntimeError):
    """Erreur de configuration ou de disponibilité du backend Bumble."""


@dataclass(frozen=True)
class BumbleBackendConfig:
    """Configuration explicite du transport Bumble.

    ``transport`` reprend la syntaxe Bumble, par exemple ``usb:0`` pour le
    futur nRF52840 Dongle en firmware HCI USB, ou un autre transport documenté
    par Bumble pour les diagnostics.
    """

    transport: str = DEFAULT_BUMBLE_TRANSPORT


def normalize_backend(value: str | None) -> str:
    backend = (value or RAW_HCI_BACKEND).strip().lower().replace("_", "-")
    if backend not in SUPPORTED_BACKENDS:
        raise BumbleBackendError(
            "Backend Bluetooth inconnu: "
            f"{value!r}. Attendu: raw-hci ou bumble."
        )
    return backend


def backend_from_environment() -> str:
    return normalize_backend(os.environ.get("KINE_BLUETOOTH_BACKEND"))


def bumble_config_from_environment() -> BumbleBackendConfig:
    return BumbleBackendConfig(
        transport=os.environ.get("KINE_BUMBLE_TRANSPORT", DEFAULT_BUMBLE_TRANSPORT)
    )


def require_bumble():
    """Importe Bumble uniquement quand le backend expérimental est demandé."""

    try:
        import bumble  # noqa: F401
    except ImportError as exc:
        raise BumbleBackendError(
            "Le backend Bumble est demandé, mais le paquet Python 'bumble' "
            "n'est pas installé. Lancez d'abord l'installation des "
            "dépendances du projet."
        ) from exc


def manager_backend_notice(backend: str, config: BumbleBackendConfig | None = None):
    if backend == RAW_HCI_BACKEND:
        return "Backend Bluetooth: HCI direct validé par les captures Kinvent."
    config = config or bumble_config_from_environment()
    return (
        "Backend Bluetooth: Bumble expérimental "
        f"(transport {config.transport}). Les commandes Kinvent restent celles "
        "observées dans les captures officielles."
    )
