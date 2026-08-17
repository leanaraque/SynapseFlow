"""La guarda que decide contra qué base escribe `scripts/seed.py`.

**Existe porque falló de verdad.** El 2026-08-12 el script anunció «emulador
localhost:8080» y escribió 398 registros en la base real, sin exigir
`--permitir-produccion`. La guarda no se saltó: informó lo contrario de lo que
hizo.

La causa es una discrepancia de fuentes. `Settings` toma
`FIRESTORE_EMULATOR_HOST` del archivo `.env`; el SDK de Firestore la toma del
entorno del proceso, y `pydantic-settings` **no** escribe en `os.environ`. Con la
variable en `.env` y no en el entorno —cualquier `python -m scripts.seed` en una
consola limpia— las dos fuentes dicen cosas distintas.

Estos tests fijan la única regla que hace confiable a la guarda: **decidir con la
misma fuente que usa el cliente**.
"""

from __future__ import annotations

import pytest

from scripts.seed import SeedError, _describir_destino

VARIABLE = "FIRESTORE_EMULATOR_HOST"


def test_con_la_variable_en_el_entorno_el_destino_es_el_emulador(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(VARIABLE, "localhost:8080")

    destino = _describir_destino(permitir_produccion=False)

    assert "emulador" in destino
    assert "localhost:8080" in destino


def test_sin_la_variable_en_el_entorno_no_se_escribe_sin_permiso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**El caso que falló.**

    `.env` puede tener la variable y el proceso no. Si la guarda mira `.env`,
    cree que va al emulador y deja pasar la escritura a producción.
    """
    monkeypatch.delenv(VARIABLE, raising=False)

    with pytest.raises(SeedError) as excinfo:
        _describir_destino(permitir_produccion=False)

    assert "base REAL" in str(excinfo.value)


def test_sin_la_variable_y_con_permiso_el_destino_dice_produccion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escribir en producción está permitido; anunciarlo como emulador, no."""
    monkeypatch.delenv(VARIABLE, raising=False)

    destino = _describir_destino(permitir_produccion=True)

    assert "PRODUCCIÓN" in destino
    assert "emulador" not in destino


def test_una_variable_vacia_no_cuenta_como_emulador(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`FIRESTORE_EMULATOR_HOST=` deja al SDK intentando conectar a un host vacío
    —«the target uri is not valid: dns:///»—, así que tratarla como emulador
    prometería un destino que ni siquiera existe."""
    monkeypatch.setenv(VARIABLE, "")

    with pytest.raises(SeedError):
        _describir_destino(permitir_produccion=False)


def test_la_guarda_no_consulta_settings_para_el_destino() -> None:
    """**La invariante de fondo, verificada sobre el código.**

    Es lo que hace que la guarda no pueda volver a divergir del SDK: mientras
    lea `os.environ`, dice la verdad por construcción.
    """
    import inspect

    import scripts.seed as seed

    fuente = inspect.getsource(seed._describir_destino)
    cuerpo = fuente.split('"""')[2]  # sin el docstring, que sí menciona Settings

    assert 'os.environ.get("FIRESTORE_EMULATOR_HOST"' in cuerpo
    assert "settings.using_emulator" not in cuerpo, (
        "la guarda volvió a decidir con Settings, que lee .env y no el entorno: "
        "puede anunciar el emulador mientras el SDK escribe en producción"
    )
