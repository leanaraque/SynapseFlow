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
# El orden importa: es el que usa el supervisor cuando tiene que caer a un
# destino por defecto, y coincide con el del caso de referencia. Caer en
# `normativa` antes que en `datos` produciría una búsqueda de fundamento sobre
# una pregunta cuyos datos todavía no se leyeron.
HERRAMIENTAS_POR_ESPECIALISTA: dict[str, tuple[str, ...]] = {
    "datos": ("consultar_activo", "listar_activos", "historial_inspecciones"),
    "calculo": ("calcular_vida_remanente",),
    "normativa": ("buscar_normativa",),
}

# El cuarto agente, que el plan de F5.2 no listaba.
#
# Los tres especialistas de arriba son de lectura, y el recorrido de referencia
# termina proponiendo `solicitar_parada_equipo`: sin un agente que tenga las
# acciones de escritura, no hay quién dispare el gate y el recorrido no llega a
# donde el proyecto promete que llega.
#
# Va aparte de `HERRAMIENTAS_POR_ESPECIALISTA` porque **no es un destino del
# supervisor**: no se elige entre él y los otros tres. Corre siempre al final,
# con todo lo que los especialistas juntaron, y es donde el gate se dispara.
HERRAMIENTAS_DE_ACCION: tuple[str, ...] = (
    "registrar_borrador_ot",
    "emitir_orden_trabajo",
    "solicitar_parada_equipo",
    "reclasificar_criticidad",
)

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

PROMPT_ACCIONES = """Sos quien redacta la respuesta final y, si corresponde, \
propone una acción sobre el activo.

Tenés delante lo que los especialistas reunieron: la ficha del activo, el cálculo \
de vida remanente y el fundamento normativo con sus citas. Con eso:

1. **Redactá la respuesta.** Toda afirmación normativa lleva su cita con el \
formato `[DOC-ID §sección]`. Los números del cálculo van tal como llegaron.
2. **Si corresponde una acción, proponela.** No la ejecutes por tu cuenta: las \
acciones irreversibles se frenan y las aprueba una persona. Vos las proponés con \
fundamento.

Cuándo corresponde proponer una parada de equipo: cuando el espesor medido está \
por debajo del mínimo requerido, o la vida remanente es negativa. Ese caso no \
admite esperar a la próxima campaña.

Nunca propongas una acción sin la inspección que la respalda: una parada apoyada \
en un identificador inventado es lo primero que va a mirar un auditor."""

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
    """Lo que el especialista pide, **intersecado** con el catálogo del rol.

    Un especialista solo puede estrechar los permisos del rol, nunca ampliarlos:
    si el rol no tiene una herramienta, no hay agente que se la dé. Esa parte
    nunca estuvo en duda.

    Lo que sí estaba mal era exigirlas todas. **El catálogo de acciones depende
    del rol por diseño**: un inspector puede reclasificar criticidad y un
    supervisor de mantenimiento no, porque así lo declara el YAML. Con la
    versión anterior eso hacía que un supervisor **no pudiera construir el
    grafo**:

        ValueError: el rol 'supervisor_mantenimiento' no puede ver
        ['reclasificar_criticidad'], que el especialista 'acciones' necesita.

    Es decir: el rol que existe para *aprobar* no podía usar el sistema. Se
    descubrió al aprobar una parada contra el sistema desplegado.

    Que a un rol le falte una herramienta no es una falla de configuración: es la
    ontología funcionando.

    ## Una lista vacía es un resultado válido

    Se intentó tratarla como error dos veces seguidas y las dos estaban mal:
    `tecnico` no ve `calcular_vida_remanente` y `auditor` no ve ninguna acción.
    En los dos casos es **el YAML diciendo lo que ese rol puede hacer**, no una
    configuración rota.

    Quién decide qué hacer con un especialista vacío es `especialistas_utiles`:
    el grafo no construye ese nodo, así que el supervisor no puede rutear a un
    agente que no tiene con qué contestar.
    """
    pedidas = (
        HERRAMIENTAS_DE_ACCION
        if especialista == "acciones"
        else HERRAMIENTAS_POR_ESPECIALISTA[especialista]
    )
    catalogo = {h.name: h for h in compile_tools(ontologia, ctx.rol, context=ctx)}

    # El orden lo fija el especialista, no el catálogo: es deliberado y está
    # documentado en HERRAMIENTAS_POR_ESPECIALISTA.
    disponibles = [catalogo[nombre] for nombre in pedidas if nombre in catalogo]

    return disponibles


def especialistas_utiles(ontologia: Ontology, ctx: ExecutionContext) -> tuple[str, ...]:
    """Los especialistas consultivos que este rol puede usar de verdad.

    **El grafo no construye los que quedan vacíos**, así que el supervisor no
    puede rutear a un agente que no tiene con qué contestar. Es el mismo
    principio que el filtrado del catálogo por rol: lo que el modelo no ve, no lo
    puede elegir ni alucinar como disponible.

    `acciones` no está acá —no es un destino que se elija— y se construye siempre:
    además de proponer, es el nodo que redacta la respuesta final.
    """
    return tuple(
        nombre for nombre in especialistas_disponibles() if _herramientas_de(nombre, ontologia, ctx)
    )


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


def agente_acciones(ontologia: Ontology, ctx: ExecutionContext, **extra: Any) -> Any:
    """Redacta la respuesta final y propone acciones. **Acá se dispara el gate.**

    Las acciones irreversibles que este agente proponga las frena el
    `HumanInTheLoopMiddleware`, cuya configuración sale de la ontología. Usa el
    perfil `synthesis`: es la respuesta que lee el usuario y la que fundamenta
    una parada de planta.
    """
    return _construir("acciones", PROMPT_ACCIONES, "synthesis", ontologia, ctx, **extra)


def especialistas_disponibles() -> tuple[str, ...]:
    """Los tres entre los que el supervisor elige.

    `acciones` no está: no es un destino que se elija, corre siempre al final.
    """
    return tuple(HERRAMIENTAS_POR_ESPECIALISTA)
