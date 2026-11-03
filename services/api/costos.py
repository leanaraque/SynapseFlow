"""Costo de cada consulta: se registra al pasar y se resume para el panel.

## Lo que faltaba

`ContabilidadDeCosto` existía desde F1 y tenía tests, pero **nadie la conectaba**:
ni el gateway, ni el grafo, ni la API. En producción `llm_usage` tenía cero
documentos, y el detector de estado la daba por hecha porque comprobaba que la
clase existiera, no que se usara.

Ahora la API crea una por request, la pasa como callback del recorrido —LangChain
la propaga a cada llamada al modelo, en el supervisor y en cada especialista— y
vuelca las filas cuando el flujo termina.

## Por qué el volcado va al final y en un `finally`

Escribir una fila por llamada en el camino de la respuesta agregaría latencia a lo
que el usuario está esperando. Se vuelca al cerrar el flujo, y en un `finally`
para que una consulta que el usuario corta a mitad también deje su costo: el
modelo ya cobró esas llamadas aunque nadie leyera la respuesta.

## Quién ve los costos

Las filas dicen cuánto consulta cada persona, y eso es información de uso
interno: se exige el nivel `confidential` de la ontología. No hay una lista de
roles escrita acá; si mañana el YAML sube o baja el nivel de un rol, el panel lo
sigue.

## Lo que no se cuenta

Los embeddings de la búsqueda de normativa no disparan callbacks de chat en
LangChain, así que no aparecen. Es la parte más barata de una consulta —un
vector por pregunta—, pero el total del panel es una cota inferior y lo dice.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from services.api.streaming import Evento
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.callbacks import ContabilidadDeCosto
from synapseflow.ontology import Ontology, get_ontology
from synapseflow.persistence.client import Collections, get_client

# Nivel de la ontología que hace falta para ver el panel.
CLASIFICACION_DE_COSTOS = "confidential"

# Tope de filas por resumen. Una consulta completa son del orden de diez
# llamadas, así que alcanza para cientos de conversaciones. Pasado el tope, el
# resumen lo avisa en lugar de sumar una parte como si fuera el total.
TOPE_DE_FILAS = 5000

DIAS_POR_DEFECTO = 30


class SinAccesoACostosError(PermissionError):
    """El rol no alcanza la clasificación que exige el panel.

    Hereda de `PermissionError` para salir como 403 por el manejador que ya
    existe, sin que este módulo sepa de HTTP.
    """


async def contabilizar(
    fuente: AsyncIterator[Evento],
    contabilidad: ContabilidadDeCosto,
    *,
    cliente: Any = None,
) -> AsyncIterator[Evento]:
    """Deja pasar el flujo intacto y vuelca el costo cuando termina.

    Si el volcado falla, el flujo no se entera: perder una fila del panel es
    preferible a cortar una respuesta por un problema de instrumentación.
    """
    try:
        async for evento in fuente:
            yield evento
    finally:
        with contextlib.suppress(Exception):
            await contabilidad.volcar(cliente=cliente)


def exigir_acceso(ctx: ExecutionContext, ontologia: Ontology | None = None) -> None:
    """Raises: SinAccesoACostosError si el rol no alcanza el nivel requerido."""
    onto = ontologia or get_ontology()
    requerido = onto.classification_rank(CLASIFICACION_DE_COSTOS)

    if onto.max_classification_for_role(ctx.rol) < requerido:
        raise SinAccesoACostosError(
            f"el panel de costos muestra el consumo de cada persona y exige el nivel "
            f"'{CLASIFICACION_DE_COSTOS}'. El rol '{ctx.rol}' llega hasta "
            f"'{onto.role(ctx.rol).max_classification}'."
        )


async def resumen(
    ctx: ExecutionContext,
    *,
    dias: int = DIAS_POR_DEFECTO,
    cliente: Any = None,
    ontologia: Ontology | None = None,
    ahora: dt.datetime | None = None,
) -> dict[str, Any]:
    """Costo de los últimos `dias`, agrupado como lo necesita el panel.

    Se agrega en Python y no con agregaciones de Firestore: hacen falta seis
    agrupaciones distintas sobre las mismas filas, y seis consultas de agregación
    costarían más lecturas que traer las filas una vez.
    """
    exigir_acceso(ctx, ontologia)
    db = cliente or get_client()

    hasta = ahora or dt.datetime.now(dt.UTC)
    desde = hasta - dt.timedelta(days=dias)

    consulta = (
        db.collection(Collections.USAGE)
        # `momento` se guarda como ISO-8601 en UTC, que ordena igual como texto
        # que como fecha: el filtro de rango funciona sin convertir.
        .where(filter=FieldFilter("momento", ">=", desde.isoformat()))
        .limit(TOPE_DE_FILAS + 1)
    )
    filas = [documento.to_dict() or {} async for documento in consulta.stream()]

    truncado = len(filas) > TOPE_DE_FILAS
    filas = filas[:TOPE_DE_FILAS]

    return {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "dias": dias,
        "truncado": truncado,
        "totales": _totales(filas),
        "por_perfil": _agrupar(filas, "perfil"),
        "por_modelo": _agrupar(filas, "modelo"),
        "por_usuario": _agrupar(filas, "usuario"),
        "por_dia": sorted(
            _agrupar(filas, "momento", clave=lambda v: str(v)[:10]),
            key=lambda g: g["clave"],
        ),
        "nota": (
            "Cota inferior: los embeddings de la búsqueda de normativa no se cuentan. "
            "Las llamadas a modelos fuera del catálogo figuran con costo cero y se "
            "cuentan aparte."
        ),
    }


def _totales(filas: list[dict[str, Any]]) -> dict[str, Any]:
    conversaciones = {f.get("thread_id") for f in filas if f.get("thread_id")}
    costo = sum(float(f.get("costo_usd") or 0) for f in filas)
    latencias = sorted(int(f.get("latencia_ms") or 0) for f in filas)

    return {
        "costo_usd": round(costo, 6),
        "llamadas": len(filas),
        "tokens_entrada": sum(int(f.get("tokens_entrada") or 0) for f in filas),
        "tokens_salida": sum(int(f.get("tokens_salida") or 0) for f in filas),
        "conversaciones": len(conversaciones),
        # Es la métrica que pide F8: cuánto cuesta responder una consulta.
        "costo_por_conversacion_usd": (
            round(costo / len(conversaciones), 6) if conversaciones else 0.0
        ),
        "latencia_p95_ms": latencias[int(0.95 * (len(latencias) - 1))] if latencias else 0,
        "llamadas_sin_precio": sum(1 for f in filas if f.get("modelo_no_catalogado")),
    }


def _agrupar(filas: list[dict[str, Any]], campo: str, clave: Any = None) -> list[dict[str, Any]]:
    """Costo, llamadas y tokens por valor de `campo`, de mayor a menor costo."""
    grupos: dict[str, dict[str, Any]] = {}
    for fila in filas:
        valor = fila.get(campo)
        nombre = clave(valor) if clave else str(valor or "desconocido")
        grupo = grupos.setdefault(
            nombre, {"clave": nombre, "costo_usd": 0.0, "llamadas": 0, "tokens": 0}
        )
        grupo["costo_usd"] += float(fila.get("costo_usd") or 0)
        grupo["llamadas"] += 1
        grupo["tokens"] += int(fila.get("tokens_entrada") or 0) + int(
            fila.get("tokens_salida") or 0
        )

    for grupo in grupos.values():
        grupo["costo_usd"] = round(grupo["costo_usd"], 6)
    return sorted(grupos.values(), key=lambda g: g["costo_usd"], reverse=True)
