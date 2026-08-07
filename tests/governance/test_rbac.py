"""Contrato de identidad y autoridad.

Los dos tests que dan sentido al módulo son negativos, y son los que el mapa de
acción exige para cerrar F4:

- Un usuario sin `approver_roles` no puede aprobar un gate.
- **El proponente no puede aprobar su propia acción.**

El segundo es separación de funciones, y en una empresa regulada no es una
preferencia. Un supervisor que propone una parada y la aprueba él mismo produce
exactamente el mismo registro de auditoría que uno que la aprobó sin leerla.

Ver docs/plan/fases/F4-gobernanza.md § F4.1
"""

from __future__ import annotations

import pytest

from synapseflow.governance.rbac import (
    AutoridadInsuficienteError,
    ContextoRequeridoError,
    ExecutionContext,
    aprobadores_de,
    exigir_autoridad_de_aprobacion,
    exigir_contexto,
    exigir_rol_autorizado,
    puede_aprobar,
)
from synapseflow.ontology import Action, get_ontology

SUPERVISOR = ExecutionContext(usuario="uid-supervisor", rol="supervisor_mantenimiento")
INSPECTOR = ExecutionContext(usuario="uid-inspector", rol="inspector")
TECNICO = ExecutionContext(usuario="uid-tecnico", rol="tecnico")


def accion(action_id: str) -> Action:
    return next(a for a in get_ontology().actions if a.id == action_id)


PARADA = accion("solicitar_parada_equipo")
EMISION = accion("emitir_orden_trabajo")


# ─────────────────────────────────────────────────────────────────────────────
# Identidad
# ─────────────────────────────────────────────────────────────────────────────


def test_el_contexto_es_inmutable() -> None:
    """Entra al artifact de cada acción y de ahí al log de auditoría.

    Un contexto mutable permitiría que una acción cambie la identidad con la que
    quedó registrada la anterior.
    """
    with pytest.raises(Exception):  # noqa: B017 - pydantic lanza ValidationError
        SUPERVISOR.usuario = "otro"  # type: ignore[misc]


def test_el_registro_lleva_usuario_rol_e_hilo() -> None:
    """Es la respuesta a «bajo la autoridad de quién se ejecutó esto»."""
    contexto = ExecutionContext(usuario="uid-1", rol="inspector", thread_id="hilo-9")
    registro = contexto.registro()

    assert registro["usuario"] == "uid-1"
    assert registro["rol"] == "inspector"
    assert registro["thread_id"] == "hilo-9"
    assert registro["momento"]


