"""La bandeja de aprobaciones y la decisión que reanuda el grafo.

## Lo aprobado es lo ejecutado

Es la garantía central de este módulo y sale de cómo funciona `Command(resume=)`:
al aprobar **no se mandan argumentos**. El grafo retoma desde su checkpoint con
la llamada a herramienta que ya tenía, así que no hay forma de que lo ejecutado
difiera de lo propuesto — no porque se valide, sino porque no hay ningún lugar
donde meter otros argumentos.

`editar` es la excepción explícita, y por eso se audita como tal: cambia lo que
se va a ejecutar y quien lo cambió queda registrado.

## Las dos validaciones, y por qué ninguna alcanza sola

1. **El rol está entre los `approver_roles` de la acción**, según la ontología.
2. **El aprobador no es el proponente.** Separación de funciones.

La primera sin la segunda deja que un supervisor proponga una parada y la apruebe
él mismo, produciendo el mismo registro de auditoría que uno que la aprobó sin
leerla. La segunda sin la primera deja que cualquiera apruebe con tal de no haber
sido quien propuso.

Las dos las aplica `governance.rbac`, no este módulo: la API decide **cuándo**
preguntar, no **qué** responder.

## Por qué hay una colección y no se derivan del checkpointer

Un pendiente hay que poder **listarlo sin saber el hilo**: la bandeja de un
supervisor son las propuestas de otros. Derivarlas del checkpointer obligaría a
recorrer todos los checkpoints de todos los hilos, y no hay índice que lo
sostenga. `approvals` y su índice `estado` + `creado_en` ya estaban declarados
desde F0 justamente para esto.

El documento es un **puntero**, no una copia: la fuente de verdad de lo que se va
a ejecutar sigue siendo el checkpoint. Si los dos discreparan, gana el
checkpoint, porque es lo que el grafo va a correr.

Ver docs/plan/fases/F6-api.md § F6.3
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Mapping
from enum import StrEnum
from typing import Any

from fastapi import HTTPException, status
from google.cloud.firestore_v1.base_query import FieldFilter
from langgraph.types import Command
from pydantic import BaseModel, Field

from services.api.streaming import APROBACION_REQUERIDA, Evento
from synapseflow.governance import auditoria
from synapseflow.governance.rbac import (
    AutoridadInsuficienteError,
    ExecutionContext,
    exigir_autoridad_de_aprobacion,
)
from synapseflow.ontology import Ontology, get_ontology
from synapseflow.persistence.client import Collections, get_client

PENDIENTE = "pendiente"

# Tope de la bandeja. La consulta trae los pendientes más recientes y la
# autoridad se filtra después, así que sin tope una bandeja vacía costaría
# recorrer la colección entera.
LIMITE_DE_BANDEJA = 100


class Decision(StrEnum):
    """Lo que una persona puede decidir sobre un gate.

    Los nombres son los del dominio, en español, y se traducen a los que espera
    `HumanInTheLoopMiddleware` en un solo lugar: la consola no tiene por qué
    hablar el vocabulario de la librería.
    """

    APROBAR = "aprobar"
    RECHAZAR = "rechazar"
    EDITAR = "editar"


# `aprobar` no aparece: es el caso que no lleva argumentos, y esa ausencia es la
# garantía. Ver el encabezado del módulo.
_TIPOS = {Decision.APROBAR: "approve", Decision.RECHAZAR: "reject", Decision.EDITAR: "edit"}


class PedidoDeDecision(BaseModel):
    """Lo que manda la consola para resolver un gate."""

    decision: Decision
    # Solo para `editar`. En `aprobar` se ignora deliberadamente: aceptarlo
    # abriría exactamente el agujero que este módulo existe para cerrar.
    argumentos: dict[str, Any] = Field(default_factory=dict)
    motivo: str = ""


class ErrorDeAprobacion(HTTPException):
    """No hay nada que aprobar, o no se puede aprobar así."""


# ─────────────────────────────────────────────────────────────────────────────
# La bandeja
# ─────────────────────────────────────────────────────────────────────────────


async def registrar_pendiente(
    ctx: ExecutionContext,
    accion: Mapping[str, Any],
    *,
    checkpoint_id: str | None = None,
    cliente: Any = None,
    ontologia: Ontology | None = None,
) -> str:
    """Deja escrito que un gate quedó esperando a alguien.

    El id del documento es el `thread_id`: un hilo tiene a lo sumo un gate
    abierto, y usar el hilo como id hace que reanudar dos veces la misma
    propuesta sobreescriba en lugar de duplicar la bandeja.

    Los `aprobadores` se copian de la ontología al crear el pendiente. Es
    información derivada, y se guarda igual para que la consola pueda decir «esto
    lo aprueba un supervisor» sin cargar el dominio.
    """
    onto = ontologia or get_ontology()
    db = cliente or get_client()

    nombre = str(accion.get("herramienta") or "")
    declarada = _accion_de_la_ontologia(onto, nombre)

    documento = {
        "thread_id": ctx.thread_id,
        "checkpoint_id": checkpoint_id,
        "action_id": declarada.id if declarada else nombre,
        "herramienta": nombre,
        "argumentos": dict(accion.get("argumentos") or {}),
        "descripcion": str(accion.get("descripcion") or ""),
        "decisiones": list(accion.get("decisiones") or []),
        "propuesta_por": ctx.usuario,
        "rol_proponente": ctx.rol,
        "aprobadores": sorted(declarada.approver_roles) if declarada else [],
        "estado": PENDIENTE,
        "creado_en": dt.datetime.now(dt.UTC).isoformat(),
    }

    await db.collection(Collections.APPROVALS).document(str(ctx.thread_id)).set(documento)
    return str(ctx.thread_id)


async def registrar_al_pasar(
    ctx: ExecutionContext,
    grafo: Any,
    fuente: AsyncIterator[Evento],
    *,
    cliente: Any = None,
    ontologia: Ontology | None = None,
) -> AsyncIterator[Evento]:
    """Deja el flujo intacto y anota los gates que aparecen.

    Va acá y no dentro de `streaming.eventos()` porque aquello traduce y no
    persiste: mezclarlos haría que un test de traducción necesitara una base.

    Si registrar falla, el flujo **no** se cae. El usuario ya vio la propuesta y
    el checkpoint ya está guardado; perder la fila de la bandeja es un problema
    de comodidad, y romper la respuesta por eso sería un problema de verdad. El
    fallo se anuncia como un `error` propio para que no pase inadvertido.
    """
    async for evento in fuente:
        yield evento

        if evento.tipo != APROBACION_REQUERIDA:
            continue

        try:
            checkpoint_id = await _checkpoint_actual(grafo, ctx.thread_id)
            for accion in evento.datos.get("acciones") or []:
                await registrar_pendiente(
                    ctx,
                    accion,
                    checkpoint_id=checkpoint_id,
                    cliente=cliente,
                    ontologia=ontologia,
                )
        except Exception as exc:
            yield Evento(
                tipo="error",
                datos={
                    "error": f"la propuesta quedó sin registrar en la bandeja: {exc}",
                    "clase": type(exc).__name__,
                },
            )


async def pendientes(
    ctx: ExecutionContext,
    *,
    cliente: Any = None,
    ontologia: Ontology | None = None,
    limite: int = LIMITE_DE_BANDEJA,
) -> list[dict[str, Any]]:
    """Los gates que **este** usuario puede resolver.

    El filtro por autoridad se aplica en Python y no en Firestore, y es
    deliberado: lo aplica `exigir_autoridad_de_aprobacion`, el mismo código que
    va a decidir cuando llegue el POST. Una consulta que filtrara por rol sería
    una segunda regla de autoridad, y el día que divergieran la bandeja
    ofrecería algo que el POST rechaza.
    """
    onto = ontologia or get_ontology()
    db = cliente or get_client()

    consulta = (
        db.collection(Collections.APPROVALS)
        .where(filter=FieldFilter("estado", "==", PENDIENTE))
        .order_by("creado_en", direction="DESCENDING")
        .limit(max(1, min(limite, LIMITE_DE_BANDEJA)))
    )

    visibles: list[dict[str, Any]] = []
    async for documento in consulta.stream():
        fila = documento.to_dict() or {}
        if _puede_decidir(ctx, fila, onto):
            visibles.append(fila)

    return visibles


# ─────────────────────────────────────────────────────────────────────────────
# La decisión
# ─────────────────────────────────────────────────────────────────────────────


async def decidir(
    ctx: ExecutionContext,
    thread_id: str,
    pedido: PedidoDeDecision,
    *,
    cliente: Any = None,
    ontologia: Ontology | None = None,
) -> tuple[Command, dict[str, Any]]:
    """Valida la decisión y devuelve con qué reanudar el grafo.

    Devuelve el `Command` y el pendiente, en lugar de reanudar acá, porque el
    grafo depende del rol de quien consulta y armarlo es responsabilidad del
    endpoint. Este módulo decide si **se puede**, no ejecuta.

    Raises:
        ErrorDeAprobacion: 404 si no hay gate abierto en ese hilo, 409 si ya se
            resolvió, 400 si la decisión no está permitida para esa acción.
        AutoridadInsuficienteError: 403, con el motivo distinguido.
    """
    onto = ontologia or get_ontology()
    db = cliente or get_client()

    referencia = db.collection(Collections.APPROVALS).document(thread_id)
    documento = await referencia.get()
    if not documento.exists:
        raise ErrorDeAprobacion(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no hay ninguna propuesta registrada para el hilo '{thread_id}'.",
        )

    pendiente = documento.to_dict() or {}
    if pendiente.get("estado") != PENDIENTE:
        # 409 y no 403: el problema no es quién sos, es que llegaste tarde. Dos
        # supervisores mirando la misma bandeja es el caso normal, no el raro.
        raise ErrorDeAprobacion(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"la propuesta del hilo '{thread_id}' ya está "
                f"'{pendiente.get('estado')}'. La resolvió "
                f"'{pendiente.get('decidida_por') or 'alguien'}'."
            ),
        )

    accion = _accion_de_la_ontologia(onto, str(pendiente.get("action_id") or ""))
    if accion is None:
        raise ErrorDeAprobacion(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"la acción '{pendiente.get('action_id')}' ya no existe en el "
                "dominio. La propuesta quedó huérfana de un cambio del YAML y no "
                "se puede aprobar contra reglas que ya no rigen."
            ),
        )

    # Las dos validaciones. Las aplica la gobernanza, no la API.
    exigir_autoridad_de_aprobacion(
        ctx, accion, propuesta_por=str(pendiente.get("propuesta_por") or "")
    )

    permitidas = pendiente.get("decisiones") or []
    tipo = _TIPOS[pedido.decision]
    if permitidas and tipo not in permitidas:
        raise ErrorDeAprobacion(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{pedido.decision}' no está permitida para "
                f"'{accion.id}'. Permitidas: {sorted(permitidas)}."
            ),
        )

    if pedido.decision is Decision.EDITAR and not pedido.argumentos:
        raise ErrorDeAprobacion(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="editar sin argumentos nuevos es aprobar, y se registra distinto.",
        )

    comando = _a_comando(pedido, pendiente)
    await _resolver(referencia, ctx, pedido, pendiente, accion.id, cliente=db)

    return comando, pendiente


def _a_comando(pedido: PedidoDeDecision, pendiente: Mapping[str, Any]) -> Command:
    """La decisión, en el vocabulario que espera el middleware.

    **En `aprobar` no viaja ningún argumento**, y eso es la garantía de que lo
    ejecutado es lo propuesto: el grafo retoma la llamada que ya tenía en su
    checkpoint. No hay validación que lo asegure porque no hace falta ninguna.
    """
    tipo = _TIPOS[pedido.decision]

    if pedido.decision is Decision.EDITAR:
        decision: dict[str, Any] = {
            "type": tipo,
            "edited_action": {
                "name": str(pendiente.get("herramienta") or ""),
                "args": dict(pedido.argumentos),
            },
        }
    elif pedido.decision is Decision.RECHAZAR:
        decision = {"type": tipo}
        if pedido.motivo:
            # El motivo vuelve al modelo: sin él, el agente puede reintentar la
            # misma acción sin entender por qué se la negaron.
            decision["message"] = pedido.motivo
    else:
        decision = {"type": tipo}

    return Command(resume={"decisions": [decision]})


async def _resolver(
    referencia: Any,
    ctx: ExecutionContext,
    pedido: PedidoDeDecision,
    pendiente: Mapping[str, Any],
    action_id: str,
    *,
    cliente: Any = None,
) -> None:
    """Cierra el pendiente y lo registra en el log de auditoría.

    El log se escribe **antes** de reanudar el grafo. Si se escribiera después,
    una caída entre la ejecución y el registro dejaría una acción irreversible
    ejecutada y sin rastro de quién la aprobó — que es exactamente lo que este
    proyecto existe para que no pase. Al revés, el peor caso es un registro de
    una aprobación que no llegó a ejecutarse, y eso el `thread_id` lo explica.
    """
    concedida = pedido.decision is not Decision.RECHAZAR

    await auditoria.registrar(
        auditoria.evento_de_aprobacion(
            ctx,
            action_id,
            concedida=concedida,
            propuesta_por=str(pendiente.get("propuesta_por") or ""),
            motivo=pedido.motivo or ("argumentos editados" if pedido.argumentos else ""),
            checkpoint_id=pendiente.get("checkpoint_id"),
        ),
        cliente=cliente,
    )

    await referencia.update(
        {
            "estado": str(pedido.decision),
            "decidida_por": ctx.usuario,
            "rol_decisor": ctx.rol,
            "decidida_en": dt.datetime.now(dt.UTC).isoformat(),
            "motivo": pedido.motivo,
            # Lo que se ejecuta cuando se edita. Vacío en aprobar y rechazar, que
            # es lo que hace legible el log: si hay algo acá, alguien cambió la
            # propuesta.
            "argumentos_finales": dict(pedido.argumentos) if pedido.argumentos else {},
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Detalles
# ─────────────────────────────────────────────────────────────────────────────


def _puede_decidir(ctx: ExecutionContext, fila: Mapping[str, Any], onto: Ontology) -> bool:
    """Si este usuario puede resolver este pendiente, según la gobernanza."""
    accion = _accion_de_la_ontologia(onto, str(fila.get("action_id") or ""))
    if accion is None:
        return False

    try:
        exigir_autoridad_de_aprobacion(
            ctx, accion, propuesta_por=str(fila.get("propuesta_por") or "")
        )
    except AutoridadInsuficienteError:
        return False
    return True


def _accion_de_la_ontologia(onto: Ontology, identificador: str) -> Any:
    """La acción declarada, buscada por id o por nombre de herramienta.

    Se acepta el nombre de herramienta porque es lo que trae el gate: el
    middleware habla de herramientas y la ontología, de acciones.
    """
    if not identificador:
        return None

    try:
        return onto.action(identificador)
    except KeyError:
        return next((a for a in onto.actions if a.tool_name == identificador), None)


async def _checkpoint_actual(grafo: Any, thread_id: str | None) -> str | None:
    """El checkpoint donde quedó frenado el grafo.

    Es la mitad de la llave que el log de auditoría necesita para reconstruir el
    razonamiento: con `thread_id` y `checkpoint_id` se recupera el estado exacto
    en el momento de la propuesta.
    """
    if thread_id is None:
        return None

    instantanea = await grafo.aget_state({"configurable": {"thread_id": thread_id}})
    configurable = (instantanea.config or {}).get("configurable") or {}
    valor = configurable.get("checkpoint_id")
    return str(valor) if valor is not None else None
