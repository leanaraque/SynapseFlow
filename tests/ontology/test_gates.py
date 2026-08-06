"""Los gates derivados de la ontología tienen que funcionar de verdad.

`interrupt_config(ontology, role)` es la pieza sobre la que se apoya el
compromiso 2: un desarrollador no puede agregar una acción irreversible y
olvidarse del freno, porque no es él quien lo escribe. Pero que el compilador
produzca un diccionario no significa que ese diccionario sirva.

Este módulo lo ejecuta contra el `HumanInTheLoopMiddleware` real. Es el test que
faltaba: el compilador emitía un `description` con la firma equivocada y el gate
lanzaba `TypeError` en la primera acción irreversible que se propusiera. Nada lo
detectaba, porque la capa de ontología no tenía tests y el primer commit del plan
que la ejercitaba estaba veintinueve commits más adelante.

Ver docs/adr/0005-hitl-con-interrupt-de-langgraph.md
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from synapseflow.llm.fake import FakeChatModel, Llamada, Respuesta
from synapseflow.ontology import get_ontology, interrupt_config

ONTO = get_ontology()


# ─────────────────────────────────────────────────────────────────────────────
# Forma de la configuración
# ─────────────────────────────────────────────────────────────────────────────


def test_toda_accion_irreversible_visible_para_un_rol_tiene_gate() -> None:
    """La invariante central del compromiso 2, sobre todos los roles."""
    for rol in [r.id for r in ONTO.roles]:
        config = interrupt_config(ONTO, rol)
        irreversibles = {a.id for a in ONTO.actions_for_role(rol) if not a.reversible}
        sin_gate = irreversibles - set(config)
        assert not sin_gate, f"el rol '{rol}' puede alcanzar sin gate: {sorted(sin_gate)}"


def test_ninguna_accion_reversible_lleva_gate() -> None:
    """Un gate de más entrena a los aprobadores a aprobar sin leer."""
    for rol in [r.id for r in ONTO.roles]:
        config = interrupt_config(ONTO, rol)
        for accion_id in config:
            assert ONTO.action(accion_id).requires_approval, (
                f"'{accion_id}' tiene gate y no declara requires_approval"
            )


def test_un_rol_sin_acciones_irreversibles_no_tiene_gates() -> None:
    """`auditor` y `consulta` solo leen: no hay nada que aprobar."""
    for rol in ("auditor", "consulta"):
        assert interrupt_config(ONTO, rol) == {}


# ─────────────────────────────────────────────────────────────────────────────
# El descriptor y su firma
# ─────────────────────────────────────────────────────────────────────────────


def _descriptor(accion_id: str, rol: str = "inspector") -> Any:
    entrada = interrupt_config(ONTO, rol)[accion_id]
    return entrada["description"]


def test_el_descriptor_acepta_la_firma_que_usa_el_middleware() -> None:
    """`description(tool_call, state, runtime)`.

    Es la firma de `_DescriptionFactory`, verificada por introspección contra
    langchain 1.3.14. Con la firma equivocada el gate lanza TypeError en la
    primera acción irreversible propuesta, no antes.
    """
    descriptor = _descriptor("solicitar_parada_equipo")
    tool_call = {
        "name": "solicitar_parada_equipo",
        "args": {"tag": "P-2101-A", "motivo": "espesor bajo t_min", "id_inspeccion": "INS-1"},
        "id": "call_1",
    }
    texto = descriptor(tool_call, {"messages": []}, None)
    assert isinstance(texto, str)


def test_el_texto_del_gate_lleva_los_argumentos_reales() -> None:
    """Un supervisor tiene que ver de qué equipo se trata, no «se requiere aprobación»."""
    descriptor = _descriptor("solicitar_parada_equipo")
    texto = descriptor(
        {
            "name": "solicitar_parada_equipo",
            "args": {
                "tag": "P-2101-A",
                "motivo": "espesor medido 6,8 mm bajo t_min 7,1 mm",
                "id_inspeccion": "INS-2026-00007",
            },
            "id": "call_1",
        },
        None,
        None,
    )

    assert "P-2101-A" in texto
    assert "6,8 mm" in texto
    assert "INS-2026-00007" in texto
    assert "supervisor_mantenimiento" in texto, "el texto debe decir quién puede aprobar"
    assert "irreversible" in texto


@pytest.mark.parametrize(
    "accion_id",
    sorted(a.id for a in ONTO.actions_requiring_approval() if a.approval_prompt),
)
def test_ningun_gate_se_renderiza_con_campos_no_informados(accion_id: str) -> None:
    """Con los argumentos que la acción declara, el texto tiene que salir completo.

    Este es el test que atrapa el modo de falla silencioso: el gate de
    `emitir_orden_trabajo` mostraba «sobre el activo «no informado» con prioridad
    «no informado»», que es justamente lo que el supervisor necesita para
    decidir. Un texto degradado no lo reporta nadie: se aprueba a ciegas.
    """
    accion = ONTO.action(accion_id)
    rol = accion.allowed_roles[0]
    descriptor = interrupt_config(ONTO, rol)[accion_id]["description"]

    args = {p.name: f"<{p.name}>" for p in accion.parameters if p.required}
    texto = descriptor({"name": accion_id, "args": args, "id": "c1"}, None, None)

    assert "no informado" not in texto, (
        f"el gate de '{accion_id}' deja campos sin completar con los argumentos "
        f"que la propia acción declara requeridos: {sorted(args)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# De punta a punta contra el middleware real
# ─────────────────────────────────────────────────────────────────────────────


@tool
def consultar_activo(tag: str) -> str:
    """Ficha técnica de un activo por su TAG."""
    return f"{tag}: t_min 7,1 mm, criticidad A, en servicio"


@tool
def solicitar_parada_equipo(tag: str, motivo: str, id_inspeccion: str) -> str:
    """Solicita la parada de un equipo en servicio por riesgo de integridad."""
    return f"parada registrada para {tag}"


def _agente(modelo: FakeChatModel) -> Any:
    """Agente real, con los gates derivados de la ontología y nada escrito a mano."""
    return create_agent(
        model=modelo,
        tools=[consultar_activo, solicitar_parada_equipo],
        middleware=[HumanInTheLoopMiddleware(interrupt_on=interrupt_config(ONTO, "inspector"))],
        checkpointer=InMemorySaver(),
    )


def _modelo_que_propone_la_parada() -> FakeChatModel:
    return FakeChatModel(
        respuestas=[
            Respuesta(
                herramientas=(Llamada(nombre="consultar_activo", argumentos={"tag": "P-2101-A"}),)
            ),
            Respuesta(
                texto="El espesor está por debajo de t_min. Propongo la parada.",
                herramientas=(
                    Llamada(
                        nombre="solicitar_parada_equipo",
                        argumentos={
                            "tag": "P-2101-A",
                            "motivo": "espesor medido 6,8 mm bajo t_min 7,1 mm",
                            "id_inspeccion": "INS-2026-00007",
                        },
                    ),
                ),
            ),
            Respuesta(texto="La parada quedó registrada."),
        ]
    )


async def test_el_gate_frena_la_accion_irreversible() -> None:
    """El recorrido que justifica el proyecto, sin llamar a ningún proveedor.

    El agente consulta el activo, propone la parada y **se detiene**. La
    configuración del freno no se escribe en este test: sale de
    `interrupt_config(ontology, "inspector")`.
    """
    modelo = _modelo_que_propone_la_parada()
    agente = _agente(modelo)
    config = {"configurable": {"thread_id": "gate-1"}}

    resultado = await agente.ainvoke(
        {"messages": [("user", "El P-2101-A midió 6,8 mm. ¿Sigue apto?")]}, config
    )

    assert "__interrupt__" in resultado, "el agente no se detuvo en el gate"

    # El efecto no se materializó: la herramienta no llegó a ejecutarse.
    textos = [str(getattr(m, "content", "")) for m in resultado["messages"]]
    assert not any("parada registrada" in t for t in textos), (
        "la acción irreversible se ejecutó antes de que un humano la aprobara"
    )
    assert modelo.llamadas == 2, "el modelo no debería haber sido llamado tras el freno"

    # El aprobador recibe el texto derivado de la ontología, con los argumentos
    # reales de la invocación.
    payload = resultado["__interrupt__"][0].value
    assert "P-2101-A" in str(payload)


async def test_la_lectura_previa_no_frena() -> None:
    """`consultar_activo` es reversible: tiene que ejecutarse sin pedir permiso."""
    modelo = _modelo_que_propone_la_parada()
    agente = _agente(modelo)
    resultado = await agente.ainvoke(
        {"messages": [("user", "¿sigue apto?")]},
        {"configurable": {"thread_id": "gate-2"}},
    )
    textos = [str(getattr(m, "content", "")) for m in resultado["messages"]]
    assert any("t_min 7,1 mm" in t for t in textos), (
        "la herramienta de lectura no se ejecutó: un gate de más la estaría frenando"
    )


async def test_aprobar_ejecuta_exactamente_lo_propuesto() -> None:
    """Lo que se aprueba es lo que se ejecuta, sin reinterpretación."""
    modelo = _modelo_que_propone_la_parada()
    agente = _agente(modelo)
    config = {"configurable": {"thread_id": "gate-3"}}

    await agente.ainvoke({"messages": [("user", "¿sigue apto?")]}, config)
    final = await agente.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), config)

    textos = [str(getattr(m, "content", "")) for m in final["messages"]]
    assert any("parada registrada para P-2101-A" in t for t in textos), (
        "la acción aprobada no se ejecutó con el TAG propuesto"
    )


async def test_rechazar_no_materializa_el_efecto() -> None:
    modelo = FakeChatModel(
        respuestas=[
            *_modelo_que_propone_la_parada().respuestas[:2],
            Respuesta(texto="Entendido, no se solicita la parada."),
        ]
    )
    agente = _agente(modelo)
    config = {"configurable": {"thread_id": "gate-4"}}

    await agente.ainvoke({"messages": [("user", "¿sigue apto?")]}, config)
    final = await agente.ainvoke(
        Command(
            resume={"decisions": [{"type": "reject", "message": "falta el permiso de trabajo"}]}
        ),
        config,
    )

    textos = [str(getattr(m, "content", "")) for m in final["messages"]]
    assert not any("parada registrada" in t for t in textos), "se ejecutó una acción rechazada"
