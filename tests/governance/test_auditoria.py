"""Contrato del log de auditoría.

La propiedad que define el módulo es que es **append-only**: un reintento nunca
sobreescribe un evento anterior. Perder un evento es peor que duplicarlo, porque
el duplicado es ruido detectable y el sobreescrito no deja rastro de que existió.

La segunda es que el log guarda la **llave del razonamiento** —`thread_id` y
`checkpoint_id`— y no solo el hecho. Un log que dice «se solicitó la parada de
P-2101-A» documenta lo que pasó y no permite defenderlo.

Ver docs/plan/fases/F4-gobernanza.md § F4.3
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from google.cloud.firestore_v1.base_query import FieldFilter

from synapseflow.governance.auditoria import (
    EventoAuditoria,
    TipoEvento,
    evento_de_acceso_denegado,
    evento_de_accion,
    evento_de_aprobacion,
    evento_de_respuesta,
    registrar,
    registrar_lote,
)
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.persistence.client import Collections, get_client

INSPECTOR = ExecutionContext(usuario="uid-inspector", rol="inspector", thread_id="hilo-7")
SUPERVISOR = ExecutionContext(
    usuario="uid-supervisor", rol="supervisor_mantenimiento", thread_id="hilo-7"
)


@pytest.fixture
async def limpiar_hilo(requiere_emulador: None) -> AsyncIterator[str]:
    """Un thread_id propio del test, y su limpieza."""
    import uuid

    hilo = f"hilo-{uuid.uuid4().hex[:8]}"
    yield hilo

    cliente = get_client()
    consulta = cliente.collection(Collections.AUDIT_LOG).where(
        filter=FieldFilter("thread_id", "==", hilo)
    )
    async for documento in consulta.stream():
        await documento.reference.delete()


def contexto(hilo: str, ctx: ExecutionContext = INSPECTOR) -> ExecutionContext:
    return ctx.model_copy(update={"thread_id": hilo})


async def eventos_de(hilo: str) -> list[dict[str, Any]]:
    cliente = get_client()
    consulta = cliente.collection(Collections.AUDIT_LOG).where(
        filter=FieldFilter("thread_id", "==", hilo)
    )
    return [d.to_dict() async for d in consulta.stream()]


# ─────────────────────────────────────────────────────────────────────────────
# La estructura del evento
# ─────────────────────────────────────────────────────────────────────────────


def test_el_evento_lleva_la_llave_del_razonamiento() -> None:
    """`thread_id` y `checkpoint_id` son lo que permite reconstruir el contexto.

    El log no guarda el razonamiento: guarda la llave para recuperarlo del
    checkpointer.
    """
    evento = evento_de_accion(INSPECTOR, "solicitar_parada_equipo", checkpoint_id="ckpt-42")

    assert evento.thread_id == "hilo-7"
    assert evento.checkpoint_id == "ckpt-42"


def test_los_campos_se_llaman_como_los_declaran_los_indices() -> None:
    """`ts` y `user_id`, no `momento` y `usuario`.

    Un índice compuesto sobre un campo que nadie escribe es inútil, y la consulta
    que lo necesita falla recién en producción. Ya pasó una vez en este
    repositorio con `llm_usage`.
    """
    declarados = set(EventoAuditoria.model_fields)

    assert "ts" in declarados and "user_id" in declarados
    assert "momento" not in declarados and "usuario" not in declarados


def test_los_indices_declarados_cubren_los_campos_del_evento() -> None:
    """Se contrasta contra el archivo que efectivamente se despliega."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    indices = json.loads((raiz / "firestore.indexes.json").read_text(encoding="utf-8"))

    campos_indexados = {
        campo["fieldPath"]
        for indice in indices["indexes"]
        if indice["collectionGroup"] == Collections.AUDIT_LOG
        for campo in indice["fields"]
    }

    assert campos_indexados, "audit_log no tiene índices declarados"
    assert campos_indexados <= set(EventoAuditoria.model_fields), (
        f"firestore.indexes.json indexa campos que el evento no escribe: "
        f"{campos_indexados - set(EventoAuditoria.model_fields)}"
    )


@pytest.mark.parametrize(
    ("constructor", "esperado"),
    [
        (lambda c: evento_de_accion(c, "x"), TipoEvento.ACCION_EJECUTADA),
        (lambda c: evento_de_accion(c, "x", propuesta=True), TipoEvento.ACCION_PROPUESTA),
        (
            lambda c: evento_de_aprobacion(c, "x", concedida=True),
            TipoEvento.APROBACION_CONCEDIDA,
        ),
        (
            lambda c: evento_de_aprobacion(c, "x", concedida=False),
            TipoEvento.APROBACION_RECHAZADA,
        ),
        (
            lambda c: evento_de_respuesta(c, emitida=True, veredicto="fundamentada"),
            TipoEvento.RESPUESTA_EMITIDA,
        ),
        (
            lambda c: evento_de_respuesta(c, emitida=False, veredicto="sin_fundamento"),
            TipoEvento.RESPUESTA_RECHAZADA,
        ),
        (
            lambda c: evento_de_acceso_denegado(c, "x", motivo="rol"),
            TipoEvento.ACCESO_DENEGADO,
        ),
    ],
)
def test_cada_constructor_produce_su_tipo(constructor: Any, esperado: TipoEvento) -> None:
    """El conjunto de tipos es cerrado: un log donde cada call site inventa el
    suyo no se puede consultar, y la consulta es todo el punto."""
    assert constructor(INSPECTOR).tipo is esperado


