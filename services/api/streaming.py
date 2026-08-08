"""Del grafo a la consola: `astream_events` traducido a Server-Sent Events.

## Por qué el usuario tiene que ver el proceso

Un spinner opaco durante veinte segundos es peor experiencia que ver
«consultando el activo P-2101-A». Y en este dominio no es solo experiencia: ver
qué herramientas se ejecutaron es parte de poder auditar la respuesta.

## La traducción está separada del transporte

`eventos()` es un generador de `Evento`, y `sse()` los serializa. Se puede
verificar la traducción sin levantar un servidor ni parsear texto, que es lo que
hace que los tests digan algo sobre el comportamiento y no sobre el formato.

## Tres cosas que se verificaron contra la librería instalada

1. **`create_agent` invoca el modelo con `ainvoke`, no con `astream`.** Está en
   `langchain/agents/factory.py`. Por eso hoy no hay eventos
   `on_chat_model_stream` y el texto llega entero en `on_chat_model_end`. El
   traductor emite `token` en los dos casos —y no duplica si el proveedor
   empieza a emitir trozos— así que la consola no cambia el día que eso cambie.

2. **El nodo que informa `langgraph_node` es el interno, no el nuestro.** Los
   especialistas son grafos compilados que corren dentro de un nodo, así que un
   evento de herramienta dice `node = tools`. El nodo externo está en el primer
   segmento de `langgraph_checkpoint_ns` (`acciones:<uuid>|tools:<uuid>`), y es
   el que le sirve a la consola para decir qué agente hizo qué.

3. **El gate llega como `on_chain_stream` con `__interrupt__` en el chunk.** El
   valor trae `action_requests` —nombre, argumentos y descripción— y
   `review_configs` con las decisiones permitidas.

## El orden en que se emite no es el orden en que llega

El gate aparece a mitad del flujo y las citas solo se conocen al final. Se
emiten citas y después la aprobación, que es el orden en que la respuesta se
lee: fundamento primero, propuesta después. Pedirle a alguien que apruebe una
parada antes de mostrarle las fuentes es pedirle que apruebe a ciegas.

## Un error a mitad del flujo no puede ser un 500

El 200 ya salió con la primera línea. Por eso el flujo termina **siempre** con
exactamente un evento terminal, `fin` o `error`: una consola que espera un final
no se queda colgada esperando un status que no va a cambiar.

Ver docs/plan/fases/F6-api.md § F6.2
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from synapseflow.agents.graph import NODO_ACCIONES

# Los nombres son el contrato con la consola. Se declaran una vez acá para que
# un typo rompa el import y no deje a la consola esperando un evento que nunca
# llega — que es como se rompe en silencio un protocolo de texto.
# El `noqa` es porque S105 ve un secreto en cualquier constante que se llame
# TOKEN. Acá es un token de texto del modelo, no una credencial.
TOKEN = "token"  # noqa: S105
HERRAMIENTA_INICIO = "herramienta_inicio"
HERRAMIENTA_FIN = "herramienta_fin"
CITAS = "citas"
APROBACION_REQUERIDA = "aprobacion_requerida"
ERROR = "error"
FIN = "fin"

TERMINALES = (FIN, ERROR)


class Evento(BaseModel):
    """Un evento del recorrido, antes de convertirse en texto SSE."""

    model_config = ConfigDict(frozen=True)

    tipo: str
    datos: dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Traducción
# ─────────────────────────────────────────────────────────────────────────────


async def eventos(
    grafo: Any,
    entrada: Any,
    config: Mapping[str, Any],
    *,
    nodo_final: str = NODO_ACCIONES,
) -> AsyncIterator[Evento]:
    """Traduce el recorrido del grafo a eventos de dominio.

    Args:
        grafo: grafo compilado.
        entrada: estado inicial, o `Command(resume=...)` para reanudar un gate.
        config: lleva el `thread_id`, que es lo que hace reanudable al recorrido.
        nodo_final: el nodo cuya salida del modelo es *la respuesta*. Los demás
            razonan; su texto no es para el usuario.
    """
    fragmentos: list[dict[str, Any]] = []
    vistas: set[tuple[str, str]] = set()
    aprobacion: Evento | None = None
    # Runs que ya emitieron texto trozo a trozo. Evita que `on_chat_model_end`
    # repita entera una respuesta que la consola ya fue pintando.
    con_trozos: set[str] = set()

    try:
        async for evento in grafo.astream_events(entrada, config):
            clase = evento.get("event")
            datos = evento.get("data") or {}
            nodo = _nodo_externo(evento.get("metadata") or {})

            if clase == "on_tool_start":
                yield Evento(
                    tipo=HERRAMIENTA_INICIO,
                    datos={
                        "herramienta": evento.get("name"),
                        "agente": nodo,
                        "argumentos": datos.get("input") or {},
                    },
                )

            elif clase == "on_tool_end":
                salida = datos.get("output")
                detalle = getattr(salida, "artifact", None) or {}
                _acumular_citas(detalle, fragmentos, vistas)
                yield Evento(
                    tipo=HERRAMIENTA_FIN,
                    datos={
                        "herramienta": evento.get("name"),
                        "agente": nodo,
                        # El `content` y no el `artifact`: es lo que el modelo
                        # leyó, y por lo tanto lo que explica su respuesta.
                        "contenido": _texto(salida),
                        # El detalle sí viaja, porque es lo que permite mostrar
                        # los números intermedios de un cálculo sin pedírselos
                        # al modelo. Ver ToolResult.
                        "detalle": detalle,
                    },
                )

            elif clase == "on_chat_model_stream" and nodo == nodo_final:
                texto = _texto(datos.get("chunk"))
                if texto:
                    con_trozos.add(str(evento.get("run_id")))
                    yield Evento(tipo=TOKEN, datos={"texto": texto})

            elif clase == "on_chat_model_end" and nodo == nodo_final:
                texto = _texto(datos.get("output"))
                if texto and str(evento.get("run_id")) not in con_trozos:
                    yield Evento(tipo=TOKEN, datos={"texto": texto})

            elif clase == "on_chain_stream":
                interrumpido = _interrupciones(datos.get("chunk"))
                if interrumpido:
                    aprobacion = Evento(tipo=APROBACION_REQUERIDA, datos=_aprobacion(interrumpido))

    except Exception as exc:
        # `CancelledError` no es `Exception`: si el cliente cortó, la cancelación
        # tiene que propagarse y liberar el grafo, no convertirse en un evento
        # que nadie va a leer.
        yield Evento(tipo=ERROR, datos={"error": str(exc), "clase": type(exc).__name__})
        return

    if fragmentos:
        yield Evento(tipo=CITAS, datos={"citas": fragmentos})

    if aprobacion is not None:
        yield aprobacion

    yield Evento(tipo=FIN, datos={"thread_id": _thread_id(config)})


# ─────────────────────────────────────────────────────────────────────────────
# Serialización
# ─────────────────────────────────────────────────────────────────────────────


def sse(evento: Evento) -> str:
    """Un evento como bloque SSE.

    El JSON va en una sola línea `data:` porque JSON escapa los saltos de línea
    dentro de las cadenas: no puede producir el carácter que terminaría el bloque
    antes de tiempo. `ensure_ascii=False` porque el dominio es en español y
    escapar cada acento triplica el tamaño del flujo.
    """
    cuerpo = json.dumps(evento.datos, ensure_ascii=False, default=str)
    return f"event: {evento.tipo}\ndata: {cuerpo}\n\n"


async def flujo_sse(fuente: AsyncIterator[Evento]) -> AsyncIterator[str]:
    """Los eventos como texto SSE, listos para `StreamingResponse`.

    Arranca con un comentario. Los proxies suelen retener la respuesta hasta
    juntar un buffer, y con él las cabeceras salen enseguida: sin eso, el usuario
    mira una pantalla vacía hasta el primer evento de verdad.
    """
    yield ": abierto\n\n"
    async for evento in fuente:
        yield sse(evento)


# ─────────────────────────────────────────────────────────────────────────────
# Detalles de la traducción
# ─────────────────────────────────────────────────────────────────────────────


def _nodo_externo(metadata: Mapping[str, Any]) -> str:
    """Qué nodo *nuestro* produjo el evento.

    `langgraph_node` informa el nodo interno del subgrafo —`model`, `tools`—
    porque cada especialista es un grafo compilado que corre adentro de un nodo.
    El namespace del checkpoint conserva la jerarquía completa, y su primer
    segmento es el nodo externo.
    """
    ns = str(metadata.get("langgraph_checkpoint_ns") or "")
    if ns:
        return ns.split("|")[0].split(":")[0]
    return str(metadata.get("langgraph_node") or "")


def _texto(mensaje: Any) -> str:
    """El texto de un mensaje, venga como cadena o como bloques de contenido.

    LangChain 1.x admite las dos formas y los proveedores no coinciden. Leer
    `.content` a secas funciona hasta el día que un proveedor devuelve bloques, y
    entonces la consola muestra la repr de una lista de diccionarios.
    """
    contenido = getattr(mensaje, "content", mensaje)

    if isinstance(contenido, str):
        return contenido

    if isinstance(contenido, list):
        partes = [
            str(bloque.get("text") or "")
            for bloque in contenido
            if isinstance(bloque, dict) and bloque.get("type") in (None, "text")
        ]
        return "".join(partes)

    return ""


def _acumular_citas(
    artifact: Mapping[str, Any],
    fragmentos: list[dict[str, Any]],
    vistas: set[tuple[str, str]],
) -> None:
    """Junta las citas del artifact, sin repetir.

    **Salen del artifact y no del texto del modelo.** Es la misma razón por la
    que el verificador de fundamento contrasta contra lo recuperado: una cita que
    el modelo escribió puede no corresponder a nada que se haya leído.

    El ciclo del verificador puede buscar dos veces y traer la misma sección; la
    consola no debería listarla dos veces.
    """
    for fragmento in artifact.get("fragmentos") or []:
        if not isinstance(fragmento, Mapping):
            continue
        clave = (str(fragmento.get("doc_id") or ""), str(fragmento.get("seccion") or ""))
        if clave in vistas:
            continue
        vistas.add(clave)
        fragmentos.append(
            {
                "doc_id": fragmento.get("doc_id"),
                "seccion": fragmento.get("seccion"),
                "titulo": fragmento.get("titulo"),
                "vigencia": fragmento.get("vigencia", "vigente"),
            }
        )


def _interrupciones(chunk: Any) -> tuple[Any, ...]:
    """Las interrupciones de un chunk del grafo, si las hay."""
    if isinstance(chunk, Mapping):
        valor = chunk.get("__interrupt__")
        if isinstance(valor, Iterable) and not isinstance(valor, str | bytes | Mapping):
            return tuple(valor)
    return ()


def _aprobacion(interrupciones: tuple[Any, ...]) -> dict[str, Any]:
    """Lo que la consola necesita para mostrar el gate.

    Las decisiones permitidas vienen de `review_configs` y no se inventan acá:
    ofrecer «editar» donde la ontología no lo permite es prometer algo que el
    endpoint de aprobación después rechaza.
    """
    acciones: list[dict[str, Any]] = []
    ids: list[str] = []

    for interrupcion in interrupciones:
        ids.append(str(getattr(interrupcion, "id", "")))
        valor = getattr(interrupcion, "value", None)
        if not isinstance(valor, Mapping):
            continue

        decisiones = {
            str(config.get("action_name")): list(config.get("allowed_decisions") or [])
            for config in valor.get("review_configs") or []
            if isinstance(config, Mapping)
        }

        for pedido in valor.get("action_requests") or []:
            if not isinstance(pedido, Mapping):
                continue
            nombre = str(pedido.get("name") or "")
            acciones.append(
                {
                    "herramienta": nombre,
                    "argumentos": pedido.get("args") or {},
                    "descripcion": pedido.get("description") or "",
                    "decisiones": decisiones.get(nombre, []),
                }
            )

    return {"interrupt_ids": ids, "acciones": acciones}


def _thread_id(config: Mapping[str, Any]) -> str | None:
    """El hilo del recorrido. Es lo que la consola necesita para aprobar."""
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        valor = configurable.get("thread_id")
        return str(valor) if valor is not None else None
    return None
