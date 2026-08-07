"""Los tres agentes especialistas.

Cada uno ve **solo su subconjunto de herramientas**. No es una decisión de
prolijidad: un agente con las nueve herramientas delante elige mal más seguido, y
cada herramienta que ve ocupa espacio en su ventana de contexto en cada turno.

| Agente | Herramientas | Perfil |
|---|---|---|
| `agente_normativa` | `buscar_normativa` | `synthesis` |
| `agente_datos` | `consultar_activo`, `listar_activos`, `historial_inspecciones` | `router` |
| `agente_calculo` | `calcular_vida_remanente` | `router` |

## Las herramientas salen del catálogo compilado, no se instancian a mano

`compile_tools(onto, rol, context=ctx)` produce el catálogo filtrado por rol, con
los gates y la identidad ya inyectados. Armar la lista a mano acá saltearía ese
filtrado, y entonces el mínimo privilegio dependería de que el autor de cada
especialista se acuerde — que es exactamente el diseño que el proyecto rechaza.

Lo que estos módulos hacen es **quedarse con un subconjunto de lo que el rol ya
podía ver**. Un especialista nunca amplía permisos: solo los estrecha.

## Por qué el de cálculo casi no usa el modelo

Su trabajo es invocar la función de Python y presentar el resultado. Si el modelo
empieza a reinterpretar los números, el compromiso 3 se rompe sin que nada falle:
la respuesta sigue sonando técnica. Por eso su prompt es explícito en que
**reporta, no estima**, y por eso usa el perfil barato: no hay nada que razonar.

Ver docs/plan/fases/F5-grafo.md § F5.2
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain_core.tools import BaseTool

from synapseflow.governance.middleware import construir_middleware
from synapseflow.governance.pii import Tokenizador
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import Ontology, compile_tools

if TYPE_CHECKING:  # pragma: no cover - solo para el tipado
    from synapseflow.config import Settings

# Qué herramientas ve cada especialista. Los ids son los de la ontología: si
# alguno se renombrara en el YAML, `_herramientas_de` falla al no encontrarlo, en
# lugar de armar un agente sin herramientas que fallaría en la primera consulta.
HERRAMIENTAS_POR_ESPECIALISTA: dict[str, tuple[str, ...]] = {
    "normativa": ("buscar_normativa",),
    "datos": ("consultar_activo", "listar_activos", "historial_inspecciones"),
    "calculo": ("calcular_vida_remanente",),
}

PROMPT_NORMATIVA = """Sos el especialista en normativa técnica de inspección en \
servicio.

Respondés **solo** con fundamento en los fragmentos que devuelve tu herramienta. \
Reglas que no se negocian:

- Toda afirmación normativa lleva documento y sección, con el formato \
`[DOC-ID §sección]`. Una afirmación sin cita no se emite.
- Si los fragmentos recuperados no responden la pregunta, decilo. No completes \
con conocimiento general: en este dominio una respuesta plausible y sin respaldo \
es peor que «no encontré fundamento».
- No inventes números de sección. Si no aparece en lo que recuperaste, no existe \
para vos."""

PROMPT_DATOS = """Sos el especialista en datos de activos.

Consultás el inventario y el historial de inspecciones, y presentás lo que \
encontrás sin interpretarlo. Reglas:

- No estimes ni proyectes valores. Si el dato no está, decí que no está.
- No saques conclusiones sobre aptitud para el servicio: eso depende de un \
cálculo y de la normativa, que no son tu tarea.
- Si un TAG no existe, es probable que esté mal tipeado: reportalo así."""

PROMPT_CALCULO = """Sos el especialista en cálculo de integridad.

**Vos no calculás: reportás.** El número viene de una función determinística en \
Python que implementa el método de la sección 7 de API 570, y llega ya \
computado. Tu tarea es presentarlo junto con las mediciones que lo sustentan.

Reglas que no se negocian:

