"""Fixtures de los tests de la API."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.api.firestore_en_memoria import ClienteEnMemoria


@pytest.fixture(autouse=True)
def _cuota_en_memoria(monkeypatch: pytest.MonkeyPatch) -> Iterator[ClienteEnMemoria]:
    """La cuota por persona cuenta en memoria en todos los tests de endpoints.

    Cada endpoint que corre el grafo consume cupo antes de armarlo. Lo que se
    prueba en la mayoría de estos tests es otra cosa —el transporte, los
    permisos, los costos—, y sin esto todos necesitarían Firestore. La cuota
    tiene sus propios tests en test_cuota.py, contra la función y no contra
    este reemplazo.
    """
    from services.api import cuota, main

    contadores = ClienteEnMemoria()
    original = cuota.consumir_cuota

    async def _en_memoria(ctx, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("cliente", contadores)
        return await original(ctx, **kwargs)

    monkeypatch.setattr(main, "consumir_cuota", _en_memoria)
    yield contadores
