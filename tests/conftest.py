"""Fixtures compartidas.

Los tests marcados `emulator` necesitan el emulador de Firestore corriendo:

    firebase emulators:start --only firestore --project synapseflow-5fc52

Se saltean con un mensaje explícito si no está, en lugar de fallar con un
timeout de gRPC que no dice nada.
"""

from __future__ import annotations

import contextlib
import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
EMULATOR_HOST = os.environ.get("FIRESTORE_EMULATOR_HOST", "localhost:8080")
PROYECTO_TEST = "synapseflow-5fc52"


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

    # El .env se carga ANTES de la clave de mentira. Las variables de entorno
    # tienen prioridad sobre el .env en pydantic-settings, así que el
    # `setdefault` de abajo estaba pisando la credencial real: los tests
    # `live_llm` recibían "clave-de-test" y morían con API_KEY_INVALID tras
    # catorce minutos de reintentos. El marcador existía en las convenciones y
    # era estructuralmente inservible.
    with contextlib.suppress(ImportError):
        from dotenv import load_dotenv

        load_dotenv(RAIZ / ".env")

    # El destino de Firestore se fuerza igual: un test que escriba en la base
    # real por heredar el .env es un accidente que solo se descubre en la
    # factura.
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


class FirestoreSinMarcaError(RuntimeError):
    """Un test sin la marca `emulator` intentó hablar con Firestore."""


# Puntos de entrada del cliente asíncrono. Todo acceso a la base pasa por uno de
# ellos, así que interceptarlos alcanza para detectar cualquier lectura o
# escritura sin depender de cómo cada módulo importó el cliente.
_ENTRADAS_DE_FIRESTORE = (
    "collection",
    "collection_group",
    "collections",
    "document",
    "get_all",
    "batch",
    "transaction",
)


@pytest.fixture(autouse=True)
def _firestore_solo_con_marca(request: pytest.FixtureRequest) -> Iterator[None]:
    """Un test que toca Firestore sin la marca `emulator` falla al instante.

    **Esto existe porque el CI estuvo en rojo tres meses por un caso así.** Un
    test del pipeline de gobernanza llegaba a `consultar_activo` y leía la base.
    Con el emulador corriendo en la máquina de desarrollo pasaba; en el paso
    «Tests unitarios» del CI, que corre `-m "not emulator"` sin emulador, el
    cliente reintentaba durante cinco minutos y fallaba con un `RetryError` que
    no nombraba el problema.

    Se intercepta la clase y no `get_client`: los módulos importan el cliente
    con `from ... import get_client`, así que parchear el nombre del módulo no
    alcanzaría a los que ya lo importaron. Los fakes en memoria no heredan de
    `AsyncClient` y no se ven afectados.
    """
    if request.node.get_closest_marker("emulator") is not None:
        yield
        return

    from google.cloud.firestore import AsyncClient

    def _prohibido(nombre: str) -> object:
        def _falla(*_: object, **__: object) -> object:
            raise FirestoreSinMarcaError(
                f"{request.node.nodeid} llamó a AsyncClient.{nombre}() sin la marca "
                "`@pytest.mark.emulator`. Marcalo, o reemplazá el acceso a la base "
                "con un fake si lo que se prueba no es la persistencia."
            )

        return _falla

    with pytest.MonkeyPatch.context() as parche:
        for nombre in _ENTRADAS_DE_FIRESTORE:
            parche.setattr(AsyncClient, nombre, _prohibido(nombre))
        yield


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
async def saver(requiere_emulador: None) -> AsyncIterator[object]:
    """Un único saver para la sesión.

    De sesión y no por test porque comparte el cliente cacheado de Firestore, que
    está atado al event loop donde se creó. Los tests se aíslan por `thread_id`,
    no por instancia del saver.
    """
    from synapseflow.persistence.checkpointer import FirestoreSaver

    yield FirestoreSaver()
