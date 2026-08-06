"""Contrato de la ingesta del corpus.

El test que importa es que **ningún fragmento quede sin sección**. Un fragmento
sin sección no se puede citar, y uno que se cita mal es peor que uno que no se
cita: una referencia con formato de rigor y contenido equivocado es exactamente
lo que un revisor no va a verificar.

El resto de los tests protegen la propiedad de la que depende esa: que el
troceado respete los límites de las cláusulas en lugar de cortar cada N
caracteres.

Ver docs/plan/fases/F3-rag.md § F3.1
"""

from __future__ import annotations

from pathlib import Path

import pytest

from synapseflow.rag import ingesta
from synapseflow.rag.ingesta import (
    MAXIMO_POR_FRAGMENTO,
    DocumentoFuente,
    IngestaError,
    leer_corpus,
    leer_documento,
    trocear,
)

DOCUMENTO = """---
doc_id: TEST-001
titulo: Documento de prueba
tipo_documento: procedimiento_interno
vigencia: vigente
---

## 3.1 · Alcance

Este procedimiento cubre la medición de espesores por ultrasonido.

## 7.4 · Criterio de aceptación

Un componente cuyo espesor sea inferior al mínimo requerido se retira de
servicio o se somete a evaluación de aptitud.
"""


@pytest.fixture
def documento(tmp_path: Path) -> DocumentoFuente:
    ruta = tmp_path / "TEST-001.md"
    ruta.write_text(DOCUMENTO, encoding="utf-8")
    return leer_documento(ruta)


# ─────────────────────────────────────────────────────────────────────────────
# Lo que no se puede tolerar
# ─────────────────────────────────────────────────────────────────────────────


def test_ningun_fragmento_queda_sin_seccion(documento: DocumentoFuente) -> None:
    """**El test clave de la fase.** Sin sección no hay cita posible."""
    for fragmento in trocear(documento):
        assert fragmento.metadata.get("seccion"), (
            f"un fragmento de {documento.doc_id} quedó sin sección: no se puede citar"
        )


def test_ningun_fragmento_del_corpus_real_queda_sin_seccion() -> None:
    """Lo mismo, sobre los seis documentos versionados."""
    for documento in leer_corpus():
        for fragmento in trocear(documento):
            assert fragmento.metadata["seccion"]
            assert fragmento.metadata["doc_id"]
            assert fragmento.metadata["vigencia"]


def test_un_documento_sin_secciones_es_un_error_de_ingesta(tmp_path: Path) -> None:
    """Indexarlo con la sección vacía haría que el verificador de F3.4 rechace
    todas sus respuestas sin explicar por qué."""
    ruta = tmp_path / "SIN-SECCIONES.md"
    ruta.write_text(
        "---\ndoc_id: X\ntitulo: T\ntipo_documento: t\nvigencia: vigente\n---\n\nTexto suelto.\n",
        encoding="utf-8",
    )

    with pytest.raises(IngestaError, match="sección"):
        trocear(leer_documento(ruta))


def test_un_documento_sin_frontmatter_no_se_lee(tmp_path: Path) -> None:
    ruta = tmp_path / "SIN-CABECERA.md"
    ruta.write_text("## 1.1 · Algo\n\nTexto.\n", encoding="utf-8")

    with pytest.raises(IngestaError, match="frontmatter"):
        leer_documento(ruta)


def test_un_frontmatter_incompleto_nombra_lo_que_falta(tmp_path: Path) -> None:
    ruta = tmp_path / "INCOMPLETO.md"
    ruta.write_text("---\ndoc_id: X\ntitulo: T\n---\n\n## 1.1 · Algo\n\nTexto.\n", encoding="utf-8")

    with pytest.raises(IngestaError, match="vigencia"):
        leer_documento(ruta)


# ─────────────────────────────────────────────────────────────────────────────
# El troceado respeta las cláusulas
# ─────────────────────────────────────────────────────────────────────────────


