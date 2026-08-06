"""Contrato de la recuperación híbrida.

Dos propiedades se verifican acá y la segunda es la que más importa:

1. Que las dos ramas aporten lo que la otra no puede. La léxica tiene que
   acertar el identificador exacto; la vectorial, el sinónimo.
2. **Que el filtro de vigencia valga en las DOS ramas.** Filtrarlo solo en la
   búsqueda vectorial deja la puerta de atrás abierta: la rama léxica
   recuperaría el procedimiento derogado igual, el ensemble fusionaría ambas
   listas y el fragmento derogado terminaría fundamentando una respuesta —
   después de haber puesto el filtro «donde correspondía».

Ver docs/plan/fases/F3-rag.md § F3.2
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from langchain_core.documents import Document

from synapseflow.config import Provider, Settings
from synapseflow.llm.gateway import Gateway
from synapseflow.persistence.client import get_client
from synapseflow.persistence.vectorstore import FirestoreVectorStore
from synapseflow.rag.retrievers import (
    BM25Retriever,
    construir_retriever,
    construir_retriever_vigente,
    documentos_del_corpus,
    tokenizar,
)


def doc(texto: str, **metadata: str) -> Document:
    base = {"doc_id": "D", "seccion": "1.1", "vigencia": "vigente"}
    base.update(metadata)
    return Document(page_content=texto, metadata=base)


# ─────────────────────────────────────────────────────────────────────────────
# La rama léxica
# ─────────────────────────────────────────────────────────────────────────────


def test_el_tokenizador_no_parte_las_palabras_con_acento() -> None:
    """El corpus es normativa técnica en español.

    Partir por `[a-z]+` dejaría «inspección» como «inspecci» + «n», y los
    acentos son la mitad de los términos del dominio.
    """
    assert tokenizar("Inspección de cañerías") == ["inspección", "de", "cañerías"]


def test_bm25_acierta_el_identificador_exacto() -> None:
    """Es lo que la rama vectorial no puede hacer: un TAG no significa nada."""
    retriever = BM25Retriever.desde_documentos(
        [
            doc("El activo P-2101-A midió 6,8 mm en la última campaña."),
            doc("La frecuencia de calibración de válvulas de alivio es anual."),
            doc("Los recipientes a presión se inspeccionan cada cinco años."),
        ]
    )

    resultados = retriever.invoke("P-2101-A")

    assert resultados, "BM25 no recuperó nada para un identificador exacto"
    assert "P-2101-A" in resultados[0].page_content


def test_bm25_no_devuelve_documentos_sin_ningun_termino_en_comun() -> None:
    """Un puntaje de cero es ruido que después compite en la fusión."""
    retriever = BM25Retriever.desde_documentos(
        [doc("espesor mínimo requerido"), doc("frecuencia de calibración")]
    )

    assert retriever.invoke("plancton bioluminiscente") == []


def test_bm25_sobre_un_corpus_vacio_no_explota() -> None:
    """`BM25Okapi` divide por el largo promedio del corpus: con la lista vacía
    eso es una división por cero **al construir**, no al consultar."""
    retriever = BM25Retriever.desde_documentos([])
    assert retriever.invoke("lo que sea") == []


async def test_la_rama_lexica_tiene_camino_async() -> None:
    """El resto de la plataforma invoca por `ainvoke`.

    El corpus lleva varios documentos a propósito: con uno solo, BM25 da IDF
    negativo para todos sus términos —un término presente en el 100 % del corpus
    no discrimina nada— y el filtro de puntaje positivo lo descarta. Es el
    comportamiento correcto del algoritmo, no un bug, pero hace que un test con
    un único documento no pruebe lo que parece.
    """
    retriever = BM25Retriever.desde_documentos(
        [
            doc("espesor mínimo requerido en cañerías de proceso"),
            doc("frecuencia de calibración de válvulas de alivio"),
            doc("criterios de aceptación de soldaduras"),
        ]
    )

    recuperados = await retriever.ainvoke("espesor mínimo")

    assert recuperados
    assert "espesor" in recuperados[0].page_content


# ─────────────────────────────────────────────────────────────────────────────
# El filtro que vale en las dos ramas
# ─────────────────────────────────────────────────────────────────────────────


def test_el_corpus_lexico_se_filtra_por_vigencia() -> None:
    """Si no, el derogado entra por la rama léxica con el filtro puesto."""
    todos = documentos_del_corpus()
    vigentes = documentos_del_corpus({"vigencia": "vigente"})

    assert len(vigentes) < len(todos), "el corpus de prueba no tiene ningún derogado"
    assert all(f.metadata["vigencia"] == "vigente" for f in vigentes)


def test_la_rama_lexica_sola_no_devuelve_normativa_derogada() -> None:
    """La verificación de la puerta de atrás, sobre la rama aislada.

    Se consulta con términos del documento derogado. Sin el filtro en esta rama,
    BM25 lo devolvería primero: es el que mejor coincide léxicamente.
    """
    derogados = [f for f in documentos_del_corpus() if f.metadata["vigencia"] == "derogado"]
    assert derogados, "el corpus tiene que incluir un derogado para que esto pruebe algo"

    consulta = derogados[0].page_content[:120]
    retriever = BM25Retriever.desde_documentos(documentos_del_corpus({"vigencia": "vigente"}))

    recuperados = retriever.invoke(consulta)
    assert all(f.metadata["vigencia"] == "vigente" for f in recuperados)


def test_el_corpus_lexico_se_puede_filtrar_por_tipo() -> None:
    codigos = documentos_del_corpus({"tipo_documento": "norma_internacional"})
    assert codigos
    assert {f.metadata["tipo_documento"] for f in codigos} == {"norma_internacional"}


# ─────────────────────────────────────────────────────────────────────────────
# El ensemble, contra el emulador
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def almacen(requiere_emulador: None) -> AsyncIterator[FirestoreVectorStore]:
    """Vector store con el corpus real indexado, con embeddings falsos."""
    from synapseflow.rag.ingesta import ingestar_corpus

    gateway = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE))
    coleccion = f"corpus_test_{uuid.uuid4().hex[:8]}"
    tienda = FirestoreVectorStore(gateway.embeddings(), collection=coleccion)

    await ingestar_corpus(tienda)

    yield tienda

    cliente = get_client()
    async for documento in cliente.collection(coleccion).stream():
        await documento.reference.delete()


@pytest.mark.emulator
async def test_el_ensemble_devuelve_fragmentos_citables(
    almacen: FirestoreVectorStore,
) -> None:
    retriever = construir_retriever_vigente(almacen)
    recuperados = await retriever.ainvoke("espesor por debajo del mínimo requerido")

    assert recuperados
    for fragmento in recuperados:
        assert fragmento.metadata.get("doc_id")
        assert fragmento.metadata.get("seccion")


@pytest.mark.emulator
async def test_el_ensemble_no_devuelve_normativa_derogada(
    almacen: FirestoreVectorStore,
) -> None:
    """La propiedad completa, con las dos ramas fusionadas.

    Es la que sostiene que una respuesta del sistema no puede fundamentarse en un
    procedimiento que ya no rige.
    """
    retriever = construir_retriever_vigente(almacen)

    for consulta in (
        "criterio de aceptación de espesores",
        "procedimiento interno de integridad",
        "evaluación de aptitud para el servicio",
    ):
        recuperados = await retriever.ainvoke(consulta)
        vigencias = {f.metadata.get("vigencia") for f in recuperados}
        assert vigencias <= {"vigente"}, (
            f"la consulta «{consulta}» recuperó normativa derogada: {vigencias}"
        )


@pytest.mark.emulator
async def test_el_ensemble_respeta_el_tope_de_fragmentos(
    almacen: FirestoreVectorStore,
) -> None:
    """`EnsembleRetriever` no acepta un `k`: devuelve la unión de las dos ramas.

    Con dos ramas de diez candidatos eso puede llegar a veinte fragmentos, y cada
    uno entra entero al contexto del modelo. Veinte triplican el presupuesto que
    el troceado calculó para seis.
    """
    retriever = construir_retriever_vigente(almacen, k=3)
    recuperados = await retriever.ainvoke("inspección de espesores y criterios de aceptación")

    assert 0 < len(recuperados) <= 3


@pytest.mark.emulator
async def test_el_filtro_por_tipo_de_documento_se_aplica(
    almacen: FirestoreVectorStore,
) -> None:
    retriever = construir_retriever_vigente(almacen, tipo_documento="norma_internacional")
    recuperados = await retriever.ainvoke("frecuencia de inspección")

    assert recuperados
    assert {f.metadata["tipo_documento"] for f in recuperados} == {"norma_internacional"}


@pytest.mark.emulator
async def test_el_hibrido_encuentra_lo_que_una_rama_sola_no(
    almacen: FirestoreVectorStore,
) -> None:
    """La razón de ser del ensemble.

    Una referencia con número de sección es léxica pura: el significado de «7.4»
    no ayuda. El híbrido tiene que recuperarla igual que recupera una pregunta en
    lenguaje natural.
    """
    retriever = construir_retriever_vigente(almacen)

    por_seccion = await retriever.ainvoke("API-570-2016 §7.4")
    por_significado = await retriever.ainvoke("adelgazamiento de la pared del componente")

    assert por_seccion, "el híbrido no recuperó nada para una referencia de sección"
    assert por_significado, "el híbrido no recuperó nada para una consulta semántica"


@pytest.mark.emulator
async def test_sin_filtros_el_derogado_si_aparece(almacen: FirestoreVectorStore) -> None:
    """El control negativo: sin filtro, el derogado está y es recuperable.

    Sin esto, los tests de arriba pasarían igual si la ingesta no hubiera
    indexado nunca el documento derogado.
    """
    derogados = [f for f in documentos_del_corpus() if f.metadata["vigencia"] == "derogado"]
    retriever = construir_retriever(almacen, filtros=None)

    recuperados = await retriever.ainvoke(derogados[0].page_content[:120])
    vigencias = {f.metadata.get("vigencia") for f in recuperados}

    assert "derogado" in vigencias, (
        "sin filtro el derogado tendría que aparecer: si no, los tests de "
        "vigencia no están probando nada"
    )