- **No recalcules, no redondees, no ajustes.** El número que devuelve la \
herramienta es el que va en la respuesta, con los mismos decimales.
- Si la herramienta informa que el cálculo no se pudo hacer, reportá ese motivo \
tal cual. No lo suplas con una estimación.
- Una vida remanente negativa es un resultado válido y es el caso crítico: \
significa que el activo ya está por debajo de su espesor mínimo requerido."""


def _herramientas_de(
    especialista: str, ontologia: Ontology, ctx: ExecutionContext
) -> list[BaseTool]:
    """Subconjunto del catálogo del rol que le toca a un especialista.

    Raises:
        ValueError: si el rol no puede ver alguna de las herramientas que el
            especialista necesita. Falla al construir el grafo y no en la
            primera consulta, que es donde el usuario lo pagaría.
    """
    pedidas = HERRAMIENTAS_POR_ESPECIALISTA[especialista]
    catalogo = {h.name: h for h in compile_tools(ontologia, ctx.rol, context=ctx)}

    faltantes = [nombre for nombre in pedidas if nombre not in catalogo]
    if faltantes:
        raise ValueError(
            f"el rol '{ctx.rol}' no puede ver {faltantes}, que el especialista "
            f"'{especialista}' necesita.\n"
            f"  Herramientas disponibles para el rol: {sorted(catalogo)}.\n"
            "  Un especialista solo puede estrechar los permisos del rol, nunca "
            "ampliarlos: si el rol no la tiene, no hay agente que se la dé."
        )

    return [catalogo[nombre] for nombre in pedidas]


def _construir(
    especialista: str,
    prompt: str,
    perfil: str,
    ontologia: Ontology,
    ctx: ExecutionContext,
    *,
    gateway: Gateway | None = None,
    settings: Settings | None = None,
    tokenizador: Tokenizador | None = None,
) -> Any:
    """Arma un especialista con su catálogo, su prompt y la gobernanza puesta.

    El pipeline de gobernanza se aplica a **cada** especialista y no una vez al
    grafo entero. Es más verboso y es lo correcto: el middleware envuelve la
    llamada al modelo, y cada especialista hace la suya. Ponerlo solo en el
    supervisor dejaría a los tres llamando al proveedor sin redacción.
    """
    puerta = gateway or Gateway(settings=settings)

    # `create_agent` declara `model: str | BaseChatModel` y `Gateway.chat`
    # devuelve `Runnable`, que es la verdad: con respaldo configurado es un
    # `RunnableWithFallbacks`. Verificado por ejecución que `create_agent` lo
    # acepta igual —tool calling incluido— porque lo resuelve por duck-typing.
    # Ver docs/plan/00-convenciones.md § Hallazgo 7.
    return create_agent(
        puerta.chat(perfil),  # type: ignore[call-overload,arg-type]
        _herramientas_de(especialista, ontologia, ctx),
        system_prompt=prompt,
        middleware=construir_middleware(
            ontologia, ctx.rol, settings=settings, tokenizador=tokenizador
        ),
        name=f"agente_{especialista}",
    )


def agente_normativa(ontologia: Ontology, ctx: ExecutionContext, **extra: Any) -> Any:
    """Recuperación con citas obligatorias. Usa el perfil más capaz.

    Es el único que usa `synthesis`: redactar un fundamento normativo con citas
    correctas es la llamada que define la calidad del sistema, y es donde el
    modelo barato se nota.
    """
    return _construir("normativa", PROMPT_NORMATIVA, "synthesis", ontologia, ctx, **extra)


def agente_datos(ontologia: Ontology, ctx: ExecutionContext, **extra: Any) -> Any:
    """Consulta de activos e inspecciones. Perfil barato: no interpreta."""
    return _construir("datos", PROMPT_DATOS, "router", ontologia, ctx, **extra)


def agente_calculo(ontologia: Ontology, ctx: ExecutionContext, **extra: Any) -> Any:
    """Vida remanente y velocidad de corrosión. **Reporta, no estima.**"""
    return _construir("calculo", PROMPT_CALCULO, "router", ontologia, ctx, **extra)


def especialistas_disponibles() -> tuple[str, ...]:
    """Nombres de los especialistas, para el supervisor y para los tests."""
    return tuple(HERRAMIENTAS_POR_ESPECIALISTA)