def test_un_contexto_sin_usuario_no_se_puede_construir() -> None:
    """«Anónimo» no es una identidad auditable."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic lanza ValidationError
        ExecutionContext(usuario="", rol="inspector")


def test_exigir_contexto_nombra_la_accion_que_lo_necesitaba() -> None:
    with pytest.raises(ContextoRequeridoError, match="emitir_orden_trabajo"):
        exigir_contexto(None, "emitir_orden_trabajo")


def test_exigir_contexto_devuelve_el_contexto_para_estrechar_el_tipo() -> None:
    assert exigir_contexto(SUPERVISOR, "x") is SUPERVISOR


def test_un_rol_no_autorizado_no_ejecuta() -> None:
    with pytest.raises(PermissionError, match="tecnico"):
        exigir_rol_autorizado(TECNICO, "reclasificar_criticidad", ["inspector"])


# ─────────────────────────────────────────────────────────────────────────────
# Autoridad para aprobar · los dos negativos que exige el plan
# ─────────────────────────────────────────────────────────────────────────────


def test_un_rol_sin_autoridad_no_puede_aprobar() -> None:
    """El catálogo filtra quién PROPONE, no quién APRUEBA.

    Un inspector puede proponer una parada y no puede aprobarla: los conjuntos
    son distintos y por eso este chequeo no es redundante con el filtrado.
    """
    assert puede_aprobar(INSPECTOR, PARADA) is False

    with pytest.raises(AutoridadInsuficienteError, match="no puede aprobar"):
        exigir_autoridad_de_aprobacion(INSPECTOR, PARADA)


def test_el_proponente_no_puede_aprobar_su_propia_accion() -> None:
    """**Separación de funciones.**

    Aprobar la propia propuesta produce el mismo registro de auditoría que
    aprobar sin leer, así que la aprobación deja de significar algo.
    """
    assert puede_aprobar(SUPERVISOR, PARADA, propuesta_por=SUPERVISOR.usuario) is False

    with pytest.raises(AutoridadInsuficienteError, match="no puede aprobarla"):
        exigir_autoridad_de_aprobacion(SUPERVISOR, PARADA, propuesta_por=SUPERVISOR.usuario)


def test_el_motivo_del_rechazo_se_distingue() -> None:
    """«No sos aprobador» y «no podés aprobar la tuya» se corrigen distinto.

    El primero se resuelve pidiéndoselo a otra persona; el segundo también, pero
    por una razón que el usuario tiene que entender para no insistir.
    """
    with pytest.raises(AutoridadInsuficienteError) as sin_rol:
        exigir_autoridad_de_aprobacion(TECNICO, PARADA)
    with pytest.raises(AutoridadInsuficienteError) as propio:
        exigir_autoridad_de_aprobacion(SUPERVISOR, PARADA, propuesta_por=SUPERVISOR.usuario)

    assert "Aprobadores declarados" in str(sin_rol.value)
    assert "separación de funciones" in str(propio.value)


def test_un_aprobador_legitimo_de_otra_propuesta_si_puede() -> None:
    """El control positivo: sin esto, un sistema que rechaza todo pasaría igual."""
    assert puede_aprobar(SUPERVISOR, PARADA, propuesta_por=INSPECTOR.usuario) is True
    exigir_autoridad_de_aprobacion(SUPERVISOR, PARADA, propuesta_por=INSPECTOR.usuario)


def test_sin_proponente_declarado_solo_se_verifica_el_rol() -> None:
    """La consola puede no saber quién propuso —una sesión reanudada, por
    ejemplo—. Ahí se verifica lo que sí se sabe en lugar de bloquear todo."""
    exigir_autoridad_de_aprobacion(SUPERVISOR, PARADA)


# ─────────────────────────────────────────────────────────────────────────────
# Los aprobadores salen de la ontología
# ─────────────────────────────────────────────────────────────────────────────


def test_los_aprobadores_salen_del_yaml_y_no_del_codigo() -> None:
    """Cambiar quién aprueba una acción es editar el YAML."""
    assert aprobadores_de(PARADA) == tuple(PARADA.approver_roles)
    assert "supervisor_mantenimiento" in aprobadores_de(PARADA)


def test_toda_accion_que_requiere_aprobacion_declara_aprobadores() -> None:
    """Una acción con gate y sin aprobadores no se puede aprobar nunca.

    Quedaría trabada para siempre, y el usuario vería el gate sin ningún camino
    hacia adelante.
    """
    for accion_ in get_ontology().actions:
        if accion_.requires_approval:
            assert accion_.approver_roles, (
                f"'{accion_.id}' requiere aprobación y no declara approver_roles: "
                "el gate quedaría trabado sin salida"
            )


def test_ninguna_accion_reversible_exige_aprobacion() -> None:
    """Un gate sobre algo reversible cansa al aprobador sin reducir riesgo.

    Y un aprobador cansado aprueba sin leer, que es cómo se pierde la garantía
    sobre las que sí importan.
    """
    for accion_ in get_ontology().actions:
        if accion_.reversible:
            assert not accion_.requires_approval, f"'{accion_.id}' es reversible y exige aprobación"


@pytest.mark.parametrize("action_id", ["emitir_orden_trabajo", "solicitar_parada_equipo"])
def test_las_acciones_irreversibles_las_aprueba_un_supervisor(action_id: str) -> None:
    assert "supervisor_mantenimiento" in aprobadores_de(accion(action_id))


def test_el_rol_auditor_no_aprueba_nada() -> None:
    """Lectura total, cero capacidad de escritura ni de aprobación.

    Es lo que hace que el auditor pueda mirar todo sin convertirse en parte de la
    cadena de decisión que audita.
    """
    auditor = ExecutionContext(usuario="uid-auditor", rol="auditor")

    for accion_ in get_ontology().actions:
        if accion_.requires_approval:
            assert puede_aprobar(auditor, accion_) is False, (
                f"el auditor podría aprobar '{accion_.id}'"
            )


def test_emitir_orden_no_la_aprueba_quien_la_propuso() -> None:
    """El caso concreto del recorrido: el técnico propone, el supervisor emite."""
    assert puede_aprobar(TECNICO, EMISION) is False
    assert puede_aprobar(SUPERVISOR, EMISION, propuesta_por=TECNICO.usuario) is True
