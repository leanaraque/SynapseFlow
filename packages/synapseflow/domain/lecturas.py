"""Las cuatro acciones de lectura del dominio.

## La regla que gobierna este módulo

`content` es lo que lee el modelo y **ocupa ventana de contexto en cada turno
siguiente**. `artifact` es lo que viaja a la consola y al log de auditoría sin
pasar por el modelo. Un `content` que vuelca el JSON crudo de veinte activos no
es más informativo: es más caro, y desplaza del contexto la parte de la
conversación que importaba.

Por eso cada acción de acá redacta un resumen legible y manda el detalle al
artifact.

## Sobre el legajo del inspector

`inspecciones.inspector_legajo` está clasificado como `restricted` en la
ontología: identifica a una persona. **No aparece en `content`.** La redacción
sistemática llega en F4, con el tokenizador del gateway, pero no hay ninguna
razón para exponerlo antes: al agente le importa el hallazgo, no quién lo firmó.

En `artifact` sí va, porque el artifact no cruza hacia el proveedor.

Ver docs/plan/fases/F2-dominio.md § F2.2
"""

from __future__ import annotations

from typing import Any

from synapseflow.domain.repository import RepositorioDominio
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import ToolResult, implements
from synapseflow.persistence.vectorstore import FirestoreVectorStore
from synapseflow.rag.retrievers import construir_retriever_vigente

# Campos que nunca se escriben en `content`. Salen de la clasificación
# `restricted` de la ontología; se listan acá porque F2 todavía no tiene la
# redacción sistemática de F4 y no hay motivo para exponerlos mientras tanto.
CAMPOS_RESTRINGIDOS = ("inspector_legajo",)

# Cuántos fragmentos de normativa se recuperan por consulta. Cada uno entra
# entero al contexto del modelo.
FRAGMENTOS_POR_CONSULTA = 6


@implements("consultar_activo")
async def consultar_activo(tag: str, *, ctx: ExecutionContext | None = None) -> ToolResult:
    """Ficha técnica de un activo por su TAG."""
    activo = await RepositorioDominio().activo_por_tag(tag)

    if activo is None:
        # Que el TAG no exista es una respuesta, no un error. El modelo tiene que
        # poder decírselo al usuario —probablemente lo tipeó mal— en lugar de
        # recibir una excepción que lo haga reintentar a ciegas.
        return ToolResult(
            content=f"No existe ningún activo con el TAG '{tag}'.",
            artifact={"tag": tag, "encontrado": False},
        )

    return ToolResult(content=_ficha(activo), artifact={"activo": activo, "encontrado": True})


@implements("listar_activos")
async def listar_activos(
    instalacion: str | None = None,
    clase: str | None = None,
    criticidad: str | None = None,
    estado: str | None = None,
    limite: int = 20,
    *,
    ctx: ExecutionContext | None = None,
) -> ToolResult:
    """Activos que cumplen los filtros dados."""
    activos = await RepositorioDominio().listar_activos(
        instalacion=instalacion,
        clase=clase,
        criticidad=criticidad,
        estado=estado,
        limite=limite,
    )

    criterios = {
        "instalacion": instalacion,
        "clase": clase,
        "criticidad": criticidad,
        "estado": estado,
    }
    aplicados = {k: v for k, v in criterios.items() if v is not None}

    if not activos:
        descripcion = ", ".join(f"{k}={v}" for k, v in aplicados.items()) or "sin filtros"
        return ToolResult(
            content=f"Ningún activo cumple los criterios ({descripcion}).",
            artifact={"filtros": aplicados, "cantidad": 0, "activos": []},
        )

    # Una línea por activo: TAG, clase y criticidad alcanzan para que el modelo
    # decida sobre cuál profundizar. El resto está en el artifact.
    lineas = [
        f"- {a.get('tag')} · {a.get('clase', '?')} · criticidad {a.get('criticidad', '?')}"
        f" · {a.get('estado', '?')}"
        for a in activos
    ]
    return ToolResult(
        content=f"{len(activos)} activo(s):\n" + "\n".join(lineas),
        artifact={"filtros": aplicados, "cantidad": len(activos), "activos": activos},
    )


@implements("historial_inspecciones")
async def historial_inspecciones(
    tag: str, limite: int = 10, *, ctx: ExecutionContext | None = None
) -> ToolResult:
    """Inspecciones de un activo, de la más reciente a la más antigua."""
    inspecciones = await RepositorioDominio().inspecciones_de(tag, limite=limite)

    if not inspecciones:
        return ToolResult(
            content=f"El activo '{tag}' no tiene inspecciones registradas.",
            artifact={"tag": tag, "cantidad": 0, "inspecciones": []},
        )

    lineas = []
    for i in inspecciones:
        espesor = i.get("espesor_medido_mm")
        medida = f"{espesor} mm" if espesor is not None else "sin medición"
        hallazgo = i.get("hallazgo") or "sin hallazgos"
        lineas.append(
            f"- {i.get('fecha')} · {medida} · {i.get('tecnica', '?')} · {hallazgo}"
            f" · severidad {i.get('severidad', 'n/d')}"
        )

    return ToolResult(
        # El legajo del inspector NO va acá: es `restricted` en la ontología.
        content=f"{len(inspecciones)} inspección(es) de {tag}, de la más reciente:\n"
        + "\n".join(lineas),
        artifact={"tag": tag, "cantidad": len(inspecciones), "inspecciones": inspecciones},
    )


