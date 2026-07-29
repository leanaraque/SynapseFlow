"""Fixtures compartidas.

Los tests marcados `emulator` necesitan el emulador de Firestore corriendo:

    firebase emulators:start --only firestore --project synapseflow-lean

Se saltean con un mensaje explícito si no está, en lugar de fallar con un
timeout de gRPC que no dice nada.
"""

from __future__ import annotations

import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest

EMULATOR_HOST = os.environ.get("FIRESTORE_EMULATOR_HOST", "localhost:8080")
PROYECTO_TEST = "synapseflow-lean"


def _emulador_disponible() -> bool:
    host, _, puerto = EMULATOR_HOST.partition(":")
    try:
        with socket.create_connection((host, int(puerto or 8080)), timeout=1.5):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session", autouse=True)
def _entorno_de_test() -> Iterator[None]:
    """Apunta el proceso al emulador y a credenciales de mentira.

    Se hace en una fixture y no en el módulo para que el orden de importación no
    determine si el test pega contra el emulador o contra Firestore de verdad.
    Un test que escribe en producción por un import mal ordenado es un accidente
    que solo se descubre mirando la factura.
    """
    previo = dict(os.environ)
    os.environ["FIRESTORE_EMULATOR_HOST"] = EMULATOR_HOST
    os.environ["GOOGLE_CLOUD_PROJECT"] = PROYECTO_TEST
    os.environ.setdefault("GOOGLE_API_KEY", "clave-de-test")
    os.environ.setdefault("SYNAPSEFLOW_PROVIDER", "gemini")

    from synapseflow.config import reset_settings_cache
    from synapseflow.persistence.client import reset_client_cache

    reset_settings_cache()
    reset_client_cache()

    yield

    os.environ.clear()
    os.environ.update(previo)
    reset_settings_cache()
    reset_client_cache()


@pytest.fixture(scope="session")
def requiere_emulador() -> None:
    """Saltea el test si el emulador no está.

    De sesión para que `saver` —que también es de sesión— pueda depender de ella.
    """
    if not _emulador_disponible():
        pytest.skip(
            f"el emulador de Firestore no responde en {EMULATOR_HOST}. "
            "Arrancarlo con: firebase emulators:start --only firestore"
        )


@pytest.fixture
def thread_id() -> str:
    """Hilo único por test, para que no se pisen entre sí."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture(scope="session")
async def saver(requiere_emulador: None) -> AsyncIterator["object"]:
    """Un único saver para la sesión.

    De sesión y no por test porque comparte el cliente cacheado de Firestore, que
    está atado al event loop donde se creó. Los tests se aíslan por `thread_id`,
    no por instancia del saver.
    """
    from synapseflow.persistence.checkpointer import FirestoreSaver

    yield FirestoreSaver()
