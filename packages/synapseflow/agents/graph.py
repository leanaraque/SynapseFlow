"""El grafo completo. **Cierra el compromiso 2 en ejecución real.**

    supervisor ──► datos ────┐
        ▲   │                │
        │   ├──► calculo ────┤
        │   │                │
        │   ├──► normativa ──┤
        │   │                │
        │   └──► verificador ┴──► acciones ──► END
        │              │
        └──────────────┘  (ciclo: sin fundamento, volver a buscar)

## La línea que cierra el compromiso 2

Está en `construir_middleware`, que arma el `HumanInTheLoopMiddleware` con
`interrupt_config(ontology, rol)`. Esa configuración **se deriva del YAML**: un
desarrollador no puede agregar una acción irreversible y olvidarse del freno,
porque no es él quien lo escribe.

## Por qué un StateGraph y no un solo `create_agent`

`create_agent` produce un agente con un bucle de herramientas, y alcanza para
cada especialista. No alcanza para el conjunto, que necesita dos cosas que ese
bucle no expresa: el **ruteo** entre especialistas y el **ciclo** del verificador
de vuelta a normativa. Por eso el grafo tiene a los agentes como nodos, y el
middleware vive en cada agente y no en la compilación —ver el Hallazgo 3 de las
convenciones: `compile()` no acepta `middleware`—.

## Lo que los nodos extraen del artifact

`recuperados` y `calculos` no salen del texto del modelo: salen del `artifact` de
las herramientas, que no pasa por el LLM. Es la diferencia entre verificar contra
lo que el retriever devolvió y verificar contra lo que el modelo dijo que
devolvió, y entre reportar el número que calculó Python y reportar el número que
el modelo transcribió.

Ver docs/plan/fases/F5-grafo.md § F5.5
"""

from __future__ import annotations

import functools
from typing import Any

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from synapseflow.agents.especialistas import (
    agente_acciones,
    agente_calculo,
    agente_datos,
    agente_normativa,
    especialistas_disponibles,
)
from synapseflow.agents.state import AgentState
from synapseflow.agents.supervisor import NODO_VERIFICADOR, destinos_posibles, nodo_supervisor
from synapseflow.agents.verificador import NODO_EMITIR, nodo_verificador
from synapseflow.governance.pii import Tokenizador
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import Ontology, interrupt_config

# Nodo final: redacta y propone. Es donde se dispara el gate.
#
# El nombre se importa de `verificador`, que es quien rutea hacia acá. Con dos
# constantes independientes bastaba un typo para que el verificador ruteara a un
# nodo inexistente: LangGraph lo ignora con un warning y termina el grafo, así
# que el recorrido no llegaba nunca al gate y nada fallaba ruidosamente.
NODO_ACCIONES = NODO_EMITIR
NODO_SUPERVISOR = "supervisor"

CONSTRUCTORES = {
    "datos": agente_datos,
    "calculo": agente_calculo,
    "normativa": agente_normativa,
}


def construir_grafo(
    ontologia: Ontology,
    ctx: ExecutionContext,
    *,
    gateway: Gateway | None = None,
    settings: Any = None,
    tokenizador: Tokenizador | None = None,
    checkpointer: Any = None,
) -> Any:
    """El grafo compilado para un usuario y su rol.

    Args:
        ontologia: dominio cargado. De acá salen las herramientas y los gates.
        ctx: identidad de quien consulta. **El agente hereda sus permisos**, no
            los de la cuenta de servicio.
        gateway: fábrica de modelos. Inyectable para los tests.
        settings: configuración. Gobierna qué garantías están activas.
        tokenizador: el de la conversación. Uno nuevo por hilo.
        checkpointer: `FirestoreSaver` en producción. Sin él, el gate no
            sobrevive a la muerte del proceso y el human-in-the-loop deja de ser
            asincrónico.
    """
    comun = {"gateway": gateway, "settings": settings, "tokenizador": tokenizador}

    grafo: Any = StateGraph(AgentState)

    grafo.add_node(
        NODO_SUPERVISOR,
        functools.partial(nodo_supervisor, gateway=gateway),
        # Los destinos se declaran porque el nodo devuelve `Command(goto=...)`:
        # sin esto el grafo no sabe qué aristas existen y no se puede dibujar ni
        # verificar estructuralmente, que es justo lo que hace el test de F5.6.
        destinations=destinos_posibles(),
    )

    for nombre in especialistas_disponibles():
        agente = CONSTRUCTORES[nombre](ontologia, ctx, **comun)
        grafo.add_node(nombre, _nodo_de_especialista(nombre, agente))
        # Cada especialista vuelve al supervisor, que decide si falta otro.
        grafo.add_edge(nombre, NODO_SUPERVISOR)

    grafo.add_node(
        NODO_VERIFICADOR,
        functools.partial(nodo_verificador, gateway=gateway),
        destinations=("normativa", NODO_ACCIONES),
    )

    grafo.add_node(
        NODO_ACCIONES,
        _nodo_de_especialista(NODO_ACCIONES, agente_acciones(ontologia, ctx, **comun)),
    )

    grafo.add_edge(START, NODO_SUPERVISOR)
    grafo.add_edge(NODO_ACCIONES, END)

    return grafo.compile(checkpointer=checkpointer)


