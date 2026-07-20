"""Backend Bluetooth Bumble pour le dongle nRF52840 Kinvent.

Bumble remplace uniquement la plomberie Bluetooth. Les commandes envoyées aux
capteurs restent celles observées dans les captures officielles Kinvent du
dossier ``bug_report/``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


BUMBLE_BACKEND = "bumble"
SUPPORTED_BACKENDS = {BUMBLE_BACKEND}

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
    backend = (value or BUMBLE_BACKEND).strip().lower().replace("_", "-")
    if backend not in SUPPORTED_BACKENDS:
        raise BumbleBackendError(
            "Backend Bluetooth inconnu: "
            f"{value!r}. Le serveur utilise uniquement bumble/nRF52840."
        )
    return backend


def backend_from_environment() -> str:
    return normalize_backend(os.environ.get("KINE_BLUETOOTH_BACKEND"))


def bumble_config_from_environment() -> BumbleBackendConfig:
    return BumbleBackendConfig(
        transport=os.environ.get("KINE_BUMBLE_TRANSPORT", DEFAULT_BUMBLE_TRANSPORT)
    )


def require_bumble():
    """Vérifie que Bumble est disponible pour le dongle nRF52840."""

    try:
        import bumble  # noqa: F401
    except ImportError as exc:
        raise BumbleBackendError(
            "Le backend Bumble est demandé, mais le paquet Python 'bumble' "
            "n'est pas installé. Lancez d'abord l'installation des "
            "dépendances du projet."
        ) from exc


def manager_backend_notice(backend: str, config: BumbleBackendConfig | None = None):
    config = config or bumble_config_from_environment()
    return (
        "Backend Bluetooth: Bumble/nRF52840 "
        f"(transport {config.transport}). Les commandes Kinvent restent celles "
        "observées dans les captures officielles."
    )
