"""Cada hilo de conversación tiene un dueño, y solo él lo continúa.

El test que más importa es `test_un_supervisor_no_puede_proponer_en_el_hilo_ajeno_y_aprobarlo`:
reproduce el ataque que este control cierra. Antes, un supervisor podía escribir
en el hilo de un inspector, hacer que el agente propusiera una parada —que
quedaba a nombre del inspector— y aprobarla él mismo.

Ver services/api/hilos.py
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.hilos import HiloAjenoError, reclamar_hilo
from synapseflow.governance.auditoria import TipoEvento
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.persistence.client import Collections
from tests.api.firestore_en_memoria import ClienteEnMemoria

HILO = "hilo-del-inspector"
INSPECTOR = ExecutionContext(usuario="uid-inspector", rol="inspector")
SUPERVISOR = ExecutionContext(usuario="uid-supervisor", rol="supervisor_mantenimiento")


@pytest.fixture
def db() -> ClienteEnMemoria:
    return ClienteEnMemoria()


# ─────────────────────────────────────────────────────────────────────────────
# La regla
# ─────────────────────────────────────────────────────────────────────────────


async def test_quien_estrena_un_hilo_queda_como_dueno(db: ClienteEnMemoria) -> None:
    await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    assert db.contenido(Collections.THREADS)[HILO]["propietario"] == INSPECTOR.usuario


async def test_el_dueno_puede_continuar_su_conversacion(db: ClienteEnMemoria) -> None:
    """El control positivo: sin él, un 403 para todos también pasaría el test de abajo."""
    await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    await reclamar_hilo(INSPECTOR, HILO, cliente=db)


async def test_otra_persona_no_puede_continuar_el_hilo(db: ClienteEnMemoria) -> None:
    await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    with pytest.raises(HiloAjenoError) as error:
        await reclamar_hilo(SUPERVISOR, HILO, cliente=db)

    assert error.value.status_code == 403
    # Y el dueño no cambió por el intento.
    assert db.contenido(Collections.THREADS)[HILO]["propietario"] == INSPECTOR.usuario


async def test_un_abort_al_estrenar_el_hilo_se_reintenta(
    db: ClienteEnMemoria, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Firestore puede abortar el `create()` por contención sin aplicarlo."""
    monkeypatch.setattr("services.api.hilos.ESPERA_BASE", 0)
    db.abortar_proximas(1)

    await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    assert db.contenido(Collections.THREADS)[HILO]["propietario"] == INSPECTOR.usuario


async def test_si_aborta_siempre_es_503_y_no_403(
    db: ClienteEnMemoria, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**No se sabe de quién es el hilo, así que no se puede decir que es ajeno.**
    Un 403 acusaría a la persona de entrar a una conversación que no es suya."""
    monkeypatch.setattr("services.api.hilos.ESPERA_BASE", 0)
    db.abortar_proximas(100)

    with pytest.raises(HTTPException) as error:
        await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    assert error.value.status_code == 503
    assert not isinstance(error.value, HiloAjenoError)


async def test_el_intento_queda_en_el_log_de_auditoria(db: ClienteEnMemoria) -> None:
    """Un patrón de intentos sobre hilos ajenos es la señal que busca un auditor."""
    await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    with pytest.raises(HiloAjenoError):
        await reclamar_hilo(SUPERVISOR, HILO, cliente=db)

    eventos = list(db.contenido(Collections.AUDIT_LOG).values())
    assert [e["tipo"] for e in eventos] == [TipoEvento.ACCESO_DENEGADO]
    assert eventos[0]["user_id"] == SUPERVISOR.usuario
    assert eventos[0]["thread_id"] == HILO


# ─────────────────────────────────────────────────────────────────────────────
# El ataque, de punta a punta por HTTP
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def cliente() -> Any:
    import httpx

    from services.api.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as cliente:
        yield cliente


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, db: ClienteEnMemoria) -> Any:
    """La API con los hilos en memoria y un grafo que registra quién lo pidió."""
    from services.api import main
    from services.api.auth import usuario_actual

    grafos_armados: list[str] = []

    def _grafo_para(ctx: ExecutionContext) -> Any:
        # Si se llega acá, el agente ya iba a correr con la pregunta del intruso.
        grafos_armados.append(ctx.usuario)
        raise AssertionError("el grafo no tenía que armarse para un hilo ajeno")

    monkeypatch.setattr(main, "grafo_para", _grafo_para)
    monkeypatch.setattr(
        main, "reclamar_hilo", lambda ctx, hilo: reclamar_hilo(ctx, hilo, cliente=db)
    )

    def como(ctx: ExecutionContext) -> None:
        async def _quien() -> ExecutionContext:
            return ctx

        main.app.dependency_overrides[usuario_actual] = _quien

    yield como, grafos_armados
    main.app.dependency_overrides.clear()


async def test_un_supervisor_no_puede_proponer_en_el_hilo_ajeno_y_aprobarlo(
    cliente: Any, api: Any, db: ClienteEnMemoria
) -> None:
    """**El ataque que este control cierra.**

    El supervisor ve el hilo del inspector en su bandeja y le escribe al agente
    en ese hilo. Antes, la propuesta resultante quedaba a nombre del inspector y
    el supervisor podía aprobarla: separación de funciones salteada. Ahora la
    pregunta ni siquiera llega al agente.
    """
    como, grafos_armados = api
    await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    como(SUPERVISOR)
    respuesta = await cliente.post(
        "/api/consultas",
        json={"pregunta": "Solicitá la parada de P-2101-A.", "thread_id": HILO},
    )

    assert respuesta.status_code == 403
    assert "otra persona" in respuesta.json()["error"]
    assert grafos_armados == []


async def test_la_cabecera_del_hilo_tampoco_sirve_para_entrar(
    cliente: Any, api: Any, db: ClienteEnMemoria
) -> None:
    """El hilo también puede llegar por `X-Thread-Id`: es la misma puerta."""
    from services.api import main
    from services.api.auth import usuario_actual

    _, grafos_armados = api
    await reclamar_hilo(INSPECTOR, HILO, cliente=db)

    async def _supervisor_con_cabecera() -> ExecutionContext:
        return SUPERVISOR.model_copy(update={"thread_id": HILO})

    main.app.dependency_overrides[usuario_actual] = _supervisor_con_cabecera
    respuesta = await cliente.post("/api/consultas", json={"pregunta": "¿Y ahora?"})

    assert respuesta.status_code == 403
    assert grafos_armados == []


# ─────────────────────────────────────────────────────────────────────────────
# Contra Firestore de verdad
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.emulator
async def test_create_rechaza_un_segundo_dueno_en_firestore(requiere_emulador: None) -> None:
    """El doble en memoria imita `create()`; esto confirma que Firestore lanza
    la misma excepción, que es de lo que depende todo el control."""
    hilo = f"test-hilo-{uuid.uuid4()}"

    await reclamar_hilo(INSPECTOR, hilo)
    await reclamar_hilo(INSPECTOR, hilo)

    with pytest.raises(HiloAjenoError):
        await reclamar_hilo(SUPERVISOR, hilo)