def test_la_aprobacion_registra_al_proponente_aparte_de_quien_decide() -> None:
    """Sin eso no se puede verificar la separación de funciones mirando el log.

    `user_id` es quien decidió; el proponente va en el resultado.
    """
    evento = evento_de_aprobacion(
        SUPERVISOR, "solicitar_parada_equipo", concedida=True, propuesta_por="uid-inspector"
    )

    assert evento.user_id == "uid-supervisor"
    assert evento.resultado["propuesta_por"] == "uid-inspector"


def test_una_respuesta_rechazada_se_registra_igual_que_una_emitida() -> None:
    """Un log que solo guarda lo que salió no permite medir cuántas veces el
    sistema se negó, que es una métrica de éxito del proyecto."""
    evento = evento_de_respuesta(
        INSPECTOR, emitida=False, veredicto="sin_fundamento", motivo="sin citas"
    )

    assert evento.tipo is TipoEvento.RESPUESTA_RECHAZADA
    assert evento.resultado["veredicto"] == "sin_fundamento"


def test_el_evento_guarda_el_mapa_de_tokenizacion() -> None:
    """Para contrastar qué vio el proveedor externo contra qué había de verdad."""
    evento = evento_de_respuesta(
        INSPECTOR,
        emitida=True,
        veredicto="fundamentada",
        tokenizacion={"«INSPECTOR_1»": "LEG-00042"},
    )

    assert evento.tokenizacion["«INSPECTOR_1»"] == "LEG-00042"


def test_el_evento_es_inmutable_en_memoria() -> None:
    evento = evento_de_accion(INSPECTOR, "x")
    with pytest.raises(Exception):  # noqa: B017 - pydantic lanza ValidationError
        evento.user_id = "otro"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# Append-only, contra el emulador
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.emulator
async def test_un_evento_se_escribe_y_se_recupera(limpiar_hilo: str) -> None:
    ctx = contexto(limpiar_hilo)
    await registrar(evento_de_accion(ctx, "solicitar_parada_equipo", checkpoint_id="ckpt-1"))

    guardados = await eventos_de(limpiar_hilo)
    assert len(guardados) == 1
    assert guardados[0]["action_id"] == "solicitar_parada_equipo"
    assert guardados[0]["checkpoint_id"] == "ckpt-1"
    assert guardados[0]["user_id"] == "uid-inspector"


@pytest.mark.emulator
async def test_registrar_el_mismo_evento_dos_veces_no_sobreescribe(
    limpiar_hilo: str,
) -> None:
    """**La propiedad que define el módulo.**

    Con un id derivado del contenido, un reintento tras un timeout borraría el
    evento anterior. Un duplicado es ruido detectable —mismo contenido, distinto
    id—; un sobreescrito no deja rastro de que existió.
    """
    ctx = contexto(limpiar_hilo)
    evento = evento_de_accion(ctx, "emitir_orden_trabajo")

    primero = await registrar(evento)
    segundo = await registrar(evento)

    assert primero != segundo, "dos registros produjeron el mismo id: se sobreescribiría"
    assert len(await eventos_de(limpiar_hilo)) == 2


@pytest.mark.emulator
async def test_el_id_ordena_cronologicamente(limpiar_hilo: str) -> None:
    """Es donde alguien va a mirar primero en la consola de Firestore."""
    ctx = contexto(limpiar_hilo)

    viejo = EventoAuditoria(
        ts=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        user_id=ctx.usuario,
        rol=ctx.rol,
        tipo=TipoEvento.ACCION_EJECUTADA,
        thread_id=limpiar_hilo,
    )
    nuevo = evento_de_accion(ctx, "x")

    id_viejo = await registrar(viejo)
    id_nuevo = await registrar(nuevo)

    assert id_viejo < id_nuevo


@pytest.mark.emulator
async def test_un_lote_escribe_todos_los_eventos(limpiar_hilo: str) -> None:
    """Un turno del grafo produce del orden de diez eventos.

    Escribirlos de a uno en el camino de la respuesta agrega latencia a lo que el
    usuario está esperando.
    """
    ctx = contexto(limpiar_hilo)
    eventos = [evento_de_accion(ctx, f"accion_{i}") for i in range(5)]

    ids = await registrar_lote(eventos)

    assert len(ids) == 5
    assert len(set(ids)) == 5, "el lote produjo ids repetidos"
    assert len(await eventos_de(limpiar_hilo)) == 5


@pytest.mark.emulator
async def test_un_lote_vacio_no_escribe(limpiar_hilo: str) -> None:
    assert await registrar_lote([]) == []
    assert await eventos_de(limpiar_hilo) == []


@pytest.mark.emulator
async def test_los_eventos_de_un_hilo_se_recuperan_juntos(limpiar_hilo: str) -> None:
    """Es la consulta que reconstruye una conversación completa."""
    ctx = contexto(limpiar_hilo)
    await registrar_lote(
        [
            evento_de_accion(ctx, "consultar_activo"),
            evento_de_accion(ctx, "solicitar_parada_equipo", propuesta=True),
            evento_de_aprobacion(
                contexto(limpiar_hilo, SUPERVISOR),
                "solicitar_parada_equipo",
                concedida=True,
                propuesta_por=ctx.usuario,
            ),
        ]
    )

    guardados = await eventos_de(limpiar_hilo)
    tipos = {e["tipo"] for e in guardados}

    assert len(guardados) == 3
    assert tipos == {
        TipoEvento.ACCION_EJECUTADA.value,
        TipoEvento.ACCION_PROPUESTA.value,
        TipoEvento.APROBACION_CONCEDIDA.value,
    }
