"""Contrato del streaming: qué ve el usuario mientras el agente trabaja.

## El test que sostiene a todos los demás

`test_las_formas_grabadas_siguen_siendo_las_de_la_libreria` corre un grafo de
verdad —modelo falso, herramienta real, gate real— y verifica que produzca los
eventos que el resto de los tests grabaron a mano. Sin él, una versión nueva de
LangGraph podría cambiar la forma de un evento y toda esta suite seguiría en
verde traduciendo algo que ya no existe.

Las formas grabadas no se inventaron: salieron de correr la librería instalada.

Ver docs/plan/fases/F6-api.md § F6.2
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from services.api.streaming import (
    APROBACION_REQUERIDA,
    CITAS,
    ERROR,
    FIN,
    HERRAMIENTA_FIN,
    HERRAMIENTA_INICIO,
    TERMINALES,
    TOKEN,
    Evento,
    eventos,
    flujo_sse,
    sse,
)
from synapseflow.agents.graph import NODO_ACCIONES
from synapseflow.agents.state import AgentState
from synapseflow.governance.middleware import construir_middleware
from synapseflow.llm.fake import FakeChatModel, Llamada, Respuesta
from synapseflow.ontology import get_ontology

ONTOLOGIA = get_ontology()
CONFIG = {"configurable": {"thread_id": "hilo-1"}}

# El namespace del checkpoint que produce un subgrafo corriendo dentro de un
# nodo. Es de donde sale el nombre del agente, porque `langgraph_node` informa
# el nodo *interno* (`model`, `tools`).
NS_ACCIONES = f"{NODO_ACCIONES}:abc123|tools:def456"
NS_NORMATIVA = "normativa:abc123|tools:def456"


class GrafoGrabado:
    """Un grafo que reproduce eventos ya grabados.

    Existe para que los tests de traducción no dependan de un modelo, una base
    ni una red: lo que se verifica acá es la traducción, no LangGraph.
    """

    def __init__(self, *eventos_crudos: dict[str, Any], falla: Exception | None = None) -> None:
        self._eventos = eventos_crudos
        self._falla = falla

    async def astream_events(self, _entrada: Any, _config: Any) -> AsyncIterator[dict[str, Any]]:
        for evento in self._eventos:
            yield evento
        if self._falla is not None:
            raise self._falla


class ToolMessageFalso:
    """Lo que `on_tool_end` trae en `data.output`: content y artifact."""

    def __init__(self, content: str, artifact: dict[str, Any] | None = None) -> None:
        self.content = content
        self.artifact = artifact or {}


class MensajeFalso:
    """Un `AIMessage` para lo que solo se le lee el contenido."""

    def __init__(self, content: Any) -> None:
        self.content = content


class InterrupcionFalsa:
    def __init__(self, valor: dict[str, Any], id_: str = "int-1") -> None:
        self.value = valor
        self.id = id_


def herramienta_inicio(nombre: str, ns: str = NS_ACCIONES, **args: Any) -> dict[str, Any]:
    return {
        "event": "on_tool_start",
        "name": nombre,
        "data": {"input": args},
        "metadata": {"langgraph_node": "tools", "langgraph_checkpoint_ns": ns},
    }


def herramienta_fin(
    nombre: str, contenido: str, artifact: dict[str, Any] | None = None, ns: str = NS_ACCIONES
) -> dict[str, Any]:
    return {
        "event": "on_tool_end",
        "name": nombre,
        "data": {"output": ToolMessageFalso(contenido, artifact)},
        "metadata": {"langgraph_node": "tools", "langgraph_checkpoint_ns": ns},
    }


def modelo_fin(texto: Any, ns: str = NS_ACCIONES, run_id: str = "run-1") -> dict[str, Any]:
    return {
        "event": "on_chat_model_end",
        "name": "modelo",
        "run_id": run_id,
        "data": {"output": MensajeFalso(texto)},
        "metadata": {"langgraph_node": "model", "langgraph_checkpoint_ns": ns},
    }


def modelo_trozo(texto: str, ns: str = NS_ACCIONES, run_id: str = "run-1") -> dict[str, Any]:
    return {
        "event": "on_chat_model_stream",
        "name": "modelo",
        "run_id": run_id,
        "data": {"chunk": MensajeFalso(texto)},
        "metadata": {"langgraph_node": "model", "langgraph_checkpoint_ns": ns},
    }


def gate(nombre: str = "solicitar_parada_equipo", **args: Any) -> dict[str, Any]:
    interrupcion = InterrupcionFalsa(
        {
            "action_requests": [
                {"name": nombre, "args": args, "description": f"[{nombre}] acción irreversible"}
            ],
            "review_configs": [
                {"action_name": nombre, "allowed_decisions": ["approve", "edit", "reject"]}
            ],
        }
    )
    return {
        "event": "on_chain_stream",
        "name": "LangGraph",
        "data": {"chunk": {"__interrupt__": (interrupcion,)}},
        "metadata": {},
    }


async def traducir(*crudos: dict[str, Any], falla: Exception | None = None) -> list[Evento]:
    grafo = GrafoGrabado(*crudos, falla=falla)
    return [e async for e in eventos(grafo, {}, CONFIG)]


def de_tipo(lista: list[Evento], tipo: str) -> list[Evento]:
    return [e for e in lista if e.tipo == tipo]


# ─────────────────────────────────────────────────────────────────────────────
# Lo que el usuario ve mientras espera
# ─────────────────────────────────────────────────────────────────────────────


async def test_las_herramientas_se_anuncian_antes_de_ejecutarse() -> None:
    """**Es el punto del streaming.**

    Un spinner opaco durante veinte segundos es peor experiencia que ver
    «consultando el activo P-2101-A».
    """
    emitidos = await traducir(
        herramienta_inicio("consultar_activo", tag="P-2101-A"),
        herramienta_fin("consultar_activo", "t_min 7,1 mm"),
    )

    tipos = [e.tipo for e in emitidos]
    assert tipos.index(HERRAMIENTA_INICIO) < tipos.index(HERRAMIENTA_FIN)


async def test_el_anuncio_lleva_los_argumentos() -> None:
    """«Consultando un activo» no dice nada; «consultando P-2101-A» sí."""
    emitidos = await traducir(herramienta_inicio("consultar_activo", tag="P-2101-A"))

    assert de_tipo(emitidos, HERRAMIENTA_INICIO)[0].datos["argumentos"] == {"tag": "P-2101-A"}


async def test_el_evento_dice_que_agente_lo_produjo() -> None:
    """**Verificado contra la librería instalada.**

    `langgraph_node` informa el nodo interno del subgrafo —`tools`— porque cada
    especialista es un grafo compilado que corre dentro de un nodo. El nodo
    nuestro está en el primer segmento de `langgraph_checkpoint_ns`.
    """
    emitidos = await traducir(herramienta_inicio("buscar_normativa", ns=NS_NORMATIVA, consulta="x"))

    assert de_tipo(emitidos, HERRAMIENTA_INICIO)[0].datos["agente"] == "normativa"


async def test_el_fin_de_herramienta_lleva_el_content_y_no_solo_el_artifact() -> None:
    """El `content` es lo que el modelo leyó, y por lo tanto lo que explica su
    respuesta. El artifact viaja aparte, como detalle."""
    emitidos = await traducir(
        herramienta_fin(
            "calcular_vida_remanente",
            "vida remanente: −1,43 años",
            {"analisis": {"velocidad_mm_anio": 0.21}},
        )
    )

    datos = de_tipo(emitidos, HERRAMIENTA_FIN)[0].datos
    assert datos["contenido"] == "vida remanente: −1,43 años"
    assert datos["detalle"]["analisis"]["velocidad_mm_anio"] == 0.21


# ─────────────────────────────────────────────────────────────────────────────
# El texto de la respuesta
# ─────────────────────────────────────────────────────────────────────────────


async def test_solo_el_nodo_final_produce_texto_para_el_usuario() -> None:
    """**Los demás nodos razonan; su texto no es una respuesta.**

    El supervisor rutea y los especialistas resumen para el siguiente. Emitir
    todo eso mostraría al usuario cuatro respuestas parciales y contradictorias
    antes de la buena.
    """
    emitidos = await traducir(
        modelo_fin("voy a buscar normativa", ns=NS_NORMATIVA, run_id="run-normativa"),
        modelo_fin("El activo NO está apto.", run_id="run-final"),
    )

    assert [e.datos["texto"] for e in de_tipo(emitidos, TOKEN)] == ["El activo NO está apto."]


async def test_el_texto_llega_entero_cuando_el_modelo_no_emite_trozos() -> None:
    """**`create_agent` invoca el modelo con `ainvoke`, no con `astream`.**

    Está en `langchain/agents/factory.py` de la versión instalada. Si el
    traductor solo escuchara `on_chat_model_stream`, la consola no mostraría
    nunca la respuesta — y nada fallaría.
    """
    emitidos = await traducir(modelo_fin("El activo NO está apto."))

    assert [e.datos["texto"] for e in de_tipo(emitidos, TOKEN)] == ["El activo NO está apto."]


async def test_un_modelo_que_emite_trozos_no_se_repite_al_cerrar() -> None:
    """El día que el modelo emita trozos, el cierre no puede repetir todo.

    La consola habría pintado la respuesta dos veces: una de a poco y otra de
    golpe.
    """
    emitidos = await traducir(
        modelo_trozo("El activo ", run_id="run-final"),
        modelo_trozo("NO está apto.", run_id="run-final"),
        modelo_fin("El activo NO está apto.", run_id="run-final"),
    )

    assert [e.datos["texto"] for e in de_tipo(emitidos, TOKEN)] == ["El activo ", "NO está apto."]


async def test_el_texto_se_lee_igual_venga_como_cadena_o_como_bloques() -> None:
    """LangChain 1.x admite las dos formas y los proveedores no coinciden.

    Leer `.content` a secas funciona hasta que un proveedor devuelve bloques, y
    entonces la consola muestra la repr de una lista de diccionarios.
    """
    emitidos = await traducir(
        modelo_fin(
            [{"type": "text", "text": "El activo "}, {"type": "text", "text": "NO está apto."}]
        )
    )

    assert de_tipo(emitidos, TOKEN)[0].datos["texto"] == "El activo NO está apto."


async def test_un_turno_sin_texto_no_emite_un_token_vacio() -> None:
    """El mensaje que solo invoca herramientas tiene contenido vacío."""
    emitidos = await traducir(modelo_fin(""))

    assert de_tipo(emitidos, TOKEN) == []


# ─────────────────────────────────────────────────────────────────────────────
# Las citas
# ─────────────────────────────────────────────────────────────────────────────


async def test_las_citas_salen_del_artifact_y_no_del_texto() -> None:
    """Es la misma razón por la que el verificador contrasta contra lo
    recuperado: una cita que el modelo escribió puede no corresponder a nada."""
    emitidos = await traducir(
        herramienta_fin(
            "buscar_normativa",
            "API 570 §7.4 exige retirar de servicio…",
            {"fragmentos": [{"doc_id": "API-570-2016", "seccion": "7.4", "titulo": "Espesor"}]},
            ns=NS_NORMATIVA,
        ),
        modelo_fin("El activo NO está apto [API-570-2016 §7.4]."),
    )

    citas = de_tipo(emitidos, CITAS)[0].datos["citas"]
    assert citas == [
        {
            "doc_id": "API-570-2016",
            "seccion": "7.4",
            "titulo": "Espesor",
            "vigencia": "vigente",
        }
    ]


async def test_una_seccion_recuperada_dos_veces_se_lista_una_sola() -> None:
    """El ciclo del verificador puede buscar de nuevo y traer lo mismo."""
    fragmento = {"doc_id": "API-570-2016", "seccion": "7.4", "titulo": "Espesor"}
    emitidos = await traducir(
        herramienta_fin("buscar_normativa", "…", {"fragmentos": [fragmento]}, ns=NS_NORMATIVA),
        herramienta_fin("buscar_normativa", "…", {"fragmentos": [fragmento]}, ns=NS_NORMATIVA),
    )

    assert len(de_tipo(emitidos, CITAS)[0].datos["citas"]) == 1


async def test_la_vigencia_viaja_con_la_cita() -> None:
    """Un documento derogado citado sin decirlo es peor que no citar nada."""
    emitidos = await traducir(
        herramienta_fin(
            "buscar_normativa",
            "…",
            {"fragmentos": [{"doc_id": "PROC-INT-009", "seccion": "3.1", "vigencia": "derogado"}]},
            ns=NS_NORMATIVA,
        )
    )

    assert de_tipo(emitidos, CITAS)[0].datos["citas"][0]["vigencia"] == "derogado"


async def test_sin_recuperacion_no_se_emite_el_evento_de_citas() -> None:
    """Un evento de citas vacío le haría dibujar a la consola una sección
    «Fuentes» sin fuentes."""
    emitidos = await traducir(modelo_fin("No encontré fundamento normativo."))

    assert de_tipo(emitidos, CITAS) == []


# ─────────────────────────────────────────────────────────────────────────────
# El gate
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_gate_se_anuncia_con_la_accion_y_sus_argumentos() -> None:
    """Aprobar «una parada» no es aprobar nada: hay que ver cuál."""
    emitidos = await traducir(gate(tag="P-2101-A", motivo="espesor bajo t_min"))

    acciones = de_tipo(emitidos, APROBACION_REQUERIDA)[0].datos["acciones"]
    assert acciones[0]["herramienta"] == "solicitar_parada_equipo"
    assert acciones[0]["argumentos"] == {"tag": "P-2101-A", "motivo": "espesor bajo t_min"}


async def test_las_decisiones_ofrecidas_son_las_que_permite_la_ontologia() -> None:
    """Ofrecer «editar» donde no se permite es prometer algo que el endpoint de
    aprobación después rechaza."""
    emitidos = await traducir(gate(tag="P-2101-A"))

    acciones = de_tipo(emitidos, APROBACION_REQUERIDA)[0].datos["acciones"]
    assert acciones[0]["decisiones"] == ["approve", "edit", "reject"]


async def test_las_citas_se_emiten_antes_que_la_aprobacion() -> None:
    """**Pedir una aprobación antes de mostrar el fundamento es pedirla a ciegas.**

    El gate llega a mitad del flujo y las citas solo se conocen al final, así que
    el orden de emisión no puede ser el orden de llegada.
    """
    emitidos = await traducir(
        herramienta_fin(
            "buscar_normativa",
            "…",
            {"fragmentos": [{"doc_id": "API-570-2016", "seccion": "7.4"}]},
            ns=NS_NORMATIVA,
        ),
        gate(tag="P-2101-A"),
    )

    tipos = [e.tipo for e in emitidos]
    assert tipos.index(CITAS) < tipos.index(APROBACION_REQUERIDA)


async def test_sin_gate_no_hay_evento_de_aprobacion() -> None:
    emitidos = await traducir(modelo_fin("El activo está apto."))

    assert de_tipo(emitidos, APROBACION_REQUERIDA) == []


# ─────────────────────────────────────────────────────────────────────────────
# El final del flujo
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_flujo_termina_siempre_con_un_evento_terminal() -> None:
    """Una consola que espera un final no puede quedarse colgada."""
    emitidos = await traducir(modelo_fin("listo"))

    assert emitidos[-1].tipo in TERMINALES


async def test_un_error_a_mitad_del_flujo_es_un_evento_y_no_un_500() -> None:
    """**El 200 ya salió con la primera línea.**

    Levantar la excepción dejaría a la consola con una respuesta truncada y
    ningún motivo, porque el status ya no se puede cambiar.
    """
    emitidos = await traducir(
        herramienta_inicio("consultar_activo", tag="P-2101-A"),
        falla=RuntimeError("Firestore no responde"),
    )

    assert emitidos[-1].tipo == ERROR
    assert "Firestore no responde" in emitidos[-1].datos["error"]


async def test_un_error_no_agrega_un_fin_ademas() -> None:
    """Exactamente un evento terminal: dos estados finales son dos caminos en la
    consola, y uno de los dos no se prueba nunca."""
    emitidos = await traducir(modelo_fin("x"), falla=RuntimeError("boom"))

    assert len([e for e in emitidos if e.tipo in TERMINALES]) == 1


async def test_lo_emitido_antes_del_error_no_se_pierde() -> None:
    """El usuario vio al agente consultar el activo; negarlo después confunde
    más que el error."""
    emitidos = await traducir(
        herramienta_inicio("consultar_activo", tag="P-2101-A"),
        falla=RuntimeError("boom"),
    )

    assert de_tipo(emitidos, HERRAMIENTA_INICIO)


async def test_el_fin_lleva_el_hilo_para_poder_aprobar_despues() -> None:
    """Cuando el hilo lo genera el servidor, la consola no lo conoce."""
    emitidos = await traducir(modelo_fin("listo"))

    assert emitidos[-1].datos["thread_id"] == "hilo-1"


# ─────────────────────────────────────────────────────────────────────────────
# El formato SSE
# ─────────────────────────────────────────────────────────────────────────────


def test_un_bloque_sse_lleva_tipo_datos_y_linea_en_blanco() -> None:
    """La línea en blanco es lo que cierra el bloque: sin ella el navegador
    espera indefinidamente el resto del evento."""
    texto = sse(Evento(tipo=TOKEN, datos={"texto": "hola"}))

    assert texto.startswith("event: token\n")
    assert texto.endswith("\n\n")
    assert json.loads(texto.split("data: ", 1)[1].strip()) == {"texto": "hola"}


def test_un_salto_de_linea_en_el_texto_no_parte_el_bloque() -> None:
    """**Es la forma más fácil de romper un protocolo de texto.**

    JSON escapa los saltos dentro de las cadenas, así que el bloque sigue siendo
    una sola línea `data:`.
    """
    texto = sse(Evento(tipo=TOKEN, datos={"texto": "línea 1\nlínea 2"}))

    assert len([linea for linea in texto.splitlines() if linea.startswith("data:")]) == 1
    assert json.loads(texto.split("data: ", 1)[1].strip())["texto"] == "línea 1\nlínea 2"


def test_los_acentos_no_se_escapan() -> None:
    """El dominio es en español: escapar cada acento triplica el flujo."""
    assert "inspección" in sse(Evento(tipo=TOKEN, datos={"texto": "inspección"}))


async def test_el_flujo_arranca_con_un_comentario() -> None:
    """Los proxies retienen la respuesta hasta juntar un buffer. Con el
    comentario las cabeceras salen enseguida y el usuario ve que algo pasa."""

    async def fuente() -> AsyncIterator[Evento]:
        yield Evento(tipo=FIN, datos={})

    trozos = [t async for t in flujo_sse(fuente())]

    assert trozos[0].startswith(":")


# ─────────────────────────────────────────────────────────────────────────────
# Contra la librería de verdad
# ─────────────────────────────────────────────────────────────────────────────


async def test_las_formas_grabadas_siguen_siendo_las_de_la_libreria() -> None:
    """**El test que sostiene a todos los de arriba.**

    Los demás traducen eventos grabados a mano. Si LangGraph cambiara la forma de
    un evento, seguirían en verde traduciendo algo que ya no existe. Este corre
    un grafo real —modelo falso, herramienta real con `content_and_artifact`,
    gate derivado del YAML— y verifica que salga lo mismo.

    Es un grafo mínimo y no el del proyecto porque el real necesita Firestore; el
    recorrido completo ya está cubierto contra el emulador en F5.
    """

    class Args(BaseModel):
        tag: str
        motivo: str

    async def _parar(tag: str, motivo: str) -> tuple[str, dict[str, Any]]:
        return f"parada solicitada para {tag}", {
            "fragmentos": [{"doc_id": "API-570", "seccion": "7.4"}]
        }

    parada = StructuredTool(
        name="solicitar_parada_equipo",
        description="Solicita la parada de un equipo.",
        args_schema=Args,
        coroutine=_parar,
        func=None,
        response_format="content_and_artifact",
    )

    modelo = FakeChatModel(
        respuestas=[
            Respuesta(
                texto="El activo NO está apto [API-570 §7.4]. Propongo la parada.",
                herramientas=[
                    Llamada(
                        nombre="solicitar_parada_equipo",
                        argumentos={"tag": "P-2101-A", "motivo": "espesor bajo t_min"},
                    )
                ],
            )
        ],
        ciclico=True,
    )

    agente = create_agent(
        model=modelo,
        tools=[parada],
        middleware=construir_middleware(ONTOLOGIA, "inspector"),
    )

    async def nodo_acciones(estado: AgentState) -> dict[str, Any]:
        previos = list(estado.get("messages") or [])
        resultado = await agente.ainvoke({"messages": previos})
        return {"messages": list(resultado["messages"])[len(previos) :]}

    constructor: Any = StateGraph(AgentState)
    constructor.add_node(NODO_ACCIONES, nodo_acciones)
    constructor.add_edge(START, NODO_ACCIONES)
    constructor.add_edge(NODO_ACCIONES, END)
    grafo = constructor.compile(checkpointer=InMemorySaver())

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    emitidos = [
        e
        async for e in eventos(grafo, {"messages": [{"role": "user", "content": "¿apto?"}]}, config)
    ]

    tipos = [e.tipo for e in emitidos]
    assert TOKEN in tipos, "el texto del nodo final no llegó: cambió on_chat_model_end"
    assert APROBACION_REQUERIDA in tipos, "el gate no se detectó: cambió la forma del interrupt"
    assert tipos[-1] == FIN

    texto = "".join(e.datos["texto"] for e in de_tipo(emitidos, TOKEN))
    assert "NO está apto" in texto

    propuesta = de_tipo(emitidos, APROBACION_REQUERIDA)[0].datos["acciones"][0]
    assert propuesta["herramienta"] == "solicitar_parada_equipo"
    assert propuesta["argumentos"]["tag"] == "P-2101-A"


async def test_el_gate_frena_antes_de_ejecutar_la_accion() -> None:
    """Lo que hace real al freno, visto desde el streaming.

    Si la herramienta se hubiera ejecutado habría un `herramienta_fin` de la
    parada: el gate se dispararía después del efecto, que no es un gate.
    """

    class Args(BaseModel):
        tag: str
        motivo: str

    ejecutada = False

    async def _parar(tag: str, motivo: str) -> tuple[str, dict[str, Any]]:
        nonlocal ejecutada
        ejecutada = True
        return "parada solicitada", {}

    parada = StructuredTool(
        name="solicitar_parada_equipo",
        description="Solicita la parada de un equipo.",
        args_schema=Args,
        coroutine=_parar,
        func=None,
        response_format="content_and_artifact",
    )

    modelo = FakeChatModel(
        respuestas=[
            Respuesta(
                texto="Propongo la parada.",
                herramientas=[
                    Llamada(
                        nombre="solicitar_parada_equipo",
                        argumentos={"tag": "P-2101-A", "motivo": "espesor bajo t_min"},
                    )
                ],
            )
        ],
        ciclico=True,
    )

    agente = create_agent(
        model=modelo,
        tools=[parada],
        middleware=construir_middleware(ONTOLOGIA, "inspector"),
        checkpointer=InMemorySaver(),
    )

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    emitidos = [
        e
        async for e in eventos(agente, {"messages": [{"role": "user", "content": "parar"}]}, config)
    ]

    assert not ejecutada, "la parada se ejecutó: el gate llegó tarde"
    assert APROBACION_REQUERIDA in [e.tipo for e in emitidos]
    assert "solicitar_parada_equipo" not in [
        e.datos["herramienta"] for e in de_tipo(emitidos, HERRAMIENTA_FIN)
    ]


@pytest.mark.parametrize("tipo", [TOKEN, HERRAMIENTA_INICIO, HERRAMIENTA_FIN, CITAS, FIN])
def test_todo_evento_del_contrato_se_serializa(tipo: str) -> None:
    """Un tipo que no serializa rompe el flujo entero a mitad de camino."""
    assert sse(Evento(tipo=tipo, datos={"x": 1})).startswith(f"event: {tipo}\n")


# ─────────────────────────────────────────────────────────────────────────────
# El endpoint
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def cliente() -> Any:
    import httpx

    from services.api.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as cliente:
        yield cliente


@pytest.fixture
def grafo_grabado(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Reemplaza el grafo por uno grabado y resuelve la identidad.

    El endpoint se prueba sin Firestore ni proveedor porque lo que se verifica es
    el transporte: que exija identidad, que devuelva el content-type correcto y
    que informe el hilo. El grafo ya se verifica en los tests de arriba.
    """
    from services.api import main
    from services.api.auth import usuario_actual
    from synapseflow.governance.rbac import ExecutionContext

    grabado = GrafoGrabado(
        herramienta_inicio("consultar_activo", tag="P-2101-A"),
        modelo_fin("El activo NO está apto."),
    )
    monkeypatch.setattr(main, "grafo_para", lambda _ctx: grabado)

    async def _inspector() -> ExecutionContext:
        return ExecutionContext(usuario="uid-1", rol="inspector")

    main.app.dependency_overrides[usuario_actual] = _inspector
    yield grabado
    main.app.dependency_overrides.clear()


