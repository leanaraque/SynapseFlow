"""Contrato de la política de proveedores.

Lo que se verifica no es que la regla se cumpla —eso lo hace el gateway— sino que
la regla **exista como decisión aislada y testeable**, sin catálogo ni proveedor
de por medio. Una política que solo se puede ejercitar construyendo un gateway
está atada a su punto de aplicación, y el día que haya un segundo punto habrá dos
políticas.

Ver docs/plan/fases/F4-gobernanza.md § F4.4
"""

from __future__ import annotations

import pytest

from synapseflow.governance.politica import (
    NUNCA_SALE,
    PoliticaVioladaError,
    clasificaciones_que_nunca_salen,
    exigir_zero_training,
    puede_salir,
)
from synapseflow.ontology import get_ontology

# ─────────────────────────────────────────────────────────────────────────────
# Zero-training
# ─────────────────────────────────────────────────────────────────────────────


def test_un_proveedor_que_declara_la_politica_pasa() -> None:
    exigir_zero_training("gemini", declarado=True, exigido=True)


def test_un_proveedor_que_no_la_declara_es_rechazado() -> None:
    """La llamada FALLA en vez de degradar en silencio."""
    with pytest.raises(PoliticaVioladaError, match="zero_training"):
        exigir_zero_training("proveedor_dudoso", declarado=False, exigido=True)


def test_con_la_politica_desactivada_no_se_exige_nada() -> None:
    """Desactivarla es una decisión que alguien toma, no un default silencioso."""
    exigir_zero_training("proveedor_dudoso", declarado=False, exigido=False)


def test_el_mensaje_dice_que_es_una_declaracion_y_no_una_verificacion() -> None:
    """**Un sistema que dijera «verificado que no entrena» estaría mintiendo.**

    No hay forma programática de comprobar que una empresa no entrena con lo que
    recibe. Para un cliente regulado el respaldo es el contrato, y el catálogo es
    dónde queda anotado quién lo firmó. El mensaje tiene que ser honesto sobre la
    naturaleza de la garantía, porque es lo que alguien va a leer cuando le
    pregunten en una auditoría.
    """
    with pytest.raises(PoliticaVioladaError) as excinfo:
        exigir_zero_training("x", declarado=False, exigido=True)

    mensaje = str(excinfo.value)
    assert "DECLARACIÓN" in mensaje
    assert "contrato" in mensaje
    assert "no hay forma de comprobar" in mensaje.lower()


def test_el_mensaje_ofrece_las_dos_salidas() -> None:
    """Un error de gobernanza sin salida deja al usuario trabado."""
    with pytest.raises(PoliticaVioladaError) as excinfo:
        exigir_zero_training("x", declarado=False, exigido=True)

    mensaje = str(excinfo.value)
    assert "SYNAPSEFLOW_PROVIDER" in mensaje
    assert "desactivar la política" in mensaje


def test_la_politica_no_necesita_el_catalogo_para_testearse() -> None:
    """Es el punto de la inversión de dependencia.

    La política recibe el hecho y aplica la regla, en lugar de ir a buscarlo.
    Evita un import circular con `llm`, y deja explícito que este módulo decide y
    no averigua.
    """
    import ast
    from pathlib import Path

    from synapseflow.governance import politica

    # Se analiza el AST y no el texto: el propio docstring de `politica.py`
    # nombra `synapseflow.llm` para explicar por qué NO lo importa, y un `in`
    # sobre el código fuente lo contaría como una violación.
    arbol = ast.parse(Path(politica.__file__).read_text(encoding="utf-8"))
    importados = {
        nodo.module or "" for nodo in ast.walk(arbol) if isinstance(nodo, ast.ImportFrom)
    } | {
        alias.name
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Import)
        for alias in nodo.names
    }

    del_llm = sorted(m for m in importados if m.startswith("synapseflow.llm"))
    assert not del_llm, (
        f"la política importa del paquete llm ({del_llm}): eso reintroduce el "
        "ciclo que la inversión de dependencia existe para evitar"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Qué clasificación puede cruzar el perímetro
# ─────────────────────────────────────────────────────────────────────────────


def test_lo_restringido_nunca_sale_en_claro() -> None:
    """Es el piso del compromiso 5: para eso no hay contrato que alcance."""
    assert puede_salir(NUNCA_SALE) is False


@pytest.mark.parametrize("nivel", ["public", "internal", "confidential"])
def test_lo_de_abajo_del_umbral_si_sale(nivel: str) -> None:
    """La normativa es pública y los datos operativos no identifican a nadie.

    Tokenizar todo degradaría la respuesta sin proteger nada.
    """
    assert puede_salir(nivel) is True


def test_los_niveles_que_no_salen_se_derivan_de_la_ontologia() -> None:
    """Agregar un nivel por encima de `restricted` lo incluye automáticamente.

    Es lo contrario de tener que acordarse.
    """
    assert clasificaciones_que_nunca_salen() == ("restricted",)


def test_el_umbral_existe_en_la_ontologia() -> None:
    """Si alguien renombrara el nivel, `puede_salir` fallaría silenciosamente."""
    niveles = {n.id for n in get_ontology().classification_levels}
    assert NUNCA_SALE in niveles


def test_el_umbral_es_el_nivel_mas_alto_declarado() -> None:
    """Si mañana hubiera uno por encima, este test avisa que hay que revisarlo."""
    onto = get_ontology()
    maximo = max(n.rank for n in onto.classification_levels)

    assert onto.classification_rank(NUNCA_SALE) == maximo, (
        "hay un nivel de clasificación por encima de 'restricted': revisar si "
        "también debe tokenizarse y si el umbral sigue siendo el correcto"
    )
