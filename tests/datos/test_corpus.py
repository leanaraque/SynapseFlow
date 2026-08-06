"""El corpus de normativa tiene que ser citable e ingestable.

`data/corpus/` es fuente versionada, no derivado, así que estos tests leen los
archivos reales del repositorio.

Verifican tres cosas que fases posteriores dan por sentadas:

- **Frontmatter válido y coherente con la ontología.** La ingesta de F3.1 deriva
  de él `doc_id`, `tipo_documento` y `vigencia`, y el vector store filtra por
  esos campos. Un valor fuera del enum produce un fragmento que nunca aparece en
  una búsqueda filtrada, sin ningún error visible.
- **Secciones numeradas.** La cita del sistema es `documento §sección`; sin
  encabezados numerados no hay nada que citar.
- **Un documento derogado que contradiga al vigente.** Es lo que le da sentido
  al test de vigencia de F3.5.

El plan no incluía tests para el corpus: la verificación de F0.2 era
`ls data/corpus/*.md` y una revisión a ojo del frontmatter. Ningún commit de los
43 lo automatiza, y una edición que rompa el frontmatter no fallaría en ningún
lado hasta la ingesta.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from synapseflow.ontology import get_ontology

RAIZ = Path(__file__).resolve().parents[2]
CORPUS = RAIZ / "data" / "corpus"

# Encabezado de sección: '## 7.4 · Espesor por debajo del mínimo requerido'.
# El separador se deja abierto para no atarse al carácter exacto.
SECCION = re.compile(r"^## ([\d.]+)\s*.\s*(.+)$", re.MULTILINE)

# Cita en la documentación: 'API-570-2016 §7.4'.
CITA = re.compile(r"([A-Z][A-Z0-9-]{3,})\s*§\s*([\d.]+)")

CLAVES_OBLIGATORIAS = ("doc_id", "titulo", "tipo_documento", "vigencia")


def _documentos() -> list[Path]:
    return sorted(CORPUS.glob("*.md"))


def _frontmatter(ruta: Path) -> dict[str, Any]:
    texto = ruta.read_text(encoding="utf-8")
    encontrado = re.match(r"^---\n(.*?)\n---\n", texto, re.DOTALL)
    assert encontrado is not None, f"{ruta.name} no empieza con un bloque de frontmatter"
    datos = yaml.safe_load(encontrado.group(1))
    assert isinstance(datos, dict), f"el frontmatter de {ruta.name} no es un mapeo"
    return datos


@pytest.fixture(scope="session")
def corpus() -> dict[str, tuple[dict[str, Any], str]]:
    """Corpus indexado por `doc_id`: frontmatter y texto completo."""
    return {
        _frontmatter(r)["doc_id"]: (_frontmatter(r), r.read_text(encoding="utf-8"))
        for r in _documentos()
    }


def test_el_corpus_no_esta_vacio() -> None:
    assert _documentos(), (
        f"no hay documentos en {CORPUS}. Sin corpus no hay nada que citar y el "
        "compromiso 4 no se puede ejercitar."
    )


@pytest.mark.parametrize("ruta", _documentos(), ids=lambda p: p.name)
def test_todo_documento_tiene_frontmatter_completo(ruta: Path) -> None:
    datos = _frontmatter(ruta)
    faltantes = [c for c in CLAVES_OBLIGATORIAS if not datos.get(c)]
    assert not faltantes, f"{ruta.name}: faltan claves en el frontmatter: {faltantes}"


@pytest.mark.parametrize("ruta", _documentos(), ids=lambda p: p.name)
def test_el_frontmatter_usa_los_valores_de_la_ontologia(ruta: Path) -> None:
    """`tipo_documento` y `vigencia` son enums de `standard_document`.

    Se contrastan contra la ontología y no contra una lista repetida acá: si
    alguien agrega un tipo de documento al YAML, este test lo acepta solo.
    """
    entidad = get_ontology().entity("standard_document")
    admitidos = {p.name: p.values for p in entidad.properties if p.values}
    datos = _frontmatter(ruta)

    for campo in ("tipo_documento", "vigencia"):
        assert datos[campo] in (admitidos[campo] or []), (
            f"{ruta.name}: {campo}='{datos[campo]}' no está entre los valores "
            f"que declara la ontología: {admitidos[campo]}"
        )


@pytest.mark.parametrize("ruta", _documentos(), ids=lambda p: p.name)
def test_todo_documento_tiene_secciones_numeradas(ruta: Path) -> None:
    """Sin número de sección no hay cita posible, y sin cita no hay respuesta."""
    secciones = SECCION.findall(ruta.read_text(encoding="utf-8"))
    assert secciones, (
        f"{ruta.name} no tiene ningún encabezado con el formato "
        "'## N.N · Título'. La cita del sistema es 'documento §sección'."
    )

    numeros = [n for n, _ in secciones]
    repetidos = sorted({n for n in numeros if numeros.count(n) > 1})
    assert not repetidos, f"{ruta.name} repite números de sección: {repetidos}"


def test_hay_un_documento_derogado(corpus: dict[str, tuple[dict[str, Any], str]]) -> None:
    """Sin un derogado en el corpus, el test de vigencia de F3.5 no prueba nada."""
    derogados = [d for d, (fm, _) in corpus.items() if fm["vigencia"] == "derogado"]
    assert derogados, (
        "el corpus no tiene ningún documento derogado, así que no se puede "
        "verificar que el sistema se niegue a citarlo como fundamento vigente"
    )


def test_el_derogado_contradice_al_vigente(
    corpus: dict[str, tuple[dict[str, Any], str]],
) -> None:
    """Un derogado que dijera lo mismo que el vigente no distinguiría nada.

    Recuperarlo daría la misma respuesta, así que un sistema que ignora la
    vigencia y uno que la respeta serían indistinguibles. La contradicción es lo
    que convierte el test de F3.5 en una prueba.
    """
    vigente = corpus["PROC-INT-014"][1]
    derogado = corpus["PROC-INT-009"][1]

    # Las marcas se eligen de modo que cada una aparezca en UNO SOLO de los dos
    # documentos. No alcanza con buscar 'cinco por ciento': el vigente también
    # lo menciona, al declarar qué criterio derogó. Una marca compartida deja
    # pasar dos documentos idénticos, que es justamente lo que hay que impedir.
    assert "no admite tolerancia" in vigente, (
        "PROC-INT-014 §3.2 debe negar explícitamente la tolerancia sobre t_min"
    )
    assert "Se admite continuar la operación" in derogado, (
        "PROC-INT-009 §3.2 debe admitir el criterio que el vigente derogó"
    )
    assert "no admite tolerancia" not in derogado, (
        "el documento derogado dice lo mismo que el vigente. Recuperarlo daría "
        "la misma respuesta, así que un sistema que respeta la vigencia y uno "
        "que la ignora serían indistinguibles."
    )
    assert "PROC-INT-009" in vigente, (
        "el procedimiento vigente debe declarar a cuál reemplaza, o la relación "
        "entre ambos solo existe en la cabeza de quien escribió el corpus"
    )


def test_las_citas_de_la_documentacion_existen_en_el_corpus(
    corpus: dict[str, tuple[dict[str, Any], str]],
) -> None:
    """Toda cita que publican los READMEs tiene que tener respaldo real.

    Las citas se extraen del texto en lugar de repetirse acá: si alguien agrega
    una cita a la documentación, este test exige que el corpus la sostenga. Es la
    misma regla que el proyecto le impone al agente —sin fundamento no se
    afirma— aplicada a su propia documentación.
    """
    secciones_por_doc = {
        doc_id: {n for n, _ in SECCION.findall(texto)} for doc_id, (_, texto) in corpus.items()
    }

    faltantes: list[str] = []
    encontradas = 0
    for readme in ("README.md", "README.es.md"):
        for doc_id, seccion in CITA.findall((RAIZ / readme).read_text(encoding="utf-8")):
            encontradas += 1
            if seccion not in secciones_por_doc.get(doc_id, set()):
                faltantes.append(f"{readme} cita {doc_id} §{seccion}")

    assert encontradas, "no se encontró ninguna cita en los READMEs: ¿cambió el formato?"
    assert not faltantes, "hay citas publicadas sin respaldo en el corpus:\n  " + "\n  ".join(
        faltantes
    )
