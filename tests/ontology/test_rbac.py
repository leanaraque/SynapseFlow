"""Mínimo privilegio y clasificación de datos, derivados de la ontología.

Es el módulo que el ADR-0003 declara como verificación desde el primer commit:

    tests/ontology/test_rbac.py recorre la matriz rol × acción y verifica que
    ningún rol pueda ejecutar una acción por encima de su max_classification.

No existía. Y la invariante tampoco: `allowed_roles` y `max_classification` eran
dos mecanismos de permisos que podían contradecirse sin que nada lo notara,
porque el catálogo de herramientas se arma con el primero y nadie consultaba el
segundo.

Ver docs/adr/0003-ontologia-declarativa-en-yaml.md
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from synapseflow.ontology import Effect, get_ontology
from synapseflow.ontology.schema import Ontology

ONTO = get_ontology()
DEFINICION = (
    Path(__file__).resolve().parents[2]
    / "packages/synapseflow/ontology/definitions/oil_and_gas.yaml"
)


@pytest.fixture(scope="module")
def crudo() -> dict[str, Any]:
    return dict(yaml.safe_load(DEFINICION.read_text(encoding="utf-8")))


# ─────────────────────────────────────────────────────────────────────────────
# La invariante que el ADR promete
# ─────────────────────────────────────────────────────────────────────────────


def test_ningun_rol_ejecuta_acciones_por_encima_de_su_clasificacion() -> None:
    """La matriz rol × acción completa, sobre el dominio que se publica."""
    for accion in ONTO.actions:
        for rol_id in accion.allowed_roles:
            assert ONTO.can_role_read_entity(rol_id, accion.target_entity), (
                f"'{rol_id}' puede ejecutar '{accion.id}' sobre "
                f"'{accion.target_entity}', que excede su clasificación máxima"
            )


def test_la_invariante_se_hace_cumplir_al_cargar(crudo: dict[str, Any]) -> None:
    """No alcanza con que el dominio actual la cumpla: tiene que ser imposible violarla.

    Sin esta comprobación, el test de arriba pasa hoy y deja de proteger nada el
    día que alguien agregue un rol a una acción sin mirar la clasificación.
    """
    mutado = copy.deepcopy(crudo)
    for accion in mutado["actions"]:
        # `consulta` es rank 0 (public); `inspection` es confidential.
        if accion["id"] == "historial_inspecciones":
            accion["allowed_roles"].append("consulta")

    with pytest.raises(ValueError, match="por encima de la clasificación"):
        Ontology.model_validate(mutado)


def test_el_mensaje_dice_qué_rol_y_qué_entidad(crudo: dict[str, Any]) -> None:
    """Un error de gobernanza tiene que ser accionable sin leer el código."""
    mutado = copy.deepcopy(crudo)
    for accion in mutado["actions"]:
        if accion["id"] == "consultar_activo":
            accion["allowed_roles"].append("consulta")

    with pytest.raises(ValueError) as excinfo:
        Ontology.model_validate(mutado)

    mensaje = str(excinfo.value)
    assert "consulta" in mensaje
    assert "consultar_activo" in mensaje
    assert "asset" in mensaje


# ─────────────────────────────────────────────────────────────────────────────
# Mínimo privilegio
# ─────────────────────────────────────────────────────────────────────────────


def test_el_catalogo_de_cada_rol_es_un_subconjunto_estricto() -> None:
    """Lo que el modelo no recibe, no lo puede invocar ni ofrecer."""
    total = {a.id for a in ONTO.actions}
    for rol in ONTO.roles:
        visibles = {a.id for a in ONTO.actions_for_role(rol.id)}
        assert visibles <= total
        assert visibles, f"el rol '{rol.id}' no ve ninguna acción: no podría hacer nada"


def test_consulta_solo_ve_normativa_publica() -> None:
    """El rol de menor privilegio, que es el que más importa acotar."""
    visibles = [a.id for a in ONTO.actions_for_role("consulta")]
    assert visibles == ["buscar_normativa"]


def test_el_auditor_lee_todo_y_no_escribe_nada() -> None:
    """Lectura total incluida la clasificación restricted, sin capacidad de escritura.

    Un auditor que pudiera escribir dejaría de ser auditor: revisaría sus propios
    actos.
    """
    acciones = ONTO.actions_for_role("auditor")
    escrituras = [a.id for a in acciones if a.effect is Effect.WRITE]
    assert not escrituras, f"el auditor puede escribir: {escrituras}"
    assert ONTO.max_classification_for_role("auditor") == ONTO.classification_rank("restricted")


def test_solo_el_supervisor_aprueba() -> None:
    """Los `approver_roles` no pueden repartirse sin que quede declarado."""
    aprobadores = {r for a in ONTO.actions_requiring_approval() for r in a.approver_roles}
    assert aprobadores == {"supervisor_mantenimiento"}


def test_toda_accion_de_escritura_la_ejecuta_alguien_que_lee_la_entidad() -> None:
    """Escribir sin poder leer lo que se escribe es una combinación sin sentido."""
    for accion in ONTO.actions:
        if accion.effect is not Effect.WRITE:
            continue
        for rol_id in accion.allowed_roles:
            assert ONTO.can_role_read_entity(rol_id, accion.target_entity)


# ─────────────────────────────────────────────────────────────────────────────
# Campos que no salen del perímetro
# ─────────────────────────────────────────────────────────────────────────────


def test_los_campos_pii_del_dominio_estan_declarados() -> None:
    """Los dos campos que identifican personas en este dominio.

    Son los que el tokenizador de F4.2 tiene que reemplazar antes de que el
    texto salga hacia un proveedor.
    """
    pii = ONTO.pii_fields()
    assert pii["inspection"] == ["inspector_legajo"]
    assert pii["work_order"] == ["solicitante"]
    assert set(pii) == {"inspection", "work_order"}, (
        f"cambió el conjunto de entidades con datos personales: {sorted(pii)}"
    )


def test_una_entidad_restringida_redacta_todos_sus_campos(crudo: dict[str, Any]) -> None:
    """Una propiedad hereda la clasificación de su entidad si no declara la propia.

    Sin esto, marcar una entidad entera como `restricted` no redactaría ningún
    campo salvo los marcados uno por uno — la falla más silenciosa que puede
    tener una política de datos.
    """
    mutado = copy.deepcopy(crudo)
    for entidad in mutado["entities"]:
        if entidad["id"] == "work_order":
            entidad["classification"] = "restricted"
    # El rol que la ejecuta tiene que poder leerla, o falla la otra invariante.
    for rol in mutado["roles"]:
        if rol["id"] in ("tecnico", "inspector", "supervisor_mantenimiento"):
            rol["max_classification"] = "restricted"

    onto = Ontology.model_validate(mutado)
    redactados = set(onto.pii_fields()["work_order"])
    declarados = {
        p["name"]
        for p in next(e for e in mutado["entities"] if e["id"] == "work_order")["properties"]
    }

    assert redactados == declarados, (
        "una entidad restricted debe redactar todos sus campos, no solo los "
        f"marcados: faltan {sorted(declarados - redactados)}"
    )


def test_ningun_campo_pii_queda_sin_clasificar() -> None:
    """Un campo `pii: true` sin clasificación explícita es ambiguo.

    El redactor lo tomaría igual, pero el ruteo de modelos por clasificación no,
    y esas dos decisiones tienen que coincidir.
    """
    sin_clasificar = [
        f"{e.id}.{p.name}"
        for e in ONTO.entities
        for p in e.properties
        if p.pii and not p.classification
    ]
    assert not sin_clasificar, f"campos pii sin clasificación propia: {sin_clasificar}"
