"""Log de auditoría inmutable.

## Qué pregunta responde

No «¿qué pasó?» sino **«¿por qué pasó?»**. Un log que registra «se solicitó la
parada de P-2101-A» documenta el hecho y no permite defenderlo: el auditor
pregunta sobre qué fundamento, con qué datos, quién lo propuso y quién lo aprobó.

Por eso cada evento lleva `thread_id` y `checkpoint_id`. Con esos dos, el
checkpointer de LangGraph puede reconstruir el estado exacto del grafo en el
momento de la propuesta — los mensajes, las herramientas invocadas, los
fragmentos de normativa recuperados. El log no guarda el razonamiento: guarda la
llave para recuperarlo.

## Append-only, y por qué el id es único

Un evento nunca se actualiza ni se borra. El id lleva un componente aleatorio, no
derivado del contenido, y eso es deliberado: con un id determinístico, un
reintento tras un timeout **sobreescribiría** el evento anterior. En una
contabilidad de costos eso era lo correcto —evitaba inflar la factura—; acá es
pérdida de historia.

Un reintento que duplica un evento es ruido detectable: mismo contenido, distinto
id, y la marca temporal lo explica. Un evento sobreescrito no deja rastro de que
existió. **Perder un evento es peor que duplicarlo.**

## Los valores van en claro

El log vive dentro del perímetro, así que guarda el legajo real y no el token.
Redactarlo también acá rompería la trazabilidad sin proteger de nada: quien lee
el log ya tiene acceso a la base. Lo que sí se guarda es el **mapa de
tokenización**, para que un auditor pueda contrastar qué vio el proveedor externo
contra qué había de verdad.

Ver docs/plan/fases/F4-gobernanza.md § F4.3
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from synapseflow.governance.rbac import ExecutionContext
from synapseflow.persistence.client import Collections, get_client

# Firestore admite hasta 500 operaciones por batch.
TOPE_POR_LOTE = 400


class TipoEvento(StrEnum):
    """Qué clase de hecho se está registrando.

    El conjunto es cerrado a propósito: un log donde cada call site inventa su
    propio tipo no se puede consultar, y la consulta es todo el punto.
    """

    ACCION_EJECUTADA = "accion_ejecutada"
    ACCION_PROPUESTA = "accion_propuesta"
    APROBACION_CONCEDIDA = "aprobacion_concedida"
    APROBACION_RECHAZADA = "aprobacion_rechazada"
    RESPUESTA_EMITIDA = "respuesta_emitida"
    RESPUESTA_RECHAZADA = "respuesta_rechazada"
    ACCESO_DENEGADO = "acceso_denegado"


class EventoAuditoria(BaseModel):
    """Un hecho registrado, con la llave para reconstruir su contexto."""

    model_config = ConfigDict(frozen=True)

    # `ts` y `user_id` se llaman así, y no `momento` y `usuario`, porque es como
    # los declaran los índices compuestos de firestore.indexes.json. Un índice
    # sobre un campo que nadie escribe es inútil, y la consulta que lo necesita
    # falla recién en producción.
    ts: dt.datetime
    user_id: str
    rol: str
    tipo: TipoEvento

    # La llave del razonamiento. Sin estos dos, el log dice qué pasó y no permite
    # defenderlo.
    thread_id: str | None = None
    checkpoint_id: str | None = None

    action_id: str | None = None
    argumentos: dict[str, Any] = Field(default_factory=dict)
    resultado: dict[str, Any] = Field(default_factory=dict)
    detalle: str = ""

    # Token → valor real, para contrastar qué vio el proveedor externo contra
    # qué había de verdad.
    tokenizacion: dict[str, str] = Field(default_factory=dict)


async def registrar(evento: EventoAuditoria, *, cliente: Any = None) -> str:
    """Escribe un evento y devuelve su id.

    El id se genera acá y no se acepta del llamador: un call site que reutilice
    un id sobreescribiría historia, y este módulo es el único lugar donde esa
    invariante se puede sostener.
    """
    db = cliente or get_client()
    # El prefijo temporal hace que los ids ordenen cronológicamente en la consola
    # de Firestore, que es donde alguien va a mirar primero cuando algo pasó.
    id_evento = f"{evento.ts.strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}"

    await (
        db.collection(Collections.AUDIT_LOG).document(id_evento).set(evento.model_dump(mode="json"))
    )
    return id_evento


async def registrar_lote(eventos: list[EventoAuditoria], *, cliente: Any = None) -> list[str]:
    """Escribe varios eventos en lote. Devuelve sus ids, en orden.

    Un turno del grafo produce del orden de diez eventos; escribirlos de a uno en
    el camino de la respuesta agrega latencia a lo que el usuario está esperando.
    """
    if not eventos:
        return []

    db = cliente or get_client()
    coleccion = db.collection(Collections.AUDIT_LOG)
    ids: list[str] = []

    for desde in range(0, len(eventos), TOPE_POR_LOTE):
        lote = db.batch()
        for evento in eventos[desde : desde + TOPE_POR_LOTE]:
            id_evento = f"{evento.ts.strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
            lote.set(coleccion.document(id_evento), evento.model_dump(mode="json"))
            ids.append(id_evento)
        await lote.commit()

    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Constructores
#
# Existen para que ningún call site arme un `EventoAuditoria` a mano: el tipo y
# los campos que corresponden a cada clase de hecho se deciden acá una vez, y no
# en cada lugar que registra.
# ─────────────────────────────────────────────────────────────────────────────


def _ahora() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def evento_de_accion(
    ctx: ExecutionContext,
    action_id: str,
    *,
    argumentos: dict[str, Any] | None = None,
    resultado: dict[str, Any] | None = None,
    checkpoint_id: str | None = None,
    propuesta: bool = False,
) -> EventoAuditoria:
    """Una acción que se ejecutó, o que se propuso y quedó esperando aprobación."""
    return EventoAuditoria(
        ts=_ahora(),
        user_id=ctx.usuario,
        rol=ctx.rol,
        tipo=TipoEvento.ACCION_PROPUESTA if propuesta else TipoEvento.ACCION_EJECUTADA,
        thread_id=ctx.thread_id,
        checkpoint_id=checkpoint_id,
        action_id=action_id,
        argumentos=argumentos or {},
        resultado=resultado or {},
    )


def evento_de_aprobacion(
    ctx: ExecutionContext,
    action_id: str,
    *,
    concedida: bool,
    propuesta_por: str | None = None,
    motivo: str = "",
    checkpoint_id: str | None = None,
) -> EventoAuditoria:
    """Una decisión humana sobre un gate.

    `propuesta_por` va en el resultado y no en `user_id`: `user_id` es quien
    decidió. Sin el proponente registrado aparte, no se puede verificar la
    separación de funciones mirando el log, que es donde un auditor la busca.
    """
    return EventoAuditoria(
        ts=_ahora(),
        user_id=ctx.usuario,
        rol=ctx.rol,
        tipo=TipoEvento.APROBACION_CONCEDIDA if concedida else TipoEvento.APROBACION_RECHAZADA,
        thread_id=ctx.thread_id,
        checkpoint_id=checkpoint_id,
        action_id=action_id,
        resultado={"propuesta_por": propuesta_por, "concedida": concedida},
        detalle=motivo,
    )


def evento_de_respuesta(
    ctx: ExecutionContext,
    *,
    emitida: bool,
    veredicto: str,
    citas: list[str] | None = None,
    motivo: str = "",
    checkpoint_id: str | None = None,
    tokenizacion: dict[str, str] | None = None,
) -> EventoAuditoria:
    """Una respuesta que se emitió, o que el verificador de fundamento frenó.

    **Las negativas se registran igual que las emisiones.** Un log que solo
    guarda lo que salió no permite medir cuántas veces el sistema se negó, que es
    una métrica de éxito del proyecto y la primera que un auditor va a pedir.
    """
    return EventoAuditoria(
        ts=_ahora(),
        user_id=ctx.usuario,
        rol=ctx.rol,
        tipo=TipoEvento.RESPUESTA_EMITIDA if emitida else TipoEvento.RESPUESTA_RECHAZADA,
        thread_id=ctx.thread_id,
        checkpoint_id=checkpoint_id,
        resultado={"veredicto": veredicto, "citas": citas or []},
        detalle=motivo,
        tokenizacion=tokenizacion or {},
    )


def evento_de_acceso_denegado(
    ctx: ExecutionContext, action_id: str, *, motivo: str
) -> EventoAuditoria:
    """Un intento que la capa de permisos frenó.

    Se registra porque un patrón de intentos denegados es exactamente la señal
    que un auditor de seguridad busca, y no existe si solo se loguea lo que
    salió bien.
    """
    return EventoAuditoria(
        ts=_ahora(),
        user_id=ctx.usuario,
        rol=ctx.rol,
        tipo=TipoEvento.ACCESO_DENEGADO,
        thread_id=ctx.thread_id,
        action_id=action_id,
        detalle=motivo,
    )
