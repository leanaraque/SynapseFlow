"""El recorrido que atraviesa toda la documentación del proyecto.

    «El P-2101-A midió 6,8 mm en la última inspección. ¿Sigue apto?»

      → consultar_activo(P-2101-A)          t_min = 7,1 mm · criticidad A
      → calcular_vida_remanente(P-2101-A)   Python, no el modelo
      → buscar_normativa(...)               con documento y sección
      → solicitar_parada_equipo(P-2101-A)   ⚠ FRENA acá, esperando a un humano

**Es el test que hace verdadero lo que el README promete.** Corre el grafo real
contra el emulador, con el modelo falso programado y los datos del caso crítico
sembrados, y verifica que se detenga en el gate en lugar de ejecutar la parada.

Ver docs/plan/fases/F5-grafo.md § F5.6
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from synapseflow.agents.graph import construir_grafo
from synapseflow.agents.supervisor import Ruteo
from synapseflow.config import Provider, Settings
from synapseflow.domain import lecturas
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.fake import FakeChatModel, Llamada, Respuesta
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import get_ontology
from synapseflow.persistence.client import Collections, get_client
from synapseflow.persistence.vectorstore import FirestoreVectorStore
from synapseflow.rag.fundamento import Afirmacion, Dictamen

pytestmark = pytest.mark.emulator

ONTOLOGIA = get_ontology()
TAG = "P-2101-A"
T_MIN = 7.1
ESPESOR_FINAL = 6.8

# Cuatro campañas entre 2019 y 2026, con velocidad 0,21 mm/año. Son los números
# que publica la transcripción del README.
MEDICIONES = (
    ("2019-04-15", 8.24),
    ("2021-05-20", 7.79),
    ("2023-07-11", 7.35),
    ("2026-02-18", ESPESOR_FINAL),
)

PREGUNTA = f"El {TAG} midió {ESPESOR_FINAL} mm en la última inspección. ¿Sigue apto?"

RESPUESTA_FINAL = (
    f"El activo NO está apto para continuar en servicio. El espesor medido "
    f"({ESPESOR_FINAL} mm) está por debajo del mínimo requerido ({T_MIN} mm) "
    f"[API-570-2016 §7.4]."
)


@pytest.fixture
def inspector() -> ExecutionContext:
    return ExecutionContext(usuario="uid-inspector", rol="inspector", thread_id=uuid.uuid4().hex)


@pytest.fixture
def ajustes() -> Settings:
    return Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE)


@pytest.fixture
async def dominio_sembrado(
    requiere_emulador: None, ajustes: Settings, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[str]:
    """El caso P-2101-A en la base, y el fundamento normativo indexado."""
    cliente = get_client()
    puerta = Gateway(settings=ajustes)
    monkeypatch.setattr(lecturas, "_GATEWAY", puerta)

    escritos: list[tuple[str, str]] = [(Collections.ASSETS, TAG)]

    await (
        cliente.collection(Collections.ASSETS)
        .document(TAG)
        .set(
            {
                "tag": TAG,
                "descripcion": "Línea de proceso de crudo",
                "clase": "canieria_proceso",
                "instalacion": "BAT-LC-014",
                "criticidad": "A",
                "estado": "en_servicio",
                "fluido": "crudo",
                "espesor_nominal_mm": 9.5,
                "espesor_minimo_requerido_mm": T_MIN,
            }
        )
    )

    for fecha, espesor in MEDICIONES:
        id_insp = f"INS-{TAG}-{fecha}"
        escritos.append((Collections.INSPECTIONS, id_insp))
        await (
            cliente.collection(Collections.INSPECTIONS)
            .document(id_insp)
            .set(
                {
                    "id_inspeccion": id_insp,
                    "activo": TAG,
                    "fecha": fecha,
                    "espesor_medido_mm": espesor,
                    "tecnica": "ultrasonido",
                    "hallazgo": "adelgazamiento generalizado",
                    "severidad": "mayor",
                    "inspector_legajo": "LEG-00042",
                }
            )
        )

    almacen = FirestoreVectorStore(puerta.embeddings())
    ids = await almacen.aadd_texts(
        [
            "Un componente cuyo espesor sea inferior al mínimo requerido se retira "
            "de servicio o se somete a evaluación de aptitud según API 579."
        ],
        [
            {
                "doc_id": "API-570-2016",
                "titulo": "Inspección de cañerías en servicio",
                "seccion": "7.4",
                "tipo_documento": "codigo_api",
                "vigencia": "vigente",
            }
        ],
    )
    escritos.extend((Collections.CORPUS_CHUNKS, i) for i in ids)

    yield TAG

    for coleccion, id_doc in escritos:
        await cliente.collection(coleccion).document(id_doc).delete()


def modelo_del_recorrido() -> FakeChatModel:
    """El modelo programado para conducir el grafo hasta el gate.

    Las dos colas se consumen por separado: `estructurados` la usan el supervisor
    y el verificador; `respuestas`, los agentes. Que estén desacopladas es lo que
    permite programar un recorrido multi-nodo — con un solo contador, cada agente
    recibiría la respuesta del anterior.
    """
    return FakeChatModel(
        estructurados=[
            Ruteo(destino="datos"),
            Ruteo(destino="calculo"),
            Ruteo(destino="normativa"),
            Dictamen(afirmaciones=[Afirmacion(texto=RESPUESTA_FINAL, respaldada=True)]),
        ],
        respuestas=[
            # datos
            Respuesta(herramientas=[Llamada(nombre="consultar_activo", argumentos={"tag": TAG})]),
            Respuesta(texto=f"{TAG}: t_min {T_MIN} mm, criticidad A, en servicio."),
            # calculo
            Respuesta(
                herramientas=[Llamada(nombre="calcular_vida_remanente", argumentos={"tag": TAG})]
            ),
            Respuesta(texto="Vida remanente negativa."),
            # normativa
            Respuesta(
                herramientas=[
                    Llamada(
                        nombre="buscar_normativa",
                        argumentos={"consulta": "espesor por debajo del mínimo requerido"},
                    )
                ]
            ),
            Respuesta(texto=f"API 570 §7.4 exige el retiro de servicio. {RESPUESTA_FINAL}"),
            # acciones: propone la parada. El gate la frena antes de ejecutarla.
            Respuesta(
                texto=RESPUESTA_FINAL,
                herramientas=[
                    Llamada(
                        nombre="solicitar_parada_equipo",
                        argumentos={
                            "tag": TAG,
                            "motivo": "espesor por debajo de t_min",
                            "id_inspeccion": f"INS-{TAG}-2026-02-18",
                        },
                    )
                ],
            ),
        ],
    )


# ─────────────────────────────────────────────────────────────────────────────
# El recorrido
# ─────────────────────────────────────────────────────────────────────────────


async def test_caso_p2101a_llega_al_gate_de_parada(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """**El recorrido completo, y se detiene donde tiene que detenerse.**

    El agente no ejecuta la parada: la propone, la fundamenta y espera a un
    humano. Es la promesa central del proyecto, ejercitada de punta a punta.
    """
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    resultado = await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    assert "__interrupt__" in resultado, (
        "el grafo terminó sin frenar: una acción irreversible se habría ejecutado "
        "sin que ninguna persona la aprobara"
    )


async def test_la_accion_frenada_es_la_parada_del_activo_correcto(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """No alcanza con que frene: tiene que frenar lo que corresponde.

    Un gate que se dispara sobre otra acción, o sobre otro activo, es un freno
    que no protege de nada.
    """
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    resultado = await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    texto = str(resultado["__interrupt__"])
    assert "solicitar_parada_equipo" in texto
    assert TAG in texto


async def test_la_parada_no_se_materializo(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """**Lo que hace real al freno.**

    El estado del activo en Firestore tiene que seguir siendo `en_servicio`: si
    la acción se ejecutó y después se frenó, el gate no sirve de nada.
    """
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    documento = await get_client().collection(Collections.ASSETS).document(TAG).get()
    assert documento.to_dict()["estado"] == "en_servicio", (
        "el activo cambió de estado: la parada se ejecutó antes del gate"
    )


# ─────────────────────────────────────────────────────────────────────────────
# El número viene de Python, no del modelo
# ─────────────────────────────────────────────────────────────────────────────


async def test_la_vida_remanente_sale_del_calculo_y_no_del_texto(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """**El compromiso 3, verificado sobre el recorrido real.**

    El estado lleva el número que devolvió la función determinística. El modelo
    nunca lo escribió: en su respuesta dice «vida remanente negativa», sin cifra.
    """
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    resultado = await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    calculos = resultado.get("calculos") or {}
    assert calculos.get("vida_remanente_anios") is not None
    assert calculos["vida_remanente_anios"] < 0, "el caso crítico da vida remanente negativa"
    assert calculos["velocidad_mm_anio"] == pytest.approx(0.21, abs=0.02)
    assert calculos["apto"] is False


async def test_el_fundamento_recuperado_queda_en_el_estado(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """Con documento y sección: es contra eso que el verificador contrasta."""
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    resultado = await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    recuperados = resultado.get("recuperados") or []
    assert recuperados, "el agente de normativa no dejó fundamento en el estado"
    assert all(d.metadata.get("doc_id") and d.metadata.get("seccion") for d in recuperados)


async def test_el_legajo_del_inspector_nunca_llego_al_modelo(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """**El compromiso 5, sobre el recorrido completo.**

    Las inspecciones sembradas llevan `LEG-00042`. El historial pasó por el
    agente de datos, así que si la redacción fallara, el legajo estaría en lo que
    el modelo vio.
    """
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    assert "LEG-00042" not in modelo.texto_recibido


# ─────────────────────────────────────────────────────────────────────────────
# El supervisor orquestó los tres
# ─────────────────────────────────────────────────────────────────────────────


async def test_se_consultaron_los_tres_especialistas_en_orden(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """datos → cálculo → normativa.

    El cálculo necesita el historial que trae datos, y el fundamento se busca
    sabiendo ya qué hay que fundamentar.
    """
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    resultado = await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    assert resultado.get("especialistas_consultados") == ["datos", "calculo", "normativa"]


async def test_el_veredicto_quedo_registrado(
    dominio_sembrado: str, inspector: ExecutionContext, ajustes: Settings
) -> None:
    """El verificador corrió antes de que la respuesta llegara al nodo de acciones."""
    modelo = modelo_del_recorrido()
    grafo = construir_grafo(
        ONTOLOGIA,
        inspector,
        gateway=Gateway(settings=ajustes, falso=modelo),
        settings=ajustes,
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": inspector.thread_id}}

    resultado = await grafo.ainvoke({"messages": [{"role": "user", "content": PREGUNTA}]}, config)

    assert resultado.get("veredicto") == "fundamentada"
