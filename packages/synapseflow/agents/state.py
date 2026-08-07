"""El estado que viaja por el grafo.

## Estrecho, y por dos razones

La primera es de costo: **todo lo que está acá se serializa en cada
checkpoint**. Un grafo que pasa por seis nodos escribe seis veces el estado
entero, y un campo que nadie lee se paga seis veces.

La segunda pesa más. El estado tiene que ser **serializable**, porque el
checkpointer lo persiste y lo reconstruye después de que el proceso murió — que
es toda la promesa del human-in-the-loop asincrónico. Un cliente de Firestore o
un modelo de LangChain adentro del estado rompe eso en silencio: funciona en
memoria y falla al reanudar, horas después, cuando el supervisor aprieta Aprobar.

> **Nada de clientes, conexiones ni objetos vivos.** No es una guía de estilo:
> es la restricción que impone el checkpointer. Hay un test que la verifica
> serializando el estado con el mismo `serde` que usa `FirestoreSaver`.

## Por qué `recuperados` y `calculos` viven en el estado

Podrían pasarse de nodo a nodo por parámetro y no ocupar checkpoint. Viven acá
porque el **verificador corre al final** y necesita exactamente los fragmentos
que el redactor tuvo delante: si se los volviera a pedir al retriever, estaría
verificando contra otro conjunto y la garantía del compromiso 4 dejaría de valer.

Lo mismo con `calculos`: el número que sale de Python determinístico tiene que
llegar a la respuesta sin pasar por la interpretación del modelo. Está en el
estado para que el ensamblado final lo lea de ahí y no del texto.

## Por qué este módulo NO usa `from __future__ import annotations`

Es el único del paquete que no lo hace, y no es un descuido. Con las anotaciones
diferidas quedan como **cadenas**, y `TypedDict` no puede resolver `NotRequired`:
`AgentState.__optional_keys__` da vacío y los seis campos figuran como
obligatorios.

Eso no es cosmético. LangGraph inspecciona las anotaciones del esquema de estado
en tiempo de ejecución —para descubrir el reductor `add_messages`, entre otras
cosas— y una anotación que es una cadena no le dice nada. Verificado contra
Python 3.11: la misma clase con la anotación evaluada da
`__optional_keys__ == {'b'}` y con la anotación diferida da `frozenset()`.

Ver docs/plan/fases/F5-grafo.md § F5.1
"""

from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

# Cuántas vueltas puede dar el ciclo verificador → normativa antes de rendirse.
# Dos vueltas sin fundamento significan que no está en el corpus, y una tercera
# gasta cuota para llegar a la misma conclusión.
MAX_REINTENTOS_DE_FUNDAMENTO = 2


class AgentState(TypedDict):
    """Lo que el grafo lleva de un nodo al siguiente.

    `messages` usa `add_messages` como reductor: cada nodo devuelve solo los
    mensajes que agrega y LangGraph los acumula. Devolver la lista entera desde
    cada nodo funcionaría y duplicaría el historial en cada paso.

    El resto de los campos son `NotRequired` porque el estado inicial de una
    conversación solo trae `messages`: exigirlos obligaría a todo call site a
    inventar valores vacíos para campos que el grafo va a llenar.
    """

    messages: Annotated[list[AnyMessage], add_messages]

    # Fragmentos que el retriever devolvió para esta consulta. Son contra los que
    # el verificador contrasta: tienen que ser los mismos que vio el redactor.
    recuperados: NotRequired[list[Document]]

    # Citas extraídas de la respuesta, ya validadas contra `recuperados`.
    citas: NotRequired[list[dict[str, str]]]

    # Números que salieron de Python determinístico, no del modelo. Ver
    # `domain.calculos` y el compromiso 3.
    calculos: NotRequired[dict[str, Any]]

    # Salida del verificador de fundamento: `fundamentada`, `parcial` o
    # `sin_fundamento`. `None` significa que todavía no se verificó.
    veredicto: NotRequired[str | None]

    # Cuántas veces el verificador mandó de vuelta a buscar más contexto. Vive en
    # el estado y no en una variable del nodo porque el ciclo puede atravesar un
    # checkpoint: sin persistirlo, reanudar reiniciaría la cuenta y el grafo
    # podría no terminar nunca.
    reintentos: NotRequired[int]


def estado_inicial(pregunta: str) -> AgentState:
    """Estado con el que arranca una conversación.

    Existe para que ningún call site arme el diccionario a mano: un campo que
    falte se manifiesta como `KeyError` en el nodo que lo lee, lejos de acá.
    """
    return {
        "messages": [{"role": "user", "content": pregunta}],  # type: ignore[list-item]
        "recuperados": [],
        "citas": [],
        "calculos": {},
        "veredicto": None,
        "reintentos": 0,
    }


def puede_reintentar(estado: AgentState) -> bool:
    """Si el ciclo de recuperación tiene otra vuelta disponible.

    El ciclo verificador → normativa es una de las dos razones por las que este
    proyecto usa un motor de grafos y no una cadena lineal. Sin techo, una
    pregunta cuya respuesta no está en el corpus lo haría girar indefinidamente.
    """
    return estado.get("reintentos", 0) < MAX_REINTENTOS_DE_FUNDAMENTO


def campos_no_serializables(estado: AgentState) -> list[str]:
    """Campos del estado que el checkpointer no podría persistir.

    Se usa en los tests y en el arranque en modo debug. Devuelve la lista en
    lugar de lanzar para que el mensaje pueda nombrar todos los culpables de una
    vez, en lugar de obligar a descubrirlos de a uno.
    """
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    serde = JsonPlusSerializer()
    culpables: list[str] = []

    for clave, valor in estado.items():
        try:
            serde.dumps_typed(valor)
        except Exception:
            # Se captura todo a propósito: cualquier fallo al serializar
            # descalifica el campo igual, y distinguir el tipo de error acá solo
            # abriría la puerta a dejar pasar uno que no estaba previsto.
            culpables.append(clave)

    return culpables
