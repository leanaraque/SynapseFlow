"""Los golden datasets tienen que ser verificables antes de verificar nada.

Un caso que espera una cita a una sección inexistente hace que la eval sea
**infalsificable**: el sistema nunca la va a producir, la métrica siempre va a
dar mal, y nadie va a saber si el problema es el sistema o el dataset.

Por eso estos tests contrastan cada cita esperada contra el corpus real. Es la
misma idea que el resto del proyecto: una afirmación sin verificación es una
afirmación.

Ver docs/plan/fases/F8-evals.md § F8.1
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapseflow.rag.ingesta import leer_corpus, trocear

DATASETS = Path(__file__).resolve().parents[2] / "evals" / "datasets"
SUITES = ("normativa", "datos", "calculos", "rechazo")


def casos(suite: str) -> list[dict[str, Any]]:
    ruta = DATASETS / f"{suite}.jsonl"
    return [
        json.loads(linea)
        for linea in ruta.read_text(encoding="utf-8").splitlines()
        if linea.strip()
    ]


def todos_los_casos() -> list[dict[str, Any]]:
    return [c for suite in SUITES for c in casos(suite)]


def citas_del_corpus() -> set[str]:
    """Todas las citas que el corpus hace posibles, como `DOC-ID §sección`."""
    return {
        f"{f.metadata['doc_id']} §{f.metadata['seccion']}"
        for documento in leer_corpus()
        for f in trocear(documento)
    }


# ─────────────────────────────────────────────────────────────────────────────
# Las citas esperadas existen
# ─────────────────────────────────────────────────────────────────────────────


def test_toda_cita_esperada_existe_en_el_corpus() -> None:
    """**El test que hace falsificable la suite.**

    Una cita a una sección inexistente produce una métrica que siempre da mal, y
    nadie sabe si el problema es el sistema o el dataset.
    """
    disponibles = citas_del_corpus()
    inexistentes: list[str] = []

    for caso in todos_los_casos():
        for cita in caso.get("fuentes") or []:
            if cita not in disponibles:
                inexistentes.append(f"{caso['id']}: {cita}")

    assert not inexistentes, (
        "hay casos que esperan citas que el corpus no puede producir:\n  "
        + "\n  ".join(inexistentes)
    )


def test_toda_cita_prohibida_existe_en_el_corpus() -> None:
    """Prohibir una cita imposible no prueba nada.

    El caso pasaría siempre, y el día que el sistema empezara a citar el derogado
    de verdad nadie se enteraría.
    """
    disponibles = citas_del_corpus()

    for caso in todos_los_casos():
        for cita in caso.get("prohibidas") or []:
            assert cita in disponibles, (
                f"{caso['id']} prohíbe '{cita}', que el corpus no puede producir: "
                "el caso pasaría por vacío"
            )


def test_las_citas_prohibidas_son_de_documentos_derogados() -> None:
    """Prohibir una cita vigente sería un error del dataset, no una regla."""
    derogadas = {
        f"{f.metadata['doc_id']} §{f.metadata['seccion']}"
        for documento in leer_corpus()
        if documento.vigencia == "derogado"
        for f in trocear(documento)
    }

    for caso in todos_los_casos():
        for cita in caso.get("prohibidas") or []:
            assert cita in derogadas, f"{caso['id']} prohíbe '{cita}', que está vigente"


# ─────────────────────────────────────────────────────────────────────────────
# La forma de cada caso
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("suite", SUITES)
def test_cada_suite_tiene_casos(suite: str) -> None:
    assert len(casos(suite)) >= 5, f"la suite '{suite}' tiene muy pocos casos"


@pytest.mark.parametrize("suite", SUITES)
def test_cada_caso_tiene_los_campos_obligatorios(suite: str) -> None:
    for caso in casos(suite):
        for campo in ("id", "pregunta", "respuesta_esperada", "debe_rechazar"):
            assert campo in caso, f"a un caso de '{suite}' le falta '{campo}'"


def test_los_ids_no_se_repiten() -> None:
    """Dos casos con el mismo id hacen ambiguo el reporte por caso, que es lo
    único que sirve cuando una métrica baja."""
    ids = [c["id"] for c in todos_los_casos()]
    assert len(ids) == len(set(ids))


def test_ningun_caso_espera_citas_y_rechazo_a_la_vez() -> None:
    """Es contradictorio: si hay que rechazar, no hay fuentes que citar."""
    for caso in todos_los_casos():
        if caso["debe_rechazar"]:
            assert not caso.get("fuentes"), f"{caso['id']} debe rechazar y a la vez espera citas"


# ─────────────────────────────────────────────────────────────────────────────
# La suite de rechazo es la que más valor aporta
# ─────────────────────────────────────────────────────────────────────────────


def test_hay_casos_que_el_sistema_debe_rechazar() -> None:
    """**Sin ellos, la suite premia a un modelo que siempre contesta algo.**

    Que es exactamente el comportamiento peligroso: un asistente que nunca dice
    «no sé» es más peligroso que uno que a veces lo dice.
    """
    a_rechazar = [c for c in todos_los_casos() if c["debe_rechazar"]]

    assert len(a_rechazar) >= 5, (
        f"solo hay {len(a_rechazar)} casos de rechazo: la suite premiaría a un "
        "modelo que siempre contesta"
    )


def test_hay_casos_que_el_sistema_debe_responder() -> None:
    """El control opuesto: un sistema que rechaza todo también pasaría."""
    a_responder = [c for c in todos_los_casos() if not c["debe_rechazar"]]

    assert len(a_responder) >= 10


def test_hay_un_caso_con_normativa_derogada() -> None:
    """El corpus incluye `PROC-INT-009` justamente para esto.

    Contradice al vigente en el criterio de aceptación: responder desde él no es
    un resultado de baja calidad, es un error normativo.
    """
    con_prohibidas = [c for c in todos_los_casos() if c.get("prohibidas")]
    assert con_prohibidas, "ningún caso verifica que no se cite el documento derogado"


def test_hay_un_caso_que_verifica_que_el_legajo_no_se_expone() -> None:
    """El compromiso 5, medido como métrica y no solo como test unitario."""
    con_prohibido = [c for c in todos_los_casos() if c.get("no_debe_contener")]

    assert con_prohibido, "ningún caso mide la no exposición de datos personales"
    assert any("LEG-" in p for c in con_prohibido for p in c["no_debe_contener"])


# ─────────────────────────────────────────────────────────────────────────────
# Los valores esperados de cálculo
# ─────────────────────────────────────────────────────────────────────────────


def test_los_valores_de_calculo_declaran_campo_y_tolerancia() -> None:
    """Un valor esperado sin tolerancia obliga a igualdad exacta de floats."""
    for caso in casos("calculos"):
        esperado = caso.get("valor_esperado")
        if esperado is None:
            continue
        assert "campo" in esperado and "valor" in esperado and "tolerancia" in esperado, (
            f"{caso['id']} declara `valor_esperado` incompleto"
        )


def test_el_caso_p2101a_espera_los_numeros_que_publica_el_readme() -> None:
    """Si alguien cambia el generador, la eval avisa que el README quedó mintiendo."""
    por_id = {c["id"]: c for c in casos("calculos")}

    assert por_id["calc-001"]["valor_esperado"]["valor"] == -1.43
    assert por_id["calc-002"]["valor_esperado"]["valor"] == 0.21
