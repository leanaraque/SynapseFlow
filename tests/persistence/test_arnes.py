"""El arnés de pruebas protege la corrida unitaria de la base.

El paso «Tests unitarios» del CI corre `-m "not emulator"` sin emulador. Un test
que lee Firestore sin la marca no se saltea: reintenta cinco minutos y falla con
un `RetryError` que no dice qué pasó. Así estuvo el CI en rojo desde agosto.

`conftest._firestore_solo_con_marca` convierte ese caso en un error inmediato y
con nombre. Estos tests verifican las dos mitades: que frene sin la marca y que
no estorbe con ella.
"""

from __future__ import annotations

import time

import pytest

from synapseflow.persistence.client import get_client

# Se compara por nombre y no por clase: importar `tests.conftest` desde un test
# puede cargar el módulo dos veces y producir dos clases distintas.
ERROR_DEL_ARNES = "FirestoreSinMarcaError"


async def test_tocar_firestore_sin_la_marca_falla_al_instante() -> None:
    inicio = time.monotonic()

    with pytest.raises(RuntimeError, match="emulator") as error:
        await get_client().collection("assets").document("P-2101-A").get()

    assert type(error.value).__name__ == ERROR_DEL_ARNES

    # Un segundo es generoso: lo que se descarta son los minutos de reintentos.
    assert time.monotonic() - inicio < 1.0


def test_el_mensaje_nombra_al_test_culpable() -> None:
    with pytest.raises(RuntimeError) as error:
        get_client().document("assets/P-2101-A")

    assert "test_el_mensaje_nombra_al_test_culpable" in str(error.value)


@pytest.mark.emulator
def test_con_la_marca_el_cliente_no_se_intercepta() -> None:
    """Armar una referencia no hace red, así que no necesita el emulador vivo."""
    referencia = get_client().collection("assets").document("P-2101-A")

    assert referencia.id == "P-2101-A"
