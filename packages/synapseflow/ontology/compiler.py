"""Compilador de la ontología al catálogo de herramientas de LangChain.

Acá se materializa la promesa del ADR-0003: de un YAML declarativo salen los
schemas de argumentos, las herramientas que ve el modelo, la matriz de permisos
y la configuración de los gates de aprobación humana.

El flujo es:

    oil_and_gas.yaml
          │
          ├─► build_args_model(action)   → modelo Pydantic por acción
          ├─► compile_tools(role)        → list[BaseTool] filtrada por rol
          └─► interrupt_config(role)     → dict para HumanInTheLoopMiddleware

Una acción sin implementación registrada es un error de arranque, no un fallo
en runtime: si el YAML promete una herramienta, tiene que existir el código que
la ejecuta.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from synapseflow.ontology.schema import Action, Effect, Ontology, Parameter, PropertyType


class ToolResult(BaseModel):
    """Resultado de una acción, separado en lo que lee el modelo y lo que no.

    `content` es el texto que vuelve al LLM como ToolMessage: tiene que ser
    conciso, porque ocupa ventana de contexto en cada turno siguiente.

    `artifact` es la estructura cruda. No entra al contexto del modelo, pero
    viaja hasta la consola y hasta el log de auditoría, donde sí importa el
    detalle completo: los registros que se leyeron, las citas con documento y
    sección, los números intermedios de un cálculo.

    La separación es la razón de usar `response_format="content_and_artifact"`:
    sin ella, o el modelo recibe JSON crudo que le consume contexto, o la
    consola pierde la trazabilidad.
    """

    content: str
    artifact: dict[str, Any] = Field(default_factory=dict)


# Una implementación recibe los argumentos ya validados más el contexto de
# ejecución (usuario, rol, thread) y devuelve un ToolResult.
Implementation = Callable[..., Awaitable[ToolResult]]

_REGISTRY: dict[str, Implementation] = {}


class CompilationError(RuntimeError):
    """La ontología es válida pero no se puede compilar a herramientas."""


def implements(action_id: str) -> Callable[[Implementation], Implementation]:
    """Registra la implementación de una acción de la ontología.

    Uso:

        @implements("consultar_activo")
        async def consultar_activo(tag: str, *, ctx: ExecutionContext) -> dict:
            ...

    El nombre de la función no importa; lo que vincula es el `action_id`. Eso
    permite renombrar libremente en Python sin romper el contrato del YAML.
    """

    def decorador(fn: Implementation) -> Implementation:
        if action_id in _REGISTRY:
            raise CompilationError(
                f"la acción '{action_id}' ya tiene una implementación registrada "
                f"({_REGISTRY[action_id].__qualname__}). "
                "Dos implementaciones para la misma acción es siempre un error."
            )
        _REGISTRY[action_id] = fn
        return fn

    return decorador


def registered_actions() -> set[str]:
    return set(_REGISTRY)


def clear_registry() -> None:
    """Solo para tests: vacía el registro de implementaciones."""
    _REGISTRY.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Tipos: del YAML a anotaciones de Python
# ─────────────────────────────────────────────────────────────────────────────

_TIPOS_ESCALARES: dict[PropertyType, type] = {
    PropertyType.STRING: str,
    PropertyType.NUMBER: float,
    PropertyType.INTEGER: int,
    PropertyType.BOOLEAN: bool,
    # Pydantic coerce cadenas ISO-8601 a date, que es lo que produce un LLM.
    PropertyType.DATE: dt.date,
    # Una referencia se pasa por la clave natural de la entidad apuntada.
    PropertyType.REFERENCE: str,
}


def _anotacion(param: Parameter) -> Any:
    """Anotación de Python para un parámetro de la ontología.

    Los enums se compilan a `Literal[...]` en lugar de `str`. Es la diferencia
    entre que el modelo vea el conjunto de valores admitidos en el JSON schema
    de la herramienta y que tenga que adivinarlos desde la descripción.
    """
    if param.type is PropertyType.ENUM:
        valores = param.values or []
        return Literal[tuple(valores)]

    if param.type is PropertyType.ARRAY:
        interno = param.items or PropertyType.STRING
        if interno is PropertyType.ENUM:
            valores = param.values or []
            return list[Literal[tuple(valores)]]  # type: ignore[misc]
        return list[_TIPOS_ESCALARES.get(interno, str)]  # type: ignore[misc]

    tipo = _TIPOS_ESCALARES.get(param.type)
    if tipo is None:
        raise CompilationError(
            f"el parámetro '{param.name}' usa el tipo '{param.type}', "
            "que el compilador no sabe traducir a una anotación de Python"
        )
    return tipo


def build_args_model(action: Action) -> type[BaseModel]:
    """Modelo Pydantic con los argumentos de una acción.

    Es lo que LangChain serializa como JSON schema de la herramienta, así que
    las descripciones que vienen del YAML terminan siendo el prompt que el
    modelo lee para decidir qué pasar. No son comentarios: son parte del
    contrato con el LLM.
    """
    campos: dict[str, Any] = {}
    for param in action.parameters:
        anotacion = _anotacion(param)
        if param.required:
            campos[param.name] = (anotacion, Field(description=param.description))
        else:
            campos[param.name] = (
                anotacion | None if param.default is None else anotacion,
                Field(default=param.default, description=param.description),
            )

    modelo = create_model(
        f"{_pascal(action.id)}Args",
        __doc__=action.description.strip(),
        **campos,
    )
    return cast(type[BaseModel], modelo)


def _pascal(snake: str) -> str:
    return "".join(parte.capitalize() for parte in snake.split("_"))


# ─────────────────────────────────────────────────────────────────────────────
# Descripción que ve el modelo
# ─────────────────────────────────────────────────────────────────────────────


def tool_description(action: Action) -> str:
    """Descripción de la herramienta, enriquecida con su semántica de riesgo.

    Al modelo le decimos explícitamente cuándo una acción es irreversible. No
    reemplaza al gate —el gate es estructural y no depende de que el modelo
    cooperе— pero mejora notablemente la calidad de la propuesta: el agente
    tiende a fundamentar mejor y a pedir confirmación en su propio texto antes
    de invocar algo marcado como irreversible.
    """
    partes = [action.description.strip()]

    if not action.reversible:
        partes.append(
            "ACCIÓN IRREVERSIBLE. Requiere aprobación humana explícita antes de "
            "materializarse. Invocala solo con fundamento técnico verificable, y "
            "explicá en tu respuesta por qué corresponde."
        )
    elif action.effect is Effect.WRITE:
        partes.append(
            "Esta acción escribe datos, pero es reversible: no moviliza recursos "
            "ni impacta operación."
        )

    return "\n\n".join(partes)


# ─────────────────────────────────────────────────────────────────────────────
# Compilación a herramientas
# ─────────────────────────────────────────────────────────────────────────────


def compile_tools(
    ontology: Ontology,
    role: str,
    *,
    context: Any = None,
    require_all_implemented: bool = True,
) -> list[BaseTool]:
    """Catálogo de herramientas para un rol.

    El filtrado por rol es lo que hace efectivo el mínimo privilegio: el modelo
    no recibe las acciones que su rol no puede ejecutar, así que no puede
    invocarlas ni ofrecerlas. No hay un chequeo posterior que se pueda olvidar
    porque no hay nada que chequear.

    Args:
        ontology: dominio cargado.
        role: rol del usuario que invoca. Debe existir en la ontología.
        context: `ExecutionContext` que se inyecta en cada implementación.
        require_all_implemented: si es True, falla cuando una acción visible
            para el rol no tiene implementación registrada. Los tests de la
            capa de ontología lo bajan a False para poder compilar sin tocar la
            capa de datos.
    """
    acciones = ontology.actions_for_role(role)

    if require_all_implemented:
        faltantes = sorted(a.id for a in acciones if a.id not in _REGISTRY)
        if faltantes:
            raise CompilationError(
                "hay acciones en la ontología sin implementación registrada: "
                f"{faltantes}. Registralas con @implements o quitalas del YAML."
            )

    herramientas: list[BaseTool] = []
    for accion in acciones:
        impl = _REGISTRY.get(accion.id)
        if impl is None:
            continue
        herramientas.append(_a_structured_tool(accion, impl, context))
    return herramientas


def _a_structured_tool(action: Action, impl: Implementation, context: Any) -> BaseTool:
    """Envuelve una implementación como StructuredTool de LangChain."""
    args_model = build_args_model(action)

    async def _ejecutar(**kwargs: Any) -> tuple[str, dict[str, Any]]:
        resultado = await impl(**kwargs, ctx=context)
        if not isinstance(resultado, ToolResult):
            raise CompilationError(
                f"la implementación de '{action.id}' devolvió "
                f"{type(resultado).__name__} en lugar de ToolResult"
            )
        # El artifact lleva siempre la identidad de la acción: es lo que le
        # permite al log de auditoría reconstruir qué se ejecutó sin depender
        # de parsear el texto.
        artifact = {"action_id": action.id, **resultado.artifact}
        return resultado.content, artifact

    return StructuredTool(
        name=action.tool_name,
        description=tool_description(action),
        args_schema=args_model,
        coroutine=_ejecutar,
        # Toda la capa de datos es async: no hay camino sincrónico.
        func=None,
        # Separa lo que consume contexto del modelo de lo que necesita la
        # consola y la auditoría. Ver ToolResult.
        response_format="content_and_artifact",
        metadata={
            "action_id": action.id,
            "effect": action.effect.value,
            "reversible": action.reversible,
            "requires_approval": action.requires_approval,
            "target_entity": action.target_entity,
            "approver_roles": list(action.approver_roles),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Gates de aprobación humana
# ─────────────────────────────────────────────────────────────────────────────


def interrupt_config(ontology: Ontology, role: str) -> dict[str, Any]:
    """Configuración de `HumanInTheLoopMiddleware` derivada de la ontología.

    Devuelve el mapeo `{nombre_de_herramienta: InterruptOnConfig}` que espera el
    middleware de LangChain. Se construye recorriendo las acciones que el rol
    puede ejecutar y quedándose con las que declaran `requires_approval`.

    Que esto se derive del YAML y no se escriba a mano es el punto: un
    desarrollador no puede agregar una acción irreversible y olvidarse del gate,
    porque no es él quien escribe el gate.

    Las decisiones permitidas dependen del efecto:

    - `edit` se habilita solo cuando la acción tiene parámetros que un
      supervisor podría querer corregir antes de aprobar, por ejemplo bajar la
      prioridad de una orden de trabajo.
    - `respond` se habilita siempre: el aprobador puede rechazar explicando por
      qué, y ese texto vuelve al agente como contexto.
    """
    config: dict[str, Any] = {}

    for accion in ontology.actions_for_role(role):
        if not accion.requires_approval:
            continue

        decisiones: list[str] = ["approve", "reject", "respond"]
        if accion.parameters:
            decisiones.insert(1, "edit")

        entrada: dict[str, Any] = {"allowed_decisions": decisiones}

        if accion.approval_prompt:
            entrada["description"] = _descriptor_de_aprobacion(accion)

        config[accion.tool_name] = entrada

    return config


def _descriptor_de_aprobacion(action: Action) -> Callable[..., str]:
    """Fábrica del texto que ve el aprobador.

    El texto se arma con los argumentos reales de la invocación en lugar de ser
    genérico: un supervisor que aprueba una parada de equipo tiene que ver de qué
    equipo se trata, no «se requiere aprobación».

    ## Firma

    `HumanInTheLoopMiddleware` invoca este callable como
    `description(tool_call, state, runtime)`, donde `tool_call` es el `ToolCall`
    de LangChain: un `TypedDict` con `name`, `args` e `id`.

    Verificado por introspección contra `langchain` 1.3.14, en
    `langchain.agents.middleware.human_in_the_loop._DescriptionFactory`. Una
    versión anterior de esta función recibía un único argumento y buscaba los
    valores en `request.tool_call`, con lo que el gate lanzaba `TypeError` en la
    primera acción irreversible que se propusiera. `state` y `runtime` llevan
    default para que el descriptor se pueda invocar con solo el `tool_call`
    desde un test.
    """
    plantilla = (action.approval_prompt or "").strip()

    def descriptor(tool_call: Any, state: Any = None, runtime: Any = None) -> str:
        args: dict[str, Any] = {}
        if isinstance(tool_call, dict):
            args = dict(tool_call.get("args") or {})

        # Un placeholder sin valor se muestra como «no informado» en lugar de
        # romper el texto. No debería ocurrir: el meta-esquema exige que todo
        # placeholder corresponda a un parámetro declarado y requerido.
        class _Faltante(dict):
            def __missing__(self, clave: str) -> str:
                return "«no informado»"

        try:
            cuerpo = plantilla.format_map(_Faltante(args))
        except (ValueError, IndexError):  # pragma: no cover - defensivo
            cuerpo = plantilla

        encabezado = f"[{action.name}] acción irreversible"
        roles = ", ".join(action.approver_roles)
        pie = f"Aprobación requerida de: {roles}."
        return f"{encabezado}\n\n{cuerpo}\n\n{pie}"

    return descriptor
