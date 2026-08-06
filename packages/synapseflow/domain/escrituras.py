"""Las cuatro acciones de escritura del dominio.

## Estas funciones NO implementan el gate de aprobación

Se escriben **como si la aprobación ya hubiera ocurrido**. El freno lo pone el
grafo en F5, con `HumanInTheLoopMiddleware` configurado desde la ontología por
`interrupt_config`. Agregar acá un chequeo de «¿está aprobada?» duplicaría la
lógica en un lugar donde se puede olvidar, y peor: crearía la ilusión de que hay
dos barreras cuando la que vale es una sola, la que se deriva del YAML.

Si `emitir_orden_trabajo` se ejecuta, es porque un supervisor apretó Aprobar.

## Lo que sí verifican

Que haya un `ExecutionContext` con un usuario, y que su rol esté entre los
`allowed_roles` de la acción. Es **defensa en profundidad**, no la barrera
principal —esa es el filtrado del catálogo, que hace que la herramienta ni
siquiera exista para un rol que no puede ejecutarla—. Existe para cuando alguien
invoque la implementación por fuera del catálogo: un script, un test, una API
interna futura.

## Por qué el estado no se borra nunca

Ninguna de estas acciones elimina un documento. `solicitar_parada_equipo` cambia
el estado del activo y deja el anterior en el artifact; `reclasificar_criticidad`
guarda la criticidad previa. Un log de auditoría que registra «se cambió la
criticidad» sin decir desde qué valor no permite reconstruir nada.

Ver docs/plan/fases/F2-dominio.md § F2.4
"""

from __future__ import annotations

import datetime as dt

from synapseflow.domain.contexto import (
    ExecutionContext,
    exigir_contexto,
    exigir_rol_autorizado,
)
from synapseflow.domain.repository import RepositorioDominio
from synapseflow.ontology import ToolResult, implements

# Roles autorizados por acción. Se repiten acá desde el YAML a propósito: el
# test `test_los_roles_declarados_coinciden_con_la_ontologia` compara ambas
# fuentes y falla si divergen, así que la duplicación no puede desincronizarse
# en silencio. La alternativa —leer la ontología en cada llamada— acopla la capa
# de datos al cargador del YAML por un dato que cambia una vez por año.
ROLES_AUTORIZADOS: dict[str, list[str]] = {
    "registrar_borrador_ot": ["tecnico", "inspector", "supervisor_mantenimiento"],
    "emitir_orden_trabajo": ["tecnico", "inspector", "supervisor_mantenimiento"],
    "solicitar_parada_equipo": ["inspector", "supervisor_mantenimiento"],
    "reclasificar_criticidad": ["inspector"],
}


@implements("registrar_borrador_ot")
async def registrar_borrador_ot(
    tag: str,
    tipo: str,
    descripcion_trabajo: str,
    prioridad: str,
    *,
    ctx: ExecutionContext | None = None,
) -> ToolResult:
    """Crea una orden de trabajo en estado borrador.

    Es reversible y no requiere aprobación: no dispara ejecución ni moviliza
    recursos. Emitirla sí, y eso es `emitir_orden_trabajo`.
    """
    contexto = exigir_contexto(ctx, "registrar_borrador_ot")
    exigir_rol_autorizado(
        contexto, "registrar_borrador_ot", ROLES_AUTORIZADOS["registrar_borrador_ot"]
    )

    repo = RepositorioDominio()
    activo = await repo.activo_por_tag(tag)
    if activo is None:
        return ToolResult(
            content=f"No existe ningún activo con el TAG '{tag}'. No se creó el borrador.",
            artifact={"tag": tag, "creado": False},
        )

    id_ot = _nuevo_id_ot(tag)
    orden = {
        "id_ot": id_ot,
        "activo": tag,
        "tipo": tipo,
        "descripcion_trabajo": descripcion_trabajo,
        "prioridad": prioridad,
        "estado": "borrador",
        "solicitante": contexto.usuario,
        "creado_en": _ahora(),
    }
    await repo.guardar_orden(orden)

    return ToolResult(
        content=(
            f"Borrador {id_ot} creado para {tag} ({tipo}, prioridad {prioridad}). "
            "Todavía no está emitido: emitirlo requiere aprobación de un supervisor."
        ),
        artifact={"creado": True, "orden": orden, "contexto": contexto.registro()},
    )


@implements("emitir_orden_trabajo")
async def emitir_orden_trabajo(id_ot: str, *, ctx: ExecutionContext | None = None) -> ToolResult:
    """Emite un borrador a ejecución.

    **Irreversible.** Si esta función corre, es porque el gate ya se aprobó.
    """
    contexto = exigir_contexto(ctx, "emitir_orden_trabajo")
    exigir_rol_autorizado(
        contexto, "emitir_orden_trabajo", ROLES_AUTORIZADOS["emitir_orden_trabajo"]
    )

    repo = RepositorioDominio()
    orden = await repo.orden_por_id(id_ot)
    if orden is None:
        return ToolResult(
            content=f"No existe la orden de trabajo '{id_ot}'. No se emitió nada.",
            artifact={"id_ot": id_ot, "emitida": False},
        )

    if orden.get("estado") != "borrador":
        # Emitir dos veces movilizaría la cuadrilla dos veces. El estado es la
        # defensa contra un reintento del agente tras un timeout.
        return ToolResult(
            content=(
                f"La orden {id_ot} está en estado '{orden.get('estado')}', no en "
                "borrador. No se emitió de nuevo."
            ),
            artifact={"id_ot": id_ot, "emitida": False, "estado": orden.get("estado")},
        )

    cambios = {"estado": "emitida", "emitida_en": _ahora(), "emitida_por": contexto.usuario}
    await repo.actualizar_orden(id_ot, cambios)

    return ToolResult(
        content=(
            f"Orden {id_ot} emitida a ejecución sobre {orden.get('activo')}. "
            "Moviliza cuadrilla y materiales."
        ),
        artifact={
            "emitida": True,
            "id_ot": id_ot,
            "estado_anterior": "borrador",
            "cambios": cambios,
            "contexto": contexto.registro(),
        },
    )


