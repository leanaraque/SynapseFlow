"""El verificador como nodo del grafo: lo último antes de emitir.

Envuelve el `VerificadorDeFundamento` de F3.4 y lo convierte en una decisión de
control de flujo. Corre **después** de que se redactó la respuesta y **antes** de
que salga.

## El ciclo, y por qué el proyecto usa un motor de grafos

Cuando el veredicto es `sin_fundamento`, el nodo no corta: manda de vuelta al
agente de normativa a recuperar más contexto. **Eso es un ciclo**, y es una de
las dos razones por las que este proyecto usa LangGraph y no una cadena lineal
—la otra es el `interrupt()` del gate—.

Con una cadena, «volver a buscar» habría que expresarlo como un bucle en Python
alrededor de la cadena entera, y el estado intermedio quedaría fuera del
checkpoint: un corte de proceso en la segunda vuelta perdería la primera.

El ciclo tiene techo. Dos vueltas sin fundamento significan que la respuesta no
está en el corpus, y una tercera gasta cuota para llegar a la misma conclusión.
El contador vive en el estado —no en una variable del nodo— porque el ciclo puede
atravesar un checkpoint; ver `agents.state`.

## Qué devuelve

Un `Command` con `goto` y `update`, no un dict. Es lo que permite que la decisión
de flujo y la actualización del estado viajen juntas: con un dict, el grafo
necesitaría una arista condicional aparte que volviera a leer el veredicto para
decidir lo mismo que este nodo ya decidió.

Ver docs/plan/fases/F5-grafo.md § F5.3
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.documents import Document
from langgraph.types import Command

from synapseflow.agents.state import AgentState, puede_reintentar
from synapseflow.llm.gateway import Gateway
from synapseflow.rag.fundamento import Resultado, Veredicto, VerificadorDeFundamento

# Nombres de los nodos a los que este puede rutear.
#
# Viven acá y `graph.py` los importa, en lugar de que cada módulo declare el
# suyo. Con dos constantes, el verificador ruteaba a «emitir» y el nodo del
# grafo se llamaba «acciones»: **LangGraph ignora un destino desconocido con un
# warning y termina el grafo**. El síntoma era un recorrido que nunca llegaba al
# gate, sin ninguna excepción que lo delatara.
NODO_NORMATIVA = "normativa"
NODO_EMITIR = "acciones"

# Destinos posibles del nodo. `normativa` es el ciclo; el otro sigue al final.
Destino = Literal["normativa", "acciones"]


async def nodo_verificador(
    estado: AgentState,
    *,
    gateway: Gateway | None = None,
    verificador: VerificadorDeFundamento | None = None,
) -> Command[Any]:
    """Decide si la respuesta redactada se emite, se marca o se vuelve a buscar.

    Args:
        estado: el estado del grafo. Lee `messages` —de donde sale la respuesta
            redactada— y `recuperados`, que son los fragmentos contra los que se
            verifica.
        gateway: para construir el verificador si no se inyecta uno.
        verificador: inyectable para los tests, que programan el dictamen.
    """
    respuesta = _ultima_respuesta(estado)
    recuperados = list(estado.get("recuperados") or [])

    if not respuesta:
        # No hay nada redactado que verificar. Pasa a emitir en lugar de fallar:
        # un turno que solo invocó herramientas es legítimo y no afirma nada.
        return Command(goto=NODO_EMITIR, update={"veredicto": None})

    juez = verificador or VerificadorDeFundamento(gateway)
    veredicto = await juez.verificar(respuesta, recuperados)

    if veredicto.resultado is Resultado.SIN_FUNDAMENTO and puede_reintentar(estado):
        # El ciclo. Se vuelve al agente de normativa con el motivo, para que la
        # próxima búsqueda no repita la anterior.
        return Command(
            goto=NODO_NORMATIVA,
            update={
                "veredicto": veredicto.resultado.value,
                "reintentos": estado.get("reintentos", 0) + 1,
                "messages": [_pedido_de_mas_contexto(veredicto)],
            },
        )

    return Command(
        goto=NODO_EMITIR,
        update={
            "veredicto": veredicto.resultado.value,
            "citas": [
                {"doc_id": c.doc_id, "seccion": c.seccion} for c in veredicto.citas.respaldadas
            ],
            # El texto que corresponde emitir ya lo decidió el verificador: con
            # `sin_fundamento` es la negativa, no la respuesta original. Que el
            # nodo lo copie sin elegir es deliberado — dos lugares decidiendo qué
            # se emite es un lugar de más.
            "messages": [_mensaje_final(veredicto)],
        },
    )


def _ultima_respuesta(estado: AgentState) -> str:
    """El último texto que el modelo redactó, sin contar invocaciones vacías.

    Un `AIMessage` que solo lleva `tool_calls` tiene `content` vacío: no afirma
    nada, así que no hay nada que verificar en él.
    """
    for mensaje in reversed(estado.get("messages") or []):
        if getattr(mensaje, "type", None) != "ai":
            continue
        contenido = str(getattr(mensaje, "content", "") or "").strip()
        if contenido:
            return contenido
    return ""


def _pedido_de_mas_contexto(veredicto: Veredicto) -> dict[str, str]:
    """Instrucción para la vuelta al agente de normativa.

    Lleva el motivo del rechazo. Sin él, la segunda búsqueda repetiría la
    primera: el agente no tendría cómo saber qué le faltó.
    """
    faltantes = ", ".join(a.texto for a in veredicto.sin_respaldo) or "la respuesta completa"
    return {
        "role": "user",
        "content": (
            "La respuesta anterior no se pudo emitir porque no tiene respaldo en "
            f"la normativa recuperada. Sin fundamento: {faltantes}.\n"
            f"Motivo: {veredicto.explicacion}\n"
            "Buscá con otros términos, o respondé que no hay fundamento documental."
        ),
    }


def _mensaje_final(veredicto: Veredicto) -> dict[str, str]:
    return {"role": "assistant", "content": veredicto.texto_emitible}


def documentos_recuperados(estado: AgentState) -> list[Document]:
    """Los fragmentos contra los que se verifica.

    Existe como función y no como acceso directo para dejar en un solo lugar la
    decisión de que se usan **los que están en el estado** y no una recuperación
    nueva. Si el verificador buscara de nuevo, estaría contrastando contra otro
    conjunto y la garantía del compromiso 4 dejaría de valer.
    """
    return list(estado.get("recuperados") or [])
