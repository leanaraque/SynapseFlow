"""Ninguna acción irreversible es alcanzable sin gate.

**Verifica una propiedad del sistema, no un comportamiento.** No invoca ninguna
acción: recorre la estructura y comprueba que toda acción con `reversible: false`
esté cubierta por el `HumanInTheLoopMiddleware`.

Es la clase de test que justifica haber elegido un motor de grafos. Con el
control de flujo disperso en `if`s repartidos por el código, esta propiedad solo
se podría revisar leyendo todo y confiando.

Ver docs/plan/fases/F5-grafo.md § F5.6
"""

from __future__ import annotations

import pytest

from synapseflow.agents.graph import gates_del_grafo
from synapseflow.ontology import get_ontology, interrupt_config

ONTOLOGIA = get_ontology()
ROLES = [r.id for r in ONTOLOGIA.roles]


# ─────────────────────────────────────────────────────────────────────────────
# La propiedad
# ─────────────────────────────────────────────────────────────────────────────


def test_ninguna_accion_irreversible_es_alcanzable_sin_gate() -> None:
    """**La propiedad que sostiene el compromiso 2.**

    Para cada rol, toda acción que su catálogo incluye y que declara
    `reversible: false` tiene que tener su entrada en la configuración de gates.
    Si falta una, ese rol podría materializar una acción irreversible sin que
    ninguna persona la apruebe.
    """
    for rol in ROLES:
        gates = set(interrupt_config(ONTOLOGIA, rol))
        irreversibles = {a.tool_name for a in ONTOLOGIA.actions_for_role(rol) if not a.reversible}

        assert irreversibles <= gates, (
            f"el rol '{rol}' puede alcanzar sin gate: {sorted(irreversibles - gates)}"
        )


def test_el_grafo_usa_exactamente_esa_configuracion() -> None:
    """La propiedad se verifica sobre `interrupt_config`, y el grafo la usa.

    Sin este test, el de arriba podría pasar mientras el grafo arma sus gates de
    otra manera: se estaría verificando una fuente que nadie consume.
    """
    for rol in ROLES:
        assert set(gates_del_grafo(ONTOLOGIA, rol)) == set(interrupt_config(ONTOLOGIA, rol))


def test_hay_acciones_irreversibles_que_verificar() -> None:
    """El control negativo.

    Si la ontología no tuviera ninguna acción irreversible, el test de arriba
    pasaría por vacío y nadie se enteraría de que dejó de comprobar algo.
    """
    irreversibles = [a for a in ONTOLOGIA.actions if not a.reversible]

    assert len(irreversibles) >= 3, (
        f"solo hay {len(irreversibles)} acciones irreversibles: el test estructural "
        "estaría verificando casi nada"
    )


@pytest.mark.parametrize("rol", ROLES)
def test_ningun_gate_sobra(rol: str) -> None:
    """Un gate sobre algo reversible cansa al aprobador sin reducir riesgo.

    Y un aprobador cansado aprueba sin leer, que es cómo se pierde la garantía
    sobre las que sí importan.
    """
    gates = set(interrupt_config(ONTOLOGIA, rol))
    reversibles = {a.tool_name for a in ONTOLOGIA.actions_for_role(rol) if a.reversible}

    assert not (gates & reversibles), f"gate sobre acción reversible: {gates & reversibles}"


@pytest.mark.parametrize("rol", ROLES)
def test_todo_gate_corresponde_a_una_accion_del_rol(rol: str) -> None:
    """Un gate sobre una herramienta que el rol no ve nunca se dispararía.

    Sería una garantía inerte, del tipo que este proyecto ya pagó una vez.
    """
    gates = set(interrupt_config(ONTOLOGIA, rol))
    del_rol = {a.tool_name for a in ONTOLOGIA.actions_for_role(rol)}

    assert gates <= del_rol, f"gates sin acción correspondiente: {gates - del_rol}"


# ─────────────────────────────────────────────────────────────────────────────
# La forma de cada gate
# ─────────────────────────────────────────────────────────────────────────────


def test_todo_gate_permite_aprobar_y_rechazar() -> None:
    """Un gate sin `reject` no es un freno: es una demora."""
    for rol in ROLES:
        for herramienta, config in interrupt_config(ONTOLOGIA, rol).items():
            decisiones = set(config["allowed_decisions"])
            assert {"approve", "reject"} <= decisiones, (
                f"'{herramienta}' no permite aprobar y rechazar: {decisiones}"
            )


def test_el_texto_del_gate_nombra_el_activo() -> None:
    """Un supervisor que aprueba una parada tiene que ver de qué equipo se trata.

    «Se requiere aprobación» a secas es lo que produce una aprobación automática.
    """
    gates = interrupt_config(ONTOLOGIA, "inspector")
    descriptor = gates["solicitar_parada_equipo"]["description"]

    texto = descriptor(
        {
            "name": "solicitar_parada_equipo",
            "args": {"tag": "P-2101-A", "motivo": "espesor bajo t_min", "id_inspeccion": "INS-1"},
            "id": "call_1",
        }
    )

    assert "P-2101-A" in texto
    assert "espesor bajo t_min" in texto
    assert "supervisor_mantenimiento" in texto


def test_el_rol_de_consulta_no_tiene_ningun_gate() -> None:
    """No porque se le hayan sacado, sino porque no ve ninguna acción de escritura.

    Es la diferencia entre quitar el freno y no tener a dónde ponerlo.
    """
    assert interrupt_config(ONTOLOGIA, "consulta") == {}
    assert not [a for a in ONTOLOGIA.actions_for_role("consulta") if not a.reversible]