def test_cada_seccion_produce_su_propio_fragmento(documento: DocumentoFuente) -> None:
    """Cortar cada mil caracteres partiría la 7.4 por la mitad."""
    secciones = [f.metadata["seccion"] for f in trocear(documento)]
    assert secciones == ["3.1", "7.4"]


def test_el_fragmento_no_mezcla_texto_de_dos_secciones(documento: DocumentoFuente) -> None:
    """Es el error que produce una cita con el número de sección equivocado."""
    fragmentos = {f.metadata["seccion"]: f.page_content for f in trocear(documento)}

    assert "ultrasonido" in fragmentos["3.1"]
    assert "ultrasonido" not in fragmentos["7.4"]
    assert "evaluación de aptitud" in fragmentos["7.4"]


def test_el_encabezado_se_indexa_con_el_cuerpo(documento: DocumentoFuente) -> None:
    """Hace que «criterio de aceptación» recupere la sección que se llama así
    aunque el cuerpo no repita esas palabras."""
    fragmentos = {f.metadata["seccion"]: f.page_content for f in trocear(documento)}
    assert "Criterio de aceptación" in fragmentos["7.4"]
    assert "TEST-001 §7.4" in fragmentos["7.4"]


def test_una_seccion_larga_se_subdivide_conservando_su_numero(tmp_path: Path) -> None:
    """Los subfragmentos son la misma cláusula: citarlos por separado rompería
    la referencia."""
    largo = "Párrafo de relleno con contenido técnico suficiente. " * 120
    ruta = tmp_path / "LARGO.md"
    ruta.write_text(
        f"---\ndoc_id: L\ntitulo: T\ntipo_documento: t\nvigencia: vigente\n---\n\n"
        f"## 9.9 · Sección extensa\n\n{largo}\n",
        encoding="utf-8",
    )

    fragmentos = trocear(leer_documento(ruta))

    assert len(fragmentos) > 1, "una sección larga tendría que subdividirse"
    assert {f.metadata["seccion"] for f in fragmentos} == {"9.9"}
    assert all(f.metadata["partes"] == len(fragmentos) for f in fragmentos)


def test_ningun_fragmento_del_corpus_excede_el_maximo() -> None:
    """El perfil `synthesis` recibe seis por consulta: el tamaño es presupuesto
    de contexto, no una preferencia estética."""
    for documento in leer_corpus():
        for fragmento in trocear(documento):
            assert len(fragmento.page_content) <= MAXIMO_POR_FRAGMENTO + 100


# ─────────────────────────────────────────────────────────────────────────────
# El corpus versionado
# ─────────────────────────────────────────────────────────────────────────────


def test_el_corpus_tiene_los_seis_documentos() -> None:
    assert len(leer_corpus()) == 6


def test_el_corpus_incluye_un_documento_derogado() -> None:
    """Existe a propósito: es lo que permite que el test de vigencia pruebe algo."""
    vigencias = {d.vigencia for d in leer_corpus()}
    assert "derogado" in vigencias
    assert "vigente" in vigencias


def test_el_documento_derogado_produce_fragmentos_marcados() -> None:
    """El filtro por vigencia se aplica sobre la metadata del fragmento, no del
    archivo: si el troceado perdiera el campo, el derogado se recuperaría."""
    derogados = [d for d in leer_corpus() if d.vigencia == "derogado"]
    assert derogados

    for documento in derogados:
        for fragmento in trocear(documento):
            assert fragmento.metadata["vigencia"] == "derogado"


def test_los_doc_id_no_se_repiten() -> None:
    """Dos documentos con el mismo id harían ambiguas todas sus citas."""
    ids = [d.doc_id for d in leer_corpus()]
    assert len(ids) == len(set(ids))


def test_un_directorio_sin_corpus_falla_con_mensaje_util(tmp_path: Path) -> None:
    with pytest.raises(IngestaError, match="clon está incompleto"):
        leer_corpus(tmp_path)


def test_la_ruta_por_defecto_apunta_al_corpus_versionado() -> None:
    """Es fuente, no derivado: los .md están en el repositorio a propósito."""
    assert ingesta.CORPUS.is_dir()
    assert (ingesta.CORPUS / "API-570.md").exists()