def _nodo_de_especialista(nombre: str, agente: Any) -> Any:
    """Envuelve un agente compilado como nodo del grafo.

    Devuelve **solo los mensajes nuevos**. El agente devuelve el historial
    entero, y `add_messages` acumula: devolverlo tal cual duplicaría cada mensaje
    en cada paso, y con cuatro nodos eso es un historial cuatro veces más largo
    que la conversación.
    """

    async def nodo(estado: AgentState) -> dict[str, Any]:
        previos = list(estado.get("messages") or [])
        resultado = await agente.ainvoke({"messages": previos})
        nuevos = list(resultado.get("messages") or [])[len(previos) :]

        actualizacion: dict[str, Any] = {"messages": nuevos}
        actualizacion.update(_extraer_del_artifact(nombre, nuevos, estado))
        return actualizacion

    nodo.__name__ = f"nodo_{nombre}"
    return nodo


def _extraer_del_artifact(nombre: str, mensajes: list[Any], estado: AgentState) -> dict[str, Any]:
    """Saca del `artifact` lo que el verificador y la respuesta necesitan.

    **No se lee del texto del modelo.** El artifact es la salida cruda de la
    herramienta y no pasa por el LLM: es la diferencia entre verificar contra lo
    que el retriever devolvió y verificar contra lo que el modelo dijo que
    devolvió.
    """
    if nombre == "normativa":
        fragmentos = [
            Document(
                page_content=str(f.get("contenido") or ""),
                metadata={
                    "doc_id": f.get("doc_id"),
                    "seccion": f.get("seccion"),
                    "titulo": f.get("titulo"),
                    "vigencia": f.get("vigencia", "vigente"),
                },
            )
            for artifact in _artifacts(mensajes)
            for f in artifact.get("fragmentos") or []
        ]
        if fragmentos:
            # Se acumulan entre vueltas del ciclo: la segunda búsqueda agrega
            # fundamento, no reemplaza el de la primera.
            return {"recuperados": [*(estado.get("recuperados") or []), *fragmentos]}
        return {}

    if nombre == "calculo":
        for artifact in _artifacts(mensajes):
            analisis = artifact.get("analisis")
            if analisis:
                return {"calculos": {**(estado.get("calculos") or {}), **analisis}}
        return {}

    return {}


def _artifacts(mensajes: list[Any]) -> list[dict[str, Any]]:
    """Artifacts de los `ToolMessage` de una tanda de mensajes."""
    encontrados: list[dict[str, Any]] = []
    for mensaje in mensajes:
        artifact = getattr(mensaje, "artifact", None)
        if isinstance(artifact, dict):
            encontrados.append(artifact)
    return encontrados


# ─────────────────────────────────────────────────────────────────────────────
# Inspección estructural
# ─────────────────────────────────────────────────────────────────────────────


def gates_del_grafo(ontologia: Ontology, rol: str) -> dict[str, Any]:
    """Los gates que el grafo aplica para un rol, derivados del YAML.

    Se reexporta desde acá para que el test estructural de F5.6 verifique la
    misma fuente que usa el ensamblado, y no una copia que podría divergir.
    """
    return interrupt_config(ontologia, rol)


def nodos_del_grafo() -> tuple[str, ...]:
    """Nombres de todos los nodos, en orden de aparición en el flujo."""
    return (NODO_SUPERVISOR, *especialistas_disponibles(), NODO_VERIFICADOR, NODO_ACCIONES)