@implements("buscar_normativa")
async def buscar_normativa(
    consulta: str, tipo_documento: str | None = None, *, ctx: ExecutionContext | None = None
) -> ToolResult:
    """Fragmentos de normativa pertinentes a una consulta.

    Recuperación **híbrida**: semántica y léxica combinadas, porque fallan
    distinto. La vectorial encuentra por significado y no acierta un
    identificador arbitrario como `§7.4`; BM25 es exactamente al revés. Ver
    `synapseflow.rag.retrievers`.

    Dos garantías que este punto sostiene:

    1. Se filtra por `vigencia: vigente` en **las dos ramas**. El corpus incluye
       un procedimiento derogado que contradice al vigente en el criterio de
       aceptación; que aparezca como fundamento sería un error normativo, no un
       resultado de baja calidad.
    2. Cada fragmento vuelve con su documento y su sección, porque una respuesta
       sin cita no se puede auditar. El verificador de fundamento contrasta
       después las citas del agente contra exactamente estos fragmentos.
    """
    filtros: dict[str, Any] = {"vigencia": "vigente"}
    if tipo_documento:
        filtros["tipo_documento"] = tipo_documento

    almacen = FirestoreVectorStore(_gateway().embeddings())
    retriever = construir_retriever_vigente(
        almacen, tipo_documento=tipo_documento, k=FRAGMENTOS_POR_CONSULTA
    )
    documentos = await retriever.ainvoke(consulta)

    if not documentos:
        return ToolResult(
            content=(
                f"No se encontró normativa vigente que responda a «{consulta}». "
                "No hay fundamento documental para responder."
            ),
            artifact={"consulta": consulta, "filtros": filtros, "fragmentos": []},
        )

    fragmentos = [
        {
            "doc_id": d.metadata.get("doc_id"),
            "titulo": d.metadata.get("titulo"),
            "seccion": d.metadata.get("seccion"),
            "contenido": d.page_content,
        }
        for d in documentos
    ]
    bloques = [f"[{f['doc_id']} §{f['seccion']}] {f['contenido']}".strip() for f in fragmentos]

    return ToolResult(
        content="\n\n".join(bloques),
        artifact={"consulta": consulta, "filtros": filtros, "fragmentos": fragmentos},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auxiliares
# ─────────────────────────────────────────────────────────────────────────────


def _ficha(activo: dict[str, Any]) -> str:
    """Resumen de una línea por atributo, solo con lo que decide una respuesta.

    Se eligen los campos y no se vuelca el documento entero: el espesor mínimo
    requerido y la criticidad son los que gobiernan la aptitud para el servicio,
    y son los que el modelo tiene que ver sin buscarlos entre veinte claves.
    """
    campos = (
        ("TAG", activo.get("tag")),
        ("Descripción", activo.get("descripcion")),
        ("Clase", activo.get("clase")),
        ("Instalación", activo.get("instalacion")),
        ("Criticidad", activo.get("criticidad")),
        ("Fluido", activo.get("fluido")),
        ("Estado", activo.get("estado")),
        ("Espesor nominal", _mm(activo.get("espesor_nominal_mm"))),
        ("Espesor mínimo requerido (t_min)", _mm(activo.get("espesor_minimo_requerido_mm"))),
        ("Presión de diseño", _con_unidad(activo.get("presion_diseno_kpa"), "kPa")),
        ("Temperatura de diseño", _con_unidad(activo.get("temperatura_diseno_c"), "°C")),
        ("Puesta en servicio", activo.get("fecha_puesta_servicio")),
    )
    return "\n".join(f"{etiqueta}: {valor}" for etiqueta, valor in campos if valor is not None)


def _mm(valor: Any) -> str | None:
    return _con_unidad(valor, "mm")


def _con_unidad(valor: Any, unidad: str) -> str | None:
    return None if valor is None else f"{valor} {unidad}"


_GATEWAY: Gateway | None = None


def _gateway() -> Gateway:
    """Gateway del proceso, construido en el primer uso.

    Perezoso y no a nivel de módulo porque construirlo verifica credenciales y
    política: importar este módulo no puede exigir una `GOOGLE_API_KEY`. Con la
    verificación en el import, `synapseflow ontology validate` —que no invoca
    ningún modelo— dejaría de correr sin credenciales.
    """
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = Gateway()
    return _GATEWAY


def reset_gateway_cache() -> None:
    """Solo para tests: descarta el gateway memorizado."""
    global _GATEWAY
    _GATEWAY = None
