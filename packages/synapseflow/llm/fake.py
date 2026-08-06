"""Modelo determinístico para tests. No hace red ni consume cuota.

Este módulo es el que permite testear todo lo que viene después. Sin él, cada
test que ejercite un agente tendría que llamar a un proveedor real: lento, no
reproducible —el modelo contesta distinto cada vez— y consumiendo cuota en cada
corrida de CI. Con esto, la suite del grafo completo corre en menos de un
segundo y da siempre lo mismo.

## Qué se le programa

    modelo = FakeChatModel(respuestas=[
        Respuesta(herramientas=[Llamada("consultar_activo", {"tag": "P-2101-A"})]),
        Respuesta(texto="El activo no está apto para continuar en servicio."),
    ])

La cola se consume en orden: una entrada por llamada al modelo. Es lo que
reproduce el bucle de un agente —primero invoca una herramienta, después redacta
con el resultado— y, sobre todo, lo que permite ejercitar los gates de
aprobación sin un proveedor: se programa la invocación de una acción
irreversible y se verifica que el grafo se detenga.

También acepta respuestas por patrón sobre el último mensaje humano, para tests
donde importa qué se pregunta y no en qué orden.

## Por qué falla al agotarse la cola

Un agente que entra en un ciclo de herramientas sin converger es el modo de
falla más caro que existe. Si la cola se repitiera en silencio, un test con ese
bug pasaría igual y el problema aparecería en producción. Por eso agotar la cola
lanza `FakeChatModelError`, que nombra cuántas llamadas hubo. Para los tests que
necesitan lo contrario —verificar que `ModelCallLimitMiddleware` corta un
bucle— está `ciclico=True`.

Ver docs/plan/fases/F1-gateway.md § F1.2
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

# Aproximación de tokens: un token cada cuatro caracteres. No pretende ser
# exacta —cada proveedor tokeniza distinto— sino determinística y del orden de
# magnitud correcto, que es lo que necesita la contabilidad de costo de F1.4
# para poder verificarse contra un número esperado.
CARACTERES_POR_TOKEN = 4


class FakeChatModelError(RuntimeError):
    """El modelo falso no tiene con qué responder a esta llamada."""


class Llamada(BaseModel):
    """Una invocación de herramienta que el modelo va a emitir."""

    model_config = ConfigDict(frozen=True)

    nombre: str
    argumentos: dict[str, Any] = Field(default_factory=dict)


class Respuesta(BaseModel):
    """Lo que el modelo contesta en una llamada.

    Puede llevar texto, invocaciones de herramienta, o ambos: un agente que
    explica qué va a hacer antes de invocar produce exactamente esa forma.
    """

    model_config = ConfigDict(frozen=True)

    texto: str = ""
    herramientas: tuple[Llamada, ...] = ()
    # Sobreescriben el conteo estimado. Sirven para fijar un costo esperado en
    # los tests de contabilidad sin depender de la longitud del texto.
    tokens_entrada: int | None = None
    tokens_salida: int | None = None


class FakeChatModel(BaseChatModel):
    """Modelo de chat programable, determinístico y sin red."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Cola consumida en orden, una entrada por llamada.
    respuestas: list[Respuesta] = Field(default_factory=list)
    # Alternativa por contenido: la primera clave que aparezca —como expresión
    # regular— en el último mensaje humano decide la respuesta.
    por_patron: dict[str, Respuesta] = Field(default_factory=dict)
    por_defecto: Respuesta | None = None
    # Si la cola se agota, repetir la última en lugar de fallar.
    ciclico: bool = False

    # ── Estado observable por los tests ──────────────────────────────────────
    llamadas: int = 0
    # Los mensajes que recibió en cada llamada. Permite afirmar sobre lo que el
    # grafo le mandó al modelo, que es donde se detecta si un dato sensible
    # llegó a cruzar la frontera.
    recibidos: list[list[BaseMessage]] = Field(default_factory=list)
    herramientas_ligadas: list[str] = Field(default_factory=list)
    # Cola de objetos que devuelve `with_structured_output`.
    estructurados: list[Any] = Field(default_factory=list)

    # Nombre que el modelo reporta a los callbacks. Sin esto, `invocation_params`
    # no lleva `model` y la contabilidad de costo no puede saber qué se ejecutó:
    # el modelo falso quedaría fuera del alcance de sus propios tests, que es
    # justo lo que no puede pasar con la pieza que existe para testear.
    modelo: str = "fake-router"

    @property
    def _llm_type(self) -> str:
        return "synapseflow-fake"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        """Lo que LangChain publica en `invocation_params` de cada llamada."""
        return {"model": self.modelo}

    # ── Generación ───────────────────────────────────────────────────────────

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.llamadas += 1
        self.recibidos.append(list(messages))
        respuesta = self._elegir(messages)
        return ChatResult(
            generations=[ChatGeneration(message=self._a_mensaje(respuesta, messages))]
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Camino async explícito.

        La implementación por defecto de `BaseChatModel` delega en un executor.
        Acá no hace falta: no hay nada que bloquee, y evitar el hilo mantiene la
        ejecución en el mismo event loop que el resto de la plataforma.
        """
        return self._generate(messages, stop, None, **kwargs)

    def _elegir(self, messages: Sequence[BaseMessage]) -> Respuesta:
        if self.respuestas:
            indice = self.llamadas - 1
            if indice < len(self.respuestas):
                return self.respuestas[indice]
            if self.ciclico:
                return self.respuestas[-1]
            raise FakeChatModelError(
                f"el modelo falso recibió {self.llamadas} llamadas y solo tiene "
                f"{len(self.respuestas)} respuestas programadas.\n"
                "  Si el agente debía converger antes, esto es el bug que el test "
                "buscaba.\n"
                "  Si el bucle es deliberado —por ejemplo para verificar un límite "
                "de llamadas— usá ciclico=True."
            )

        if self.por_patron:
            ultimo = self._ultimo_humano(messages)
            for patron, respuesta in self.por_patron.items():
                if re.search(patron, ultimo, re.IGNORECASE):
                    return respuesta

        if self.por_defecto is not None:
            return self.por_defecto

        raise FakeChatModelError(
            "el modelo falso no tiene respuesta para esta llamada: no hay cola, "
            "ningún patrón coincide con el último mensaje y no se declaró "
            "`por_defecto`."
        )

    @staticmethod
    def _ultimo_humano(messages: Sequence[BaseMessage]) -> str:
        for mensaje in reversed(messages):
            if isinstance(mensaje, HumanMessage):
                return str(mensaje.content)
        return str(messages[-1].content) if messages else ""

    def _a_mensaje(self, respuesta: Respuesta, entrada: Sequence[BaseMessage]) -> AIMessage:
        # El id de cada invocación se deriva del contador, no de un uuid: dos
        # corridas del mismo test tienen que producir exactamente los mismos
        # ids, o cualquier aserción sobre el estado del grafo sería inestable.
        tool_calls = [
            {
                "name": llamada.nombre,
                "args": dict(llamada.argumentos),
                "id": f"call_{self.llamadas}_{i}",
                "type": "tool_call",
            }
            for i, llamada in enumerate(respuesta.herramientas)
        ]

        entrada_tokens = (
            respuesta.tokens_entrada
            if respuesta.tokens_entrada is not None
            else _tokens("".join(str(m.content) for m in entrada))
        )
        salida_tokens = (
            respuesta.tokens_salida
            if respuesta.tokens_salida is not None
            else _tokens(respuesta.texto) + sum(_tokens(str(t["args"])) for t in tool_calls)
        )

        return AIMessage(
            content=respuesta.texto,
            tool_calls=tool_calls,
            usage_metadata=UsageMetadata(
                input_tokens=entrada_tokens,
                output_tokens=salida_tokens,
                total_tokens=entrada_tokens + salida_tokens,
            ),
        )

    # ── Herramientas y salida estructurada ───────────────────────────────────

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        """Registra las herramientas y devuelve el modelo ligado.

        `BaseChatModel.bind_tools` lanza `NotImplementedError` por defecto, así
        que sin esto `create_agent` no acepta el modelo. Los nombres se guardan
        para que un test pueda afirmar **qué catálogo recibió el modelo**, que es
        la forma de verificar el mínimo privilegio: lo que no está en esta lista,
        el modelo no lo puede invocar.
        """
        self.herramientas_ligadas = [_nombre_de_herramienta(t) for t in tools]
        return self.bind(tools=list(tools), tool_choice=tool_choice, **kwargs)

    def with_structured_output(
        self,
        schema: dict[str, Any] | type,
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable[Any, dict[str, Any] | BaseModel]:
        """Devuelve el siguiente objeto de la cola `estructurados`.

        Se sobreescribe la implementación por defecto —que liga el schema como
        herramienta y parsea la invocación— porque para un modelo falso ese
        rodeo solo agrega formas de fallar. Acá el test declara directamente qué
        objeto quiere recibir.
        """
        cola = list(self.estructurados)

        def responder(_: Any) -> Any:
            self.llamadas += 1
            if not cola:
                raise FakeChatModelError(
                    "with_structured_output se invocó más veces que objetos hay en `estructurados`."
                )
            valor = cola.pop(0)
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                return valor if isinstance(valor, schema) else schema.model_validate(valor)
            return valor

        return RunnableLambda(responder)

    # ── Utilidades de test ───────────────────────────────────────────────────

    def reiniciar(self) -> None:
        """Vuelve el contador y el historial a cero, sin tocar lo programado."""
        self.llamadas = 0
        self.recibidos = []

    @property
    def texto_recibido(self) -> str:
        """Todo lo que el modelo vio, concatenado.

        Es lo que usa el test de la frontera de datos: si un legajo aparece acá,
        cruzó hacia el proveedor.
        """
        return "\n".join(str(m.content) for llamada in self.recibidos for m in llamada)


class FakeEmbeddings(Embeddings):
    """Embeddings determinísticos, sin red, con similitud léxica real.

    ## Por qué no es un vector aleatorio

    Lo más corto sería derivar el vector de un hash del texto entero. Sería
    determinístico y serviría para verificar que la ingesta escribe algo, pero
    dejaría los tests de recuperación de F3 sin poder afirmar nada: dos textos
    que comparten todas sus palabras salvo una darían vectores sin relación, y
    «el fragmento pertinente sale primero» no se podría testear.

    Acá se usa el *hashing trick*: cada palabra cae en un bucket por su hash y
    suma ahí. Dos textos con vocabulario en común comparten buckets, así que el
    coseno entre ellos es alto de verdad. No hay semántica —«cañería» y «tubería»
    siguen siendo desconocidas entre sí— pero sí solapamiento léxico, que es
    suficiente para verificar que el pipeline ordena por pertinencia y no por
    orden de inserción.

    ## Por qué hashlib y no hash()

    `hash()` de Python está salteado por proceso: el mismo texto da enteros
    distintos entre corridas. Un corpus indexado en una corrida no sería
    recuperable en la siguiente, que es exactamente el bug silencioso que un
    modelo falso no puede permitirse.
    """

    def __init__(self, dimensiones: int) -> None:
        if dimensiones <= 0:
            raise ValueError(f"dimensiones tiene que ser positivo, no {dimensiones}")
        self.dimensiones = dimensiones

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(texto) for texto in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensiones
        for palabra in _PALABRAS.findall(text.lower()):
            digest = hashlib.blake2b(palabra.encode("utf-8"), digest_size=8).digest()
            entero = int.from_bytes(digest, "big")
            # El bit menos significativo decide el signo. Sin él, todas las
            # componentes serían positivas y el coseno entre dos textos
            # cualesquiera daría alto aunque no compartieran una sola palabra.
            signo = 1.0 if entero & 1 else -1.0
            vector[(entero >> 1) % self.dimensiones] += signo

        norma = math.sqrt(sum(c * c for c in vector))
        if norma == 0.0:
            # Texto sin palabras. Un vector de ceros no es normalizable y
            # Firestore lo aceptaría, dejando un documento que nunca se recupera.
            # Se ancla en una componente fija para que al menos sea consistente.
            vector[0] = 1.0
            return vector
        return [c / norma for c in vector]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


# Palabras con acentos y ñ: el corpus es normativa técnica en español y partir
# por [a-z]+ dejaría «inspección» como «inspecci» + «n».
_PALABRAS = re.compile(r"[0-9a-záéíóúüñ]+")


def _tokens(texto: str) -> int:
    return max(1, len(texto) // CARACTERES_POR_TOKEN) if texto else 0


def _nombre_de_herramienta(herramienta: Any) -> str:
    if isinstance(herramienta, BaseTool):
        return herramienta.name
    if isinstance(herramienta, dict):
        return str(herramienta.get("name") or herramienta.get("function", {}).get("name", "?"))
    return getattr(herramienta, "__name__", str(herramienta))
