"""Cada persona tiene un tope de recorridos del grafo por hora.

Los dos tests que más importan:

- `test_una_rafaga_simultanea_no_pasa_del_tope`: diez consultas a la vez con
  tope cinco dejan pasar cinco. Sin la precondición pasaban todas, porque las
  diez leían el mismo contador.
- `test_sin_cupo_la_aprobacion_no_cierra_el_pendiente`: el tope se aplica antes
  de decidir. Si fuera después, una aprobación quedaría registrada y sin
  ejecutarse.

Ver services/api/cuota.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

import pytest

from services.api.cuota import CuotaAgotadaError, consumir_cuota
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.persistence.client import Collections
from tests.api.firestore_en_memoria import ClienteEnMemoria

INSPECTOR = ExecutionContext(usuario="uid-inspector", rol="inspector")
OTRO = ExecutionContext(usuario="uid-otro", rol="inspector")
SUPERVISOR = ExecutionContext(usuario="uid-supervisor", rol="supervisor_mantenimiento")

A_LAS_10_45 = dt.datetime(2026, 11, 3, 10, 45, tzinfo=dt.UTC)


@pytest.fixture
def db() -> ClienteEnMemoria:
    return ClienteEnMemoria()


# ─────────────────────────────────────────────────────────────────────────────
# La regla
# ─────────────────────────────────────────────────────────────────────────────


async def test_se_cuenta_cada_recorrido(db: ClienteEnMemoria) -> None:
    usados = [
        await consumir_cuota(INSPECTOR, cliente=db, limite=3, ahora=A_LAS_10_45) for _ in range(3)
    ]

    assert usados == [1, 2, 3]


async def test_pasado_el_tope_es_429(db: ClienteEnMemoria) -> None:
    for _ in range(3):
        await consumir_cuota(INSPECTOR, cliente=db, limite=3, ahora=A_LAS_10_45)

    with pytest.raises(CuotaAgotadaError) as error:
        await consumir_cuota(INSPECTOR, cliente=db, limite=3, ahora=A_LAS_10_45)

    assert error.value.status_code == 429


async def test_el_429_dice_cuando_vuelve_a_haber_cupo(db: ClienteEnMemoria) -> None:
    """A las 10:45 faltan quince minutos para la hora siguiente."""
    await consumir_cuota(INSPECTOR, cliente=db, limite=1, ahora=A_LAS_10_45)

    with pytest.raises(CuotaAgotadaError) as error:
        await consumir_cuota(INSPECTOR, cliente=db, limite=1, ahora=A_LAS_10_45)

    assert (error.value.headers or {})["Retry-After"] == str(15 * 60)


async def test_el_rechazo_no_suma_al_contador(db: ClienteEnMemoria) -> None:
    """Si sumara, insistir alargaría el castigo sin límite."""
    await consumir_cuota(INSPECTOR, cliente=db, limite=1, ahora=A_LAS_10_45)
    for _ in range(3):
        with pytest.raises(CuotaAgotadaError):
            await consumir_cuota(INSPECTOR, cliente=db, limite=1, ahora=A_LAS_10_45)

    (fila,) = db.contenido(Collections.QUOTAS).values()
    assert fila["usados"] == 1


async def test_la_hora_siguiente_empieza_de_cero(db: ClienteEnMemoria) -> None:
    await consumir_cuota(INSPECTOR, cliente=db, limite=1, ahora=A_LAS_10_45)

    usados = await consumir_cuota(
        INSPECTOR, cliente=db, limite=1, ahora=A_LAS_10_45 + dt.timedelta(minutes=20)
    )

    assert usados == 1


async def test_el_cupo_es_de_cada_persona(db: ClienteEnMemoria) -> None:
    """Que un usuario agote el suyo no deja sin servicio a los demás."""
    await consumir_cuota(INSPECTOR, cliente=db, limite=1, ahora=A_LAS_10_45)

    assert await consumir_cuota(OTRO, cliente=db, limite=1, ahora=A_LAS_10_45) == 1


async def test_por_defecto_el_tope_sale_de_la_configuracion(db: ClienteEnMemoria) -> None:
    from synapseflow.config import get_settings

    tope = get_settings().consultas_por_hora
    for _ in range(tope):
        await consumir_cuota(INSPECTOR, cliente=db, ahora=A_LAS_10_45)

    with pytest.raises(CuotaAgotadaError):
        await consumir_cuota(INSPECTOR, cliente=db, ahora=A_LAS_10_45)


# ─────────────────────────────────────────────────────────────────────────────
# Concurrencia
# ─────────────────────────────────────────────────────────────────────────────


async def test_una_rafaga_simultanea_no_pasa_del_tope() -> None:
    """**El caso que justifica la precondición.**

    El doble devuelve el control al event loop después de cada lectura, así que
    las diez corrutinas leen el contador antes de que cualquiera escriba.
    """
    db = ClienteEnMemoria(ceder_al_leer=True)

    resultados = await asyncio.gather(
        *(consumir_cuota(INSPECTOR, cliente=db, limite=5, ahora=A_LAS_10_45) for _ in range(10)),
        return_exceptions=True,
    )

    aceptadas = [r for r in resultados if isinstance(r, int)]
    rechazadas = [r for r in resultados if isinstance(r, CuotaAgotadaError)]
    assert sorted(aceptadas) == [1, 2, 3, 4, 5]
    assert len(rechazadas) == 5
    (fila,) = db.contenido(Collections.QUOTAS).values()
    assert fila["usados"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# Por HTTP
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
    """La API con tope 1, hilos en memoria y un grafo que anota si se armó."""
    from services.api import main
    from services.api.auth import usuario_actual
    from services.api.hilos import reclamar_hilo

    grafos_armados: list[str] = []

    def _grafo_para(ctx: ExecutionContext) -> Any:
        grafos_armados.append(ctx.usuario)
        raise AssertionError("sin cupo, el grafo no tenía que armarse")

    async def _cuota(ctx: ExecutionContext) -> int:
        return await consumir_cuota(ctx, cliente=db, limite=1)

    monkeypatch.setattr(main, "grafo_para", _grafo_para)
    monkeypatch.setattr(main, "consumir_cuota", _cuota)
    monkeypatch.setattr(
        main, "reclamar_hilo", lambda ctx, hilo: reclamar_hilo(ctx, hilo, cliente=db)
    )

    def como(ctx: ExecutionContext) -> None:
        async def _quien() -> ExecutionContext:
            return ctx

        main.app.dependency_overrides[usuario_actual] = _quien

    yield como, grafos_armados
    main.app.dependency_overrides.clear()


async def test_sin_cupo_la_consulta_es_429_y_no_llega_al_modelo(
    cliente: Any, api: Any, db: ClienteEnMemoria
) -> None:
    como, grafos_armados = api
    await consumir_cuota(INSPECTOR, cliente=db, limite=1)

    como(INSPECTOR)
    respuesta = await cliente.post("/api/consultas", json={"pregunta": "¿P-2101-A sigue apto?"})

    assert respuesta.status_code == 429
    assert int(respuesta.headers["retry-after"]) > 0
    assert "consultas por hora" in respuesta.json()["error"]
    assert grafos_armados == []


async def test_sin_cupo_la_aprobacion_no_cierra_el_pendiente(
    cliente: Any, api: Any, db: ClienteEnMemoria
) -> None:
    """**El orden importa.** `decidir` marca el pendiente como resuelto; si la
    cuota rechazara después, la aprobación quedaría registrada y el grafo nunca
    se reanudaría."""
    from services.api.aprobaciones import PENDIENTE, registrar_pendiente
    from synapseflow.ontology import get_ontology

    como, _ = api
    hilo = "hilo-con-propuesta"
    await registrar_pendiente(
        INSPECTOR.model_copy(update={"thread_id": hilo}),
        {
            "herramienta": "solicitar_parada_equipo",
            "argumentos": {"tag": "P-2101-A"},
            "decisiones": ["approve", "reject"],
        },
        cliente=db,
        ontologia=get_ontology(),
    )
    await consumir_cuota(SUPERVISOR, cliente=db, limite=1)

    como(SUPERVISOR)
    respuesta = await cliente.post(f"/api/aprobaciones/{hilo}", json={"decision": "aprobar"})

    assert respuesta.status_code == 429
    assert db.contenido(Collections.APPROVALS)[hilo]["estado"] == PENDIENTE


# ─────────────────────────────────────────────────────────────────────────────
# Cuando Firestore aborta por contención
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def sin_espera(monkeypatch: pytest.MonkeyPatch) -> None:
    """Los reintentos esperan al azar; en los tests, cero."""
    monkeypatch.setattr("services.api.cuota.ESPERA_BASE", 0)


async def test_un_abort_al_estrenar_la_hora_se_reintenta(
    db: ClienteEnMemoria, sin_espera: None
) -> None:
    """**El error que tuvo el CI en rojo.** El emulador nuevo aborta escrituras
    con contención («Transaction lock timeout») en lugar de aplicarlas, y
    Firestore real puede hacer lo mismo. Antes salía como un 500."""
    db.abortar_proximas(1)

    assert await consumir_cuota(INSPECTOR, cliente=db, limite=3, ahora=A_LAS_10_45) == 1


async def test_un_abort_al_incrementar_se_reintenta(db: ClienteEnMemoria, sin_espera: None) -> None:
    await consumir_cuota(INSPECTOR, cliente=db, limite=3, ahora=A_LAS_10_45)
    db.abortar_proximas(2)

    assert await consumir_cuota(INSPECTOR, cliente=db, limite=3, ahora=A_LAS_10_45) == 2
    (fila,) = db.contenido(Collections.QUOTAS).values()
    assert fila["usados"] == 2


async def test_si_aborta_siempre_es_429_y_no_500(db: ClienteEnMemoria, sin_espera: None) -> None:
    """Agotados los reintentos se rechaza como ráfaga, con un error que la API
    sabe responder, y sin contar nada."""
    db.abortar_proximas(100)

    with pytest.raises(CuotaAgotadaError) as error:
        await consumir_cuota(INSPECTOR, cliente=db, limite=3, ahora=A_LAS_10_45)

    assert error.value.status_code == 429
    assert db.contenido(Collections.QUOTAS) == {}


# ─────────────────────────────────────────────────────────────────────────────
# Contra Firestore de verdad
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.emulator
async def test_el_contador_funciona_contra_firestore(requiere_emulador: None) -> None:
    """El doble imita `create()` y la precondición; esto confirma que Firestore
    se comporta igual, incluida la ráfaga."""
    persona = ExecutionContext(usuario=f"test-cuota-{uuid.uuid4()}", rol="inspector")

    resultados = await asyncio.gather(
        *(consumir_cuota(persona, limite=3) for _ in range(6)), return_exceptions=True
    )

    aceptadas = [r for r in resultados if isinstance(r, int)]
    otras = [r for r in resultados if not isinstance(r, int | CuotaAgotadaError)]
    assert sorted(aceptadas) == [1, 2, 3]
    # El mensaje dice qué llegó: sin él, el CI solo decía «assert False».
    assert not otras, f"excepciones que no son un 429: {otras!r}"
