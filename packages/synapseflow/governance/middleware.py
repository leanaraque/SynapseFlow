"""El pipeline de gobernanza, ensamblado sobre `AgentMiddleware`.

Las garantías del proyecto son una **capa que atraviesa a todos los agentes**, no
código repetido en cada uno. Un especialista nuevo hereda la redacción de PII, el
gate de aprobación y el techo de llamadas sin que su autor tenga que acordarse:
una garantía que hay que recordar aplicar no es una garantía.

## Por qué la redacción es un middleware propio y no `PIIMiddleware`

LangChain trae `PIIMiddleware` con estrategias `block/redact/mask/hash`, y
ninguna sirve acá. Todas **destruyen** el dato: reemplazan el legajo por un
marcador y no lo devuelven. La respuesta volvería diciendo ««REDACTED» debe
recalibrar el equipo» y el usuario no podría actuar sobre ella.

Lo que este dominio necesita es tokenización **reversible**: el modelo externo ve
`«INSPECTOR_1»`, razona sobre ese referente, y la respuesta se rehidrata antes de
llegar al usuario. Eso es `RedaccionDePII`, sobre el hook `wrap_model_call`, que
es exactamente el punto donde el texto cruza el perímetro.

## El orden del pipeline no es arbitrario

    ModelCallLimitMiddleware   ← corta un bucle antes de que cueste
    RedaccionDePII             ← tokeniza lo que sale, rehidrata lo que vuelve
    HumanInTheLoopMiddleware   ← frena la acción irreversible

El límite va primero porque un agente en bucle es el modo de falla más caro que
existe, y cortarlo temprano ahorra las llamadas que los otros dos igual habrían
procesado. El gate va último porque opera sobre la invocación de herramienta ya
decidida, después de que el modelo respondió.

Ver docs/plan/fases/F4-gobernanza.md § F4.5 y
docs/plan/00-convenciones.md § Hallazgo 4
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import BaseMessage

from synapseflow.config import Settings, get_settings
from synapseflow.governance.pii import Tokenizador
from synapseflow.ontology import Ontology, interrupt_config

# Una capa del pipeline.
#
# `AgentMiddleware` es genérico en el estado, el contexto y la respuesta, y los
# middlewares que trae LangChain los parametrizan distinto entre sí:
# `ModelCallLimitMiddleware[Any, None]` no es asignable a
# `AgentMiddleware[AgentState[Any], None, Any]`, que es lo que mypy infiere de la
# forma sin parámetros. Fijar los tres en `Any` describe lo que la lista es de
# verdad —capas heterogéneas que el agente compone— en lugar de silenciar el
# error con un ignore que taparía también un tipo equivocado.
CapaDeGobernanza = AgentMiddleware[Any, Any, Any]


class RedaccionDePII(AgentMiddleware):
    """Tokeniza los datos personales al salir y los rehidrata al volver.

    Se instancia **una por conversación**, porque el tokenizador lo es: compartir
    uno entre hilos correlacionaría a la misma persona entre conversaciones
    distintas. Ver `governance.pii`.

    El `wrap_model_call` es el único punto por donde el texto cruza hacia el
    proveedor, así que envolverlo hace que la garantía no dependa de que cada
    agente se acuerde de redactar.
    """

    def __init__(self, tokenizador: Tokenizador | None = None) -> None:
        super().__init__()
        # `is None` y no `or`: `Tokenizador` define `__len__`, así que uno recién
        # creado —vacío— es **falsy**. Con `tokenizador or Tokenizador()`, el que
        # inyecta el llamador se descartaba en silencio y la auditoría terminaba
        # guardando un mapa de tokenización vacío mientras la redacción
        # funcionaba perfectamente. Lo encontró el test que comparte el
        # tokenizador entre esta capa y el log.
        self.tokenizador = Tokenizador() if tokenizador is None else tokenizador

    # ── Camino async, que es el que usa la plataforma ────────────────────────

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        respuesta = await handler(self._tokenizar_pedido(request))
        return self._rehidratar_respuesta(respuesta)

    # ── Camino sync, para tests y para `invoke` ──────────────────────────────

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        respuesta = handler(self._tokenizar_pedido(request))
        return self._rehidratar_respuesta(respuesta)

    # ── Lo que hace de verdad ────────────────────────────────────────────────

    def _tokenizar_pedido(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """Reemplaza los datos personales de todo lo que va hacia el proveedor.

        Se tokenizan los mensajes **y** el mensaje de sistema. El de sistema no
        debería llevar PII, pero «no debería» no es una garantía: si algún día un
        prompt se arma con datos del hilo, esto lo cubre sin que nadie tenga que
        acordarse.
        """
        cambios: dict[str, Any] = {
            "messages": [self._tokenizar_mensaje(m) for m in request.messages]
        }
        if request.system_message is not None:
            cambios["system_message"] = self._tokenizar_mensaje(request.system_message)

        return request.override(**cambios)

    def _rehidratar_respuesta(self, respuesta: ModelResponse[Any]) -> ModelResponse[Any]:
        """Devuelve los valores originales antes de que la respuesta siga viaje."""
        return dataclasses.replace(
            respuesta, result=[self._rehidratar_mensaje(m) for m in respuesta.result]
        )

    def _tokenizar_mensaje(self, mensaje: BaseMessage) -> BaseMessage:
        return _con_contenido(mensaje, self.tokenizador.tokenizar)

    def _rehidratar_mensaje(self, mensaje: BaseMessage) -> BaseMessage:
        return _con_contenido(mensaje, self.tokenizador.rehidratar)


def _con_contenido(mensaje: BaseMessage, transformar: Callable[[str], str]) -> BaseMessage:
    """Aplica una transformación al texto de un mensaje, sin tocar el resto.

    El contenido de un mensaje puede ser una cadena o una lista de bloques
    —texto, imágenes, uso de herramienta—. Se recorre la lista y se transforma
    solo lo que es texto: convertir un bloque de imagen a `str` y de vuelta
    rompería el mensaje.
    """
    contenido = mensaje.content

    if isinstance(contenido, str):
        nuevo: Any = transformar(contenido)
    elif isinstance(contenido, list):
        nuevo = [
            transformar(bloque)
            if isinstance(bloque, str)
            else (
                {**bloque, "text": transformar(bloque["text"])}
                if isinstance(bloque, dict) and isinstance(bloque.get("text"), str)
                else bloque
            )
            for bloque in contenido
        ]
    else:  # pragma: no cover - defensivo
        return mensaje

    if nuevo == contenido:
        # Sin PII no hay nada que copiar. Evita reconstruir el mensaje —y perder
        # atributos que una subclase pudiera agregar— en el caso más común.
        return mensaje

    return mensaje.model_copy(update={"content": nuevo})


# ─────────────────────────────────────────────────────────────────────────────
# Ensamblado
# ─────────────────────────────────────────────────────────────────────────────


def construir_middleware(
    ontologia: Ontology,
    rol: str,
    *,
    settings: Settings | None = None,
    tokenizador: Tokenizador | None = None,
) -> list[CapaDeGobernanza]:
    """El pipeline de gobernanza para un rol, listo para `create_agent`.

    Args:
        ontologia: dominio cargado. De acá salen los gates.
        rol: rol del usuario. Determina qué acciones tienen gate.
        settings: configuración. Cada garantía se puede desactivar por bandera,
            pero **ninguna está desactivada por omisión**.
        tokenizador: el de la conversación. Uno nuevo por hilo.

    Las tres banderas —`redact_pii`, `require_approval`,
    `max_model_calls_per_run`— vienen activas de fábrica. Desactivar una es una
    decisión que alguien toma y que queda escrita en el entorno, no un default.
    """
    config = settings or get_settings()
    pipeline: list[CapaDeGobernanza] = []

    # Un agente que entra en un ciclo de herramientas sin converger es el modo de
    # falla más caro que existe: se corta acá y no en la factura.
    if config.max_model_calls_per_run > 0:
        pipeline.append(
            ModelCallLimitMiddleware(
                run_limit=config.max_model_calls_per_run,
                # `error` y no `end`: un turno que se cortó por límite no produjo
                # una respuesta, y devolver lo que había hasta ahí como si fuera
                # la respuesta final es peor que fallar.
                exit_behavior="error",
            )
        )

    if config.redact_pii:
        pipeline.append(RedaccionDePII(tokenizador))

    if config.require_approval:
        gates = interrupt_config(ontologia, rol)
        if gates:
            # `interrupt_config` deriva los gates del YAML. Un desarrollador no
            # puede agregar una acción irreversible y olvidarse del gate porque
            # no es él quien lo escribe.
            pipeline.append(HumanInTheLoopMiddleware(interrupt_on=gates))

    return pipeline


def describir_pipeline(pipeline: list[CapaDeGobernanza]) -> list[str]:
    """Nombres de las capas activas, para el log de arranque y para los tests.

    Que el pipeline sea inspeccionable importa: es la diferencia entre poder
    afirmar qué garantías están activas y suponerlo.
    """
    return [type(capa).__name__ for capa in pipeline]