async def test_consultar_sin_identidad_es_401(cliente: Any) -> None:
    """**Es el endpoint que ejecuta acciones.**

    Sin identidad correría con la del servicio, que puede todo.
    """
    respuesta = await cliente.post("/api/consultas", json={"pregunta": "¿P-2101-A sigue apto?"})

    assert respuesta.status_code == 401


async def test_una_pregunta_vacia_se_rechaza(cliente: Any, grafo_grabado: Any) -> None:
    """Arranca un recorrido completo del grafo, con su costo, para nada."""
    respuesta = await cliente.post("/api/consultas", json={"pregunta": ""})

    assert respuesta.status_code == 422


async def test_la_respuesta_es_un_flujo_sse(cliente: Any, grafo_grabado: Any) -> None:
    respuesta = await cliente.post("/api/consultas", json={"pregunta": "¿sigue apto?"})

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/event-stream")


async def test_el_hilo_viaja_en_una_cabecera(cliente: Any, grafo_grabado: Any) -> None:
    """Cuando lo genera el servidor la consola no lo conoce, y sin él no puede
    aprobar el gate que este mismo recorrido puede abrir."""
    respuesta = await cliente.post("/api/consultas", json={"pregunta": "¿sigue apto?"})

    assert respuesta.headers["x-thread-id"]