@implements("solicitar_parada_equipo")
async def solicitar_parada_equipo(
    tag: str,
    motivo: str,
    id_inspeccion: str,
    *,
    ctx: ExecutionContext | None = None,
) -> ToolResult:
    """Solicita la parada de un equipo en servicio por riesgo de integridad.

    **Irreversible e impacta producción.** La inspección que la sustenta es
    obligatoria por la ontología, y acá se verifica que exista de verdad: una
    parada apoyada en un id de inspección inventado es exactamente lo que un
    auditor va a buscar primero.
    """
    contexto = exigir_contexto(ctx, "solicitar_parada_equipo")
    exigir_rol_autorizado(
        contexto, "solicitar_parada_equipo", ROLES_AUTORIZADOS["solicitar_parada_equipo"]
    )

    repo = RepositorioDominio()
    activo = await repo.activo_por_tag(tag)
    if activo is None:
        return ToolResult(
            content=f"No existe ningún activo con el TAG '{tag}'. No se solicitó la parada.",
            artifact={"tag": tag, "solicitada": False},
        )

    inspeccion = await repo.inspeccion_por_id(id_inspeccion)
    if inspeccion is None:
        return ToolResult(
            content=(
                f"No existe la inspección '{id_inspeccion}'. Una parada de equipo "
                "necesita un hallazgo trazable que la respalde, así que no se "
                "solicitó nada."
            ),
            artifact={"tag": tag, "solicitada": False, "id_inspeccion": id_inspeccion},
        )

    if inspeccion.get("activo") != tag:
        # Una inspección de otro equipo no fundamenta esta parada. Es el error
        # que más fácil comete un modelo que arrastra ids de turnos anteriores.
        return ToolResult(
            content=(
                f"La inspección '{id_inspeccion}' corresponde al activo "
                f"'{inspeccion.get('activo')}', no a '{tag}'. No fundamenta esta parada."
            ),
            artifact={"tag": tag, "solicitada": False, "inspeccion_de": inspeccion.get("activo")},
        )

    estado_anterior = activo.get("estado")
    cambios = {
        "estado": "parada_solicitada",
        "parada_motivo": motivo,
        "parada_inspeccion": id_inspeccion,
        "parada_solicitada_en": _ahora(),
        "parada_solicitada_por": contexto.usuario,
    }
    await repo.actualizar_activo(tag, cambios)

    return ToolResult(
        content=(
            f"Parada solicitada para {tag}. Motivo: {motivo}. "
            f"Fundamento: inspección {id_inspeccion}. Estado anterior: {estado_anterior}."
        ),
        artifact={
            "solicitada": True,
            "tag": tag,
            "estado_anterior": estado_anterior,
            "cambios": cambios,
            "contexto": contexto.registro(),
        },
    )


@implements("reclasificar_criticidad")
async def reclasificar_criticidad(
    tag: str,
    criticidad_nueva: str,
    justificacion: str,
    *,
    ctx: ExecutionContext | None = None,
) -> ToolResult:
    """Cambia la criticidad RBI de un activo.

    **Irreversible.** Altera el plan de inspección y su frecuencia, con efecto
    sobre el presupuesto de integridad del año.
    """
    contexto = exigir_contexto(ctx, "reclasificar_criticidad")
    exigir_rol_autorizado(
        contexto, "reclasificar_criticidad", ROLES_AUTORIZADOS["reclasificar_criticidad"]
    )

    repo = RepositorioDominio()
    activo = await repo.activo_por_tag(tag)
    if activo is None:
        return ToolResult(
            content=f"No existe ningún activo con el TAG '{tag}'. No se reclasificó nada.",
            artifact={"tag": tag, "reclasificado": False},
        )

    anterior = activo.get("criticidad")
    if anterior == criticidad_nueva:
        return ToolResult(
            content=f"El activo {tag} ya tiene criticidad {criticidad_nueva}. No se cambió nada.",
            artifact={"tag": tag, "reclasificado": False, "criticidad": anterior},
        )

    cambios = {
        "criticidad": criticidad_nueva,
        "criticidad_anterior": anterior,
        "criticidad_justificacion": justificacion,
        "criticidad_cambiada_en": _ahora(),
        "criticidad_cambiada_por": contexto.usuario,
    }
    await repo.actualizar_activo(tag, cambios)

    return ToolResult(
        content=(
            f"Activo {tag} reclasificado de criticidad {anterior} a {criticidad_nueva}. "
            f"Justificación: {justificacion}. Su plan de inspección queda redefinido."
        ),
        artifact={
            "reclasificado": True,
            "tag": tag,
            "criticidad_anterior": anterior,
            "cambios": cambios,
            "contexto": contexto.registro(),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliares
# ─────────────────────────────────────────────────────────────────────────────


def _ahora() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _nuevo_id_ot(tag: str) -> str:
    """Id de orden derivado del TAG y del instante.

    No se usa un UUID porque el id lo lee una persona en la consola y lo dicta
    por radio a una cuadrilla. `OT-P-2101-A-20260806T1432` se puede leer en voz
    alta; un UUID no.
    """
    marca = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
    return f"OT-{tag}-{marca}"


def roles_declarados() -> dict[str, list[str]]:
    """Los roles que este módulo hace cumplir, para contrastarlos con el YAML."""
    return {accion: list(roles) for accion, roles in ROLES_AUTORIZADOS.items()}
