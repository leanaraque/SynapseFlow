"""El supervisor: decide a qué especialista mandar cada consulta.

## Por qué usa el modelo más barato

Es la llamada **más frecuente del sistema**: corre en cada turno y varias veces
por consulta compleja. Usar el modelo caro acá es el error de costos más común en
agentes multi-nodo, y no compra nada: elegir entre tres destinos con el historial
delante es una clasificación corta, no una síntesis.

Por eso pide el perfil `router` y salida estructurada. El costo por consulta del
proyecto se decide en esta línea.

## Por qué no rutea con un `if`

La tentación es obvia: si la pregunta menciona un TAG, mandar a datos; si dice
«norma», a normativa. Funciona con las preguntas que uno imagina y falla con «el
P-2101-A midió 6,8 mm, ¿sigue apto?», que necesita los tres en orden y no lo dice
en ninguna palabra clave.

Lo que sí es determinístico es el **techo**: el supervisor no puede consultar dos
veces al mismo especialista en un turno, y si ya los consultó a todos, va al
verificador. Esas dos reglas se aplican en Python, después de que el modelo
eligió, porque son invariantes del grafo y no juicios.

## El caso de referencia

Para «el P-2101-A midió 6,8 mm, ¿sigue apto?» el orden es datos → cálculo →
normativa → verificador. El prompt lo dice explícitamente: es el recorrido que
atraviesa toda la documentación del proyecto y el que produce el gate de parada.

Ver docs/plan/fases/F5-grafo.md § F5.4
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError

from synapseflow.agents.especialistas import especialistas_disponibles
from synapseflow.agents.state import AgentState
from synapseflow.llm.gateway import Gateway

# Nodo al que se va cuando ya no queda nada que consultar.
NODO_VERIFICADOR = "verificador"

Destino = Literal["datos", "calculo", "normativa", "verificador"]

INSTRUCCION = """Sos el supervisor de un asistente de integridad de activos. Tu \
única tarea es decidir **a qué especialista mandar la consulta ahora**. No \
respondés la pregunta.

Especialistas disponibles:

- `datos`: ficha técnica de un activo, listado de activos, historial de \
inspecciones. Es de dónde salen los espesores medidos y el t_min.
- `calculo`: velocidad de corrosión y vida remanente. **Necesita que `datos` haya \
corrido antes**, porque calcula sobre el historial.
- `normativa`: qué exigen los códigos de inspección. Es de dónde sale el \
fundamento con citas.
- `verificador`: cuando ya hay con qué responder y no falta nadie más.

Reglas:

- Elegí **uno solo**, el que aporte lo que falta ahora.
- Una pregunta sobre si un activo sigue apto para el servicio necesita los tres, \
en este orden: datos → calculo → normativa.
- Si la consulta es solo sobre qué dice una norma, `normativa` alcanza.
- Si ya tenés datos, cálculo y fundamento, andá a `verificador`."""


class Ruteo(BaseModel):
    """Lo que el supervisor decide en cada turno."""

    destino: Destino = Field(description="El especialista que corresponde ahora.")
    motivo: str = Field(
        default="", description="Por qué ese y no otro, en una línea. Queda en el log."
    )


async def nodo_supervisor(estado: AgentState, *, gateway: Gateway | None = None) -> Command[Any]:
    """Elige el próximo especialista, o manda a verificar si ya no falta ninguno.

    Args:
        estado: el estado del grafo. Lee `messages` para decidir y
            `especialistas_consultados` para no repetir.
        gateway: para el perfil `router`. Inyectable en los tests.
    """
    consultados = list(estado.get("especialistas_consultados") or [])
    disponibles = [e for e in especialistas_disponibles() if e not in consultados]

    if not disponibles:
        # Ya se consultó a los tres. Insistir no aporta y el modelo, si se lo
        # deja elegir, tiende a repetir el último que le resultó útil.
        return Command(goto=NODO_VERIFICADOR)

    puerta = gateway or Gateway()
    ruteo = await _elegir(puerta, estado, disponibles)

    if ruteo.destino == NODO_VERIFICADOR:
        return Command(goto=NODO_VERIFICADOR)

    # El modelo puede elegir un especialista ya consultado. Se corrige acá y no
    # en el prompt: un prompt más severo baja la frecuencia del error y no lo
    # elimina, y esto es una invariante del grafo, no una preferencia.
    destino = ruteo.destino if ruteo.destino in disponibles else disponibles[0]

    return Command(
        goto=destino,
        update={"especialistas_consultados": [*consultados, destino]},
    )


async def _elegir(gateway: Gateway, estado: AgentState, disponibles: list[str]) -> Ruteo:
    """Le pide al perfil `router` una decisión estructurada.

    Si la salida no valida contra el esquema, se cae al primer especialista
    pendiente en lugar de propagar el error. **Una decisión de ruteo que falla no
    puede matar el turno**: el usuario perdería la consulta entera por un
    problema de formato del que no tiene culpa ni forma de enterarse, y el
    fallback —consultar al que falta— es exactamente lo que el modelo iba a
    elegir la mayoría de las veces.

    Se captura solo `ValidationError`. Un fallo de red o de credenciales sí tiene
    que subir: ahí no hay recuperación posible y esconderlo produciría un grafo
    que rutea a ciegas sin que nadie lo note.
    """
    modelo = gateway.estructurado("router", Ruteo)

    conversacion = "\n".join(
        f"{getattr(m, 'type', 'mensaje')}: {str(getattr(m, 'content', '') or '')[:500]}"
        for m in (estado.get("messages") or [])
    )

    try:
        resultado = await modelo.ainvoke(
            [
                SystemMessage(content=INSTRUCCION),
                HumanMessage(
                    content=(
                        f"CONVERSACIÓN HASTA AHORA:\n{conversacion}\n\n"
                        f"ESPECIALISTAS QUE TODAVÍA NO SE CONSULTARON: {disponibles}\n"
                        f"YA CONSULTADOS: {estado.get('especialistas_consultados') or []}\n\n"
                        "¿A cuál corresponde mandar la consulta ahora?"
                    )
                ),
            ]
        )
    except ValidationError:
        return Ruteo(
            destino=disponibles[0],  # type: ignore[arg-type]
            motivo="la salida del modelo no validó contra el esquema de ruteo",
        )

    return resultado if isinstance(resultado, Ruteo) else Ruteo.model_validate(resultado)


def destinos_posibles() -> tuple[str, ...]:
    """Nodos a los que el supervisor puede rutear. Lo consume el ensamblado."""
    return (*especialistas_disponibles(), NODO_VERIFICADOR)
