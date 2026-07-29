"""Tests de contrato del checkpointer de Firestore.

El test que importa es `test_hitl_sobrevive_a_la_muerte_del_proceso`. Los demás
verifican el contrato de `BaseCheckpointSaver`; ese verifica la promesa que
justifica que el checkpointer exista.

Ver docs/adr/0005-hitl-con-interrupt-de-langgraph.md
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from synapseflow.persistence.checkpointer import FirestoreSaver

pytestmark = pytest.mark.emulator


# ─────────────────────────────────────────────────────────────────────────────
# Contrato básico
# ─────────────────────────────────────────────────────────────────────────────


async def test_put_y_get_devuelven_el_mismo_estado(
    saver: FirestoreSaver, thread_id: str
) -> None:
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = {
        "v": 4,
        "id": "1f000000-0000-6000-8000-000000000001",
        "ts": "2026-07-29T12:00:00+00:00",
        "channel_values": {"mensajes": ["hola"], "contador": 3},
        "channel_versions": {"mensajes": "1", "contador": "1"},
        "versions_seen": {},
        "updated_channels": None,
    }
    metadata = {"source": "loop", "step": 1, "parents": {}}

    nuevo_config = await saver.aput(config, checkpoint, metadata, {"mensajes": "1", "contador": "1"})
    assert nuevo_config["configurable"]["checkpoint_id"] == checkpoint["id"]

    tupla = await saver.aget_tuple(nuevo_config)
    assert tupla is not None
    assert tupla.checkpoint["id"] == checkpoint["id"]
    # Lo central: los valores de canal se reconstruyen desde los blobs.
    assert tupla.checkpoint["channel_values"] == {"mensajes": ["hola"], "contador": 3}
    assert tupla.metadata["source"] == "loop"
    assert tupla.metadata["step"] == 1


async def test_get_tuple_sin_checkpoint_id_devuelve_el_ultimo(
    saver: FirestoreSaver, thread_id: str
) -> None:
    """Sin checkpoint_id explícito, el saver tiene que devolver el más reciente.

    Es el camino que usa LangGraph al reanudar un hilo sin decir desde dónde.
    """
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    for n, ckpt_id in enumerate(
        [
            "1f000000-0000-6000-8000-00000000000a",
            "1f000000-0000-6000-8000-00000000000b",
            "1f000000-0000-6000-8000-00000000000c",
        ],
        start=1,
    ):
        await saver.aput(
            config,
            {
                "v": 4,
                "id": ckpt_id,
                "ts": f"2026-07-29T12:0{n}:00+00:00",
                "channel_values": {"paso": n},
                "channel_versions": {"paso": str(n)},
                "versions_seen": {},
                "updated_channels": None,
            },
            {"source": "loop", "step": n, "parents": {}},
            {"paso": str(n)},
        )

    tupla = await saver.aget_tuple(config)
    assert tupla is not None
    assert tupla.checkpoint["channel_values"]["paso"] == 3, "no devolvió el último checkpoint"


async def test_alist_ordena_del_mas_reciente_al_mas_antiguo(
    saver: FirestoreSaver, thread_id: str
) -> None:
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    for n, sufijo in enumerate("abcd", start=1):
        await saver.aput(
            config,
            {
                "v": 4,
                "id": f"1f000000-0000-6000-8000-00000000001{sufijo}",
                "ts": f"2026-07-29T12:0{n}:00+00:00",
                "channel_values": {"paso": n},
                "channel_versions": {"paso": str(n)},
                "versions_seen": {},
                "updated_channels": None,
            },
            {"source": "loop", "step": n, "parents": {}},
            {"paso": str(n)},
        )

    pasos = [t.checkpoint["channel_values"]["paso"] async for t in saver.alist(config)]
    assert pasos == [4, 3, 2, 1]

    limitados = [t async for t in saver.alist(config, limit=2)]
    assert len(limitados) == 2


async def test_adelete_thread_borra_todo_el_estado(
    saver: FirestoreSaver, thread_id: str
) -> None:
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    ckpt_id = "1f000000-0000-6000-8000-0000000000ff"
    nuevo = await saver.aput(
        config,
        {
            "v": 4,
            "id": ckpt_id,
            "ts": "2026-07-29T12:00:00+00:00",
            "channel_values": {"x": 1},
            "channel_versions": {"x": "1"},
            "versions_seen": {},
            "updated_channels": None,
        },
        {"source": "loop", "step": 1, "parents": {}},
        {"x": "1"},
    )
    await saver.aput_writes(nuevo, [("canal", {"dato": 1})], "tarea-1")

    assert await saver.aget_tuple(nuevo) is not None
    await saver.adelete_thread(thread_id)
    assert await saver.aget_tuple(nuevo) is None


async def test_el_camino_sincronico_falla_con_mensaje_claro(saver: FirestoreSaver) -> None:
    """Bloquear el event loop sería peor que fallar."""
    with pytest.raises(NotImplementedError, match="async"):
        saver.get_tuple({"configurable": {"thread_id": "x"}})
    with pytest.raises(NotImplementedError, match="async"):
        saver.put({"configurable": {"thread_id": "x"}}, {}, {}, {})  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# La promesa del ADR-0005
# ─────────────────────────────────────────────────────────────────────────────


class EstadoParada(TypedDict):
    tag: str
    motivo: str
    aprobado: bool | None
    ejecutado: dict[str, Any] | None


def _construir_grafo(saver: FirestoreSaver) -> Any:
    """Grafo mínimo con la forma del flujo real: propone, se detiene, ejecuta.

    Reproduce la estructura de `solicitar_parada_equipo`: un nodo arma la
    propuesta, un gate espera a un humano, y un tercer nodo materializa el efecto
    solo si hubo aprobación.
    """

    def proponer(estado: EstadoParada) -> dict[str, Any]:
        return {"tag": "P-2101-A", "motivo": "espesor medido 6,8 mm bajo t_min 7,1 mm"}

    def gate(estado: EstadoParada) -> dict[str, Any]:
        decision = interrupt(
            {
                "accion": "solicitar_parada_equipo",
                "tag": estado["tag"],
                "motivo": estado["motivo"],
            }
        )
        return {"aprobado": bool(decision.get("aprobado"))}

    def ejecutar(estado: EstadoParada) -> dict[str, Any]:
        if not estado["aprobado"]:
            return {"ejecutado": None}
        # En el sistema real acá se escribe la solicitud de parada. Lo que el
        # test verifica es que los argumentos que llegan son los que se
        # propusieron, sin haber pasado por una reinterpretación del modelo.
        return {"ejecutado": {"tag": estado["tag"], "motivo": estado["motivo"]}}

    grafo = StateGraph(EstadoParada)
    grafo.add_node("proponer", proponer)
    grafo.add_node("gate", gate)
    grafo.add_node("ejecutar", ejecutar)
    grafo.add_edge(START, "proponer")
    grafo.add_edge("proponer", "gate")
    grafo.add_edge("gate", "ejecutar")
    grafo.add_edge("ejecutar", END)
    return grafo.compile(checkpointer=saver)


async def test_hitl_sobrevive_a_la_muerte_del_proceso(
    requiere_emulador: None, thread_id: str
) -> None:
    """El escenario que justifica el checkpointer.

    Entre la propuesta del agente y la aprobación del supervisor pueden pasar
    horas. La instancia de Cloud Run que corría el grafo ya no existe. Este test
    simula eso: descarta por completo el grafo y el saver, los reconstruye desde
    cero, y verifica que la acción que se ejecuta es exactamente la que se
    propuso.
    """
    config = {"configurable": {"thread_id": thread_id}}

    # ── Vida 1: corre hasta el gate y se interrumpe ──────────────────────────
    saver_1 = FirestoreSaver()
    grafo_1 = _construir_grafo(saver_1)
    resultado = await grafo_1.ainvoke({"tag": "", "motivo": "", "aprobado": None,
                                       "ejecutado": None}, config)

    assert "__interrupt__" in resultado, "el grafo no se detuvo en el gate"
    payload = resultado["__interrupt__"][0].value
    assert payload["accion"] == "solicitar_parada_equipo"
    assert payload["tag"] == "P-2101-A"
    motivo_propuesto = payload["motivo"]

    # ── Muere el proceso ─────────────────────────────────────────────────────
    del grafo_1, saver_1

    # ── Vida 2: otra instancia reanuda desde Firestore ───────────────────────
    saver_2 = FirestoreSaver()
    grafo_2 = _construir_grafo(saver_2)

    estado = await grafo_2.aget_state(config)
    assert estado.next == ("gate",), f"el estado no quedó esperando en el gate: {estado.next}"
    assert estado.values["tag"] == "P-2101-A", "no se recuperó el estado del hilo"

    final = await grafo_2.ainvoke(Command(resume={"aprobado": True}), config)

    assert final["ejecutado"] is not None, "la acción aprobada no se ejecutó"
    # El corazón del test: lo aprobado es lo ejecutado, literalmente.
    assert final["ejecutado"]["tag"] == "P-2101-A"
    assert final["ejecutado"]["motivo"] == motivo_propuesto


async def test_el_rechazo_no_ejecuta_la_accion(
    requiere_emulador: None, thread_id: str
) -> None:
    """Rechazar tiene que dejar el efecto sin materializar."""
    config = {"configurable": {"thread_id": thread_id}}
    saver = FirestoreSaver()
    grafo = _construir_grafo(saver)

    await grafo.ainvoke(
        {"tag": "", "motivo": "", "aprobado": None, "ejecutado": None}, config
    )
    final = await grafo.ainvoke(Command(resume={"aprobado": False}), config)

    assert final["ejecutado"] is None, "se ejecutó una acción rechazada"


async def test_el_historial_reconstruye_el_camino_a_la_propuesta(
    requiere_emulador: None, thread_id: str
) -> None:
    """Trazabilidad para auditoría.

    Un auditor no pregunta solo si hubo aprobación: pregunta qué llevó a
    proponer la parada. El historial de checkpoints tiene que permitir
    reconstruirlo.
    """
    config = {"configurable": {"thread_id": thread_id}}
    saver = FirestoreSaver()
    grafo = _construir_grafo(saver)

    await grafo.ainvoke(
        {"tag": "", "motivo": "", "aprobado": None, "ejecutado": None}, config
    )
    await grafo.ainvoke(Command(resume={"aprobado": True}), config)

    historial = [t async for t in saver.alist(config)]
    assert len(historial) >= 3, f"historial demasiado corto: {len(historial)} checkpoints"

    # El estado inicial tiene que seguir estando: nada se sobreescribió.
    pasos = sorted(t.metadata.get("step", -99) for t in historial)
    assert pasos[0] <= 0, f"falta el checkpoint inicial: pasos={pasos}"