async def test_el_hilo_pedido_se_respeta(cliente: Any, grafo_grabado: Any) -> None:
    """Es lo que permite continuar una conversación tras recargar la página."""
    respuesta = await cliente.post(
        "/api/consultas", json={"pregunta": "¿y ahora?", "thread_id": "hilo-existente"}
    )

    assert respuesta.headers["x-thread-id"] == "hilo-existente"


async def test_el_cuerpo_trae_los_eventos_traducidos(cliente: Any, grafo_grabado: Any) -> None:
    """Y el último es el terminal: es lo que le dice a la consola que terminó,
    en lugar de dejarla deducirlo de un socket cerrado."""
    respuesta = await cliente.post("/api/consultas", json={"pregunta": "¿sigue apto?"})

    tipos = [
        linea.removeprefix("event: ")
        for linea in respuesta.text.splitlines()
        if linea.startswith("event: ")
    ]

    assert HERRAMIENTA_INICIO in tipos
    assert TOKEN in tipos
    assert tipos[-1] == FIN


async def test_la_respuesta_pide_que_no_se_almacene_en_buffer(
    cliente: Any, grafo_grabado: Any
) -> None:
    """Sin esto nginx junta la respuesta y la entrega al final: el streaming
    queda técnicamente correcto y prácticamente inexistente."""
    respuesta = await cliente.post("/api/consultas", json={"pregunta": "¿sigue apto?"})

    assert respuesta.headers["x-accel-buffering"] == "no"
    assert "no-cache" in respuesta.headers["cache-control"]
