"""Contrato del grafo ensamblado.

Lo que se verifica es la **estructura**: que los nodos existan, que las aristas
lleven donde tienen que llevar, que los gates salgan de la ontología y que el
grafo compile con el checkpointer puesto.

El recorrido completo y el test estructural de gates son F5.6.

Ver docs/plan/fases/F5-grafo.md § F5.5
"""

from __future__ import annotations

from typing import Any

from synapseflow.agents.especialistas import HERRAMIENTAS_DE_ACCION
from synapseflow.agents.graph import (
    NODO_ACCIONES,
    NODO_SUPERVISOR,
    construir_grafo,
    gates_del_grafo,
    nodos_del_grafo,
)
from synapseflow.agents.supervisor import NODO_VERIFICADOR
from synapseflow.config import Provider, Settings
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.fake import FakeChatModel
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import get_ontology, interrupt_config

# Importar el paquete registra las implementaciones de las nueve acciones.
import synapseflow.domain  # noqa: F401  isort: skip

ONTOLOGIA = get_ontology()
INSPECTOR = ExecutionContext(usuario="uid-1", rol="inspector", thread_id="hilo-1")
CONSULTA = ExecutionContext(usuario="uid-2", rol="consulta", thread_id="hilo-2")


def ajustes(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"SYNAPSEFLOW_PROVIDER": Provider.FAKE}
    base.update(overrides)
    return Settings(**base)


def grafo(
    ctx: ExecutionContext = INSPECTOR, modelo: FakeChatModel | None = None, **extra: Any
) -> Any:
    return construir_grafo(
        ONTOLOGIA,
        ctx,
        gateway=Gateway(settings=ajustes(), falso=modelo),
        settings=ajustes(),
        **extra,
    )


# ─────────────────────────────────────────────────────────────────────────────
# La estructura
# ─────────────────────────────────────────────────────────────────────────────


def test_el_grafo_compila() -> None:
    assert grafo() is not None


def test_estan_todos_los_nodos() -> None:
    compilado = grafo()
    presentes = set(compilado.get_graph().nodes)

    for nodo in nodos_del_grafo():
        assert nodo in presentes, f"falta el nodo '{nodo}'"


def test_el_supervisor_es_la_entrada() -> None:
    """Toda consulta pasa por el ruteo, incluso las que van directo al final."""
    dibujo = grafo().get_graph()
    desde_inicio = {a.target for a in dibujo.edges if a.source == "__start__"}

    assert desde_inicio == {NODO_SUPERVISOR}


def test_cada_especialista_vuelve_al_supervisor() -> None:
    """El supervisor decide si falta otro. Sin la vuelta, cada consulta usaría
    un solo especialista y el caso de referencia no existiría."""
    dibujo = grafo().get_graph()

    for especialista in ("datos", "calculo", "normativa"):
        destinos = {a.target for a in dibujo.edges if a.source == especialista}
        assert NODO_SUPERVISOR in destinos, f"'{especialista}' no vuelve al supervisor"


def test_el_verificador_puede_volver_a_normativa() -> None:
    """**Es el ciclo**, y una de las dos razones por las que el proyecto usa un
    motor de grafos."""
    dibujo = grafo().get_graph()
    destinos = {a.target for a in dibujo.edges if a.source == NODO_VERIFICADOR}

    assert "normativa" in destinos


def test_el_grafo_termina_despues_de_acciones() -> None:
    dibujo = grafo().get_graph()
    destinos = {a.target for a in dibujo.edges if a.source == NODO_ACCIONES}

    assert "__end__" in destinos


# ─────────────────────────────────────────────────────────────────────────────
# Los gates salen de la ontología
# ─────────────────────────────────────────────────────────────────────────────


def test_los_gates_se_derivan_del_yaml() -> None:
    """**La línea que cierra el compromiso 2.**

    Un desarrollador no puede agregar una acción irreversible y olvidarse del
    freno, porque no es él quien lo escribe.

    Se comparan las claves y las decisiones permitidas, no el dict entero: el
    `description` de cada gate es una closure y dos llamadas producen objetos
    distintos, así que la igualdad estructural daría falso siempre.
    """
    del_grafo = gates_del_grafo(ONTOLOGIA, "inspector")
    de_la_ontologia = interrupt_config(ONTOLOGIA, "inspector")

    assert set(del_grafo) == set(de_la_ontologia)
    for herramienta, config in del_grafo.items():
        assert config["allowed_decisions"] == de_la_ontologia[herramienta]["allowed_decisions"]


def test_toda_accion_irreversible_del_rol_tiene_gate() -> None:
    gates = set(gates_del_grafo(ONTOLOGIA, "inspector"))
    irreversibles = {
        a.tool_name for a in ONTOLOGIA.actions_for_role("inspector") if not a.reversible
    }

    assert irreversibles <= gates, f"sin gate: {irreversibles - gates}"


def test_el_nodo_de_acciones_tiene_las_escrituras() -> None:
    """Sin él no hay quién proponga la parada, y el recorrido no llega al gate.

    Los tres especialistas del plan son de lectura: este cuarto agente es la
    corrección que el ensamblado hizo evidente.
    """
    compilado = grafo()
    nodo = compilado.get_graph().nodes[NODO_ACCIONES]

    assert nodo is not None
    assert set(HERRAMIENTAS_DE_ACCION) == {
        "registrar_borrador_ot",
        "emitir_orden_trabajo",
        "solicitar_parada_equipo",
        "reclasificar_criticidad",
    }


# ─────────────────────────────────────────────────────────────────────────────
# El grafo hereda los permisos del usuario
# ─────────────────────────────────────────────────────────────────────────────


def test_el_grafo_de_un_rol_restringido_solo_tiene_sus_especialistas() -> None:
    """**El agente hereda los permisos del usuario, no los de la cuenta.**

    El rol `consulta` solo lee normativa pública. Su grafo se arma —antes fallaba
    al construir, y eso dejaba a tres de los cinco roles sin poder usar la
    plataforma— pero **sin** los nodos que ese rol no puede usar. El supervisor no
    puede rutear a un agente que no tiene con qué contestar.
    """
    compilado = grafo(CONSULTA)
    presentes = set(compilado.get_graph().nodes)

    assert "normativa" in presentes
    assert "datos" not in presentes
    assert "calculo" not in presentes
    # El de acciones existe siempre: además de proponer, redacta la respuesta.
    assert NODO_ACCIONES in presentes


def test_todo_rol_del_dominio_puede_armar_su_grafo() -> None:
    """El test que faltaba y que habría atrapado el bug antes de desplegarlo."""
    from synapseflow.governance.rbac import ExecutionContext

    for rol in (r.id for r in ONTOLOGIA.roles):
        ctx = ExecutionContext(usuario=f"uid-{rol}", rol=rol, thread_id="hilo-1")
        assert grafo(ctx) is not None, f"el rol '{rol}' no puede armar su grafo"


def test_el_checkpointer_se_puede_inyectar() -> None:
    """Sin él, el gate no sobrevive a la muerte del proceso.

    El human-in-the-loop asincrónico deja de ser asincrónico: el supervisor tiene
    que aprobar antes de que el proceso muera.
    """
    from langgraph.checkpoint.memory import InMemorySaver

    compilado = grafo(checkpointer=InMemorySaver())
    assert compilado.checkpointer is not None


def test_sin_checkpointer_el_grafo_igual_compila() -> None:
    """Para los tests que no necesitan persistencia."""
    assert grafo().checkpointer is None


# ─────────────────────────────────────────────────────────────────────────────
# El turno humano de cierre
# ─────────────────────────────────────────────────────────────────────────────
#
# Gemini rechaza el «prefilling»: el último turno tiene que ser un mensaje humano
# o una respuesta de herramienta. Cuando el supervisor rutea a un SEGUNDO
# especialista, el historial termina en la respuesta del primero —un AIMessage—
# y el proveedor devuelve 400.
#
# El modelo falso acepta cualquier secuencia, así que estos tests no lo habrían
# detectado solos: apareció en la primera consulta real contra el sistema
# desplegado. Quedan acá para que la invariante no se pierda.


def test_una_conversacion_que_termina_en_el_modelo_recibe_un_turno_humano() -> None:
    """**El fallo que solo aparece con un proveedor real.**"""
    from langchain_core.messages import AIMessage, HumanMessage

    from synapseflow.agents.graph import _cerrar_con_turno_humano

    previos = [HumanMessage(content="¿P-2101-A sigue apto?"), AIMessage(content="Consulté datos.")]

    entrada = _cerrar_con_turno_humano(previos, "normativa")

    assert len(entrada) == len(previos) + 1
    assert entrada[-1].type == "human"
    assert "normativa" in entrada[-1].content


def test_una_conversacion_que_ya_termina_en_humano_no_se_toca() -> None:
    """Agregar un turno de más ensucia el historial sin resolver nada."""
    from langchain_core.messages import HumanMessage

    from synapseflow.agents.graph import _cerrar_con_turno_humano

    previos = [HumanMessage(content="¿P-2101-A sigue apto?")]

    assert _cerrar_con_turno_humano(previos, "datos") == previos


def test_una_conversacion_vacia_no_se_toca() -> None:
    from synapseflow.agents.graph import _cerrar_con_turno_humano

    assert _cerrar_con_turno_humano([], "datos") == []
