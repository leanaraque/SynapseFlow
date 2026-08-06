"""Recuperación híbrida: semántica y léxica, porque fallan distinto.

La **vectorial** encuentra por significado: «adelgazamiento de pared» recupera
«pérdida de espesor» aunque no compartan una palabra. Falla justo donde el
término es arbitrario y no tiene sinónimos — `P-2101-A`, `API 570 §7.4` — porque
un identificador no significa nada.

La **léxica** (BM25) es exactamente al revés: acierta el identificador exacto y no
entiende que dos frases dicen lo mismo.

Un usuario de este dominio escribe las dos clases de consulta, a veces en la
misma pregunta, así que se combinan con `EnsembleRetriever`.

## El filtro de vigencia va en las DOS ramas

Es la trampa de este módulo. Filtrar por `vigencia: vigente` solo en la búsqueda
vectorial deja la puerta de atrás abierta: el corpus incluye un procedimiento
derogado, y la rama léxica lo recuperaría igual. El ensemble fusionaría ambas
listas y el fragmento derogado terminaría fundamentando una respuesta, después de
haber puesto el filtro «donde correspondía».

Por eso `construir_retriever` filtra el corpus en memoria **antes** de armar el
índice BM25, y hay un test que verifica que la rama léxica sola tampoco lo
devuelve.

## Por qué BM25 se arma en memoria

BM25 necesita las estadísticas de términos del corpus completo para puntuar: no
se puede calcular sobre el resultado de una consulta filtrada. El corpus de este
dominio son seis documentos y unas decenas de secciones, así que cargarlo en
proceso cuesta milisegundos. Con un corpus de otro orden habría que mover la
recuperación léxica a un motor que la soporte —Firestore no la tiene— y eso sería
un ADR nuevo.

Ver docs/plan/fases/F3-rag.md § F3.2
"""

from __future__ import annotations

import re
from typing import Any

from langchain_classic.retrievers import EnsembleRetriever
from langchain_core.callbacks import (
    AsyncCallbackManagerForRetrieverRun,
    CallbackManagerForRetrieverRun,
)
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field
from rank_bm25 import BM25Okapi

from synapseflow.persistence.vectorstore import FirestoreVectorStore
from synapseflow.rag.ingesta import leer_corpus, trocear

# Cuántos fragmentos devuelve cada rama antes de fusionar. Se piden más de los
# que se van a usar porque el ensemble reordena: si cada rama trajera solo los
# finales, la fusión no tendría con qué mejorar el orden.
CANDIDATOS_POR_RAMA = 10
FRAGMENTOS_FINALES = 6

# Peso de cada rama en la fusión. La vectorial pesa más porque la mayoría de las
# consultas son en lenguaje natural; la léxica existe para el caso en que el
# usuario escribe un identificador, donde acierta ella sola y el peso alcanza.
PESOS = (0.6, 0.4)

# Tokenizador. El corpus es normativa técnica en español: partir por `[a-z]+`
# dejaría «inspección» como «inspecci» + «n», y los acentos son la mitad de los
# términos del dominio.
PALABRAS = re.compile(r"[0-9a-záéíóúüñ]+")


def tokenizar(texto: str) -> list[str]:
    return PALABRAS.findall(texto.lower())


class BM25Retriever(BaseRetriever):
    """Recuperación léxica sobre `rank_bm25`.

    Implementación propia y no la de `langchain-community`: ese paquete no está
    instalado y no se va a instalar por un solo retriever. Ver
    docs/plan/00-convenciones.md § Qué NO hacer.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    documentos: list[Document] = Field(default_factory=list)
    k: int = FRAGMENTOS_FINALES
    indice: Any = None

    @classmethod
    def desde_documentos(
        cls, documentos: list[Document], k: int = FRAGMENTOS_FINALES
    ) -> BM25Retriever:
        """Arma el índice léxico. Los documentos ya tienen que venir filtrados."""
        tokenizados = [tokenizar(d.page_content) for d in documentos]
        # `BM25Okapi` divide por el largo promedio del corpus: con la lista vacía
        # eso es una división por cero al construir, no al consultar.
        indice = BM25Okapi(tokenizados) if tokenizados else None
        return cls(documentos=documentos, k=k, indice=indice)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        if self.indice is None or not self.documentos:
            return []

        puntajes = self.indice.get_scores(tokenizar(query))
        ordenados = sorted(
            zip(self.documentos, puntajes, strict=True), key=lambda par: par[1], reverse=True
        )
        # Un puntaje no positivo significa que el documento no aporta ningún
        # término discriminante. Devolverlo llenaría el cupo con ruido que
        # después compite en la fusión contra resultados vectoriales buenos.
        #
        # BM25 da IDF **negativo** a un término presente en casi todo el corpus:
        # no discrimina. Con un corpus muy chico eso puede dejar la lista vacía,
        # y está bien — significa que la consulta no distingue nada y la rama
        # semántica es la que tiene que responder.
        return [documento for documento, puntaje in ordenados[: self.k] if puntaje > 0]

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        """BM25 es cálculo en memoria: no hay nada que esperar.

        Se implementa igual porque el resto de la plataforma invoca por `ainvoke`
        y la implementación por defecto de `BaseRetriever` delegaría en un hilo
        para nada.
        """
        return self._get_relevant_documents(query, run_manager=None)  # type: ignore[arg-type]


def documentos_del_corpus(filtros: dict[str, Any] | None = None) -> list[Document]:
    """Fragmentos del corpus versionado que cumplen los filtros.

    Es la fuente de la rama léxica. Aplica los mismos filtros de igualdad que se
    le pasan a Firestore, para que las dos ramas vean el mismo universo.
    """
    fragmentos = [f for documento in leer_corpus() for f in trocear(documento)]

    for campo, valor in (filtros or {}).items():
        fragmentos = [f for f in fragmentos if f.metadata.get(campo) == valor]

    return fragmentos


def construir_retriever(
    almacen: FirestoreVectorStore,
    *,
    filtros: dict[str, Any] | None = None,
    k: int = FRAGMENTOS_FINALES,
) -> BaseRetriever:
    """Ensemble de recuperación vectorial y léxica, con los mismos filtros.

    Args:
        almacen: vector store ya construido con el modelo de embeddings.
        filtros: igualdades sobre metadatos. Se aplican **antes** de la búsqueda
            vectorial —filtrar después desperdicia cupo de resultados— y también
            sobre el corpus en memoria de la rama léxica.
        k: cuántos fragmentos devuelve el ensemble.
    """
    vectorial = almacen.as_retriever(
        search_kwargs={"k": CANDIDATOS_POR_RAMA, "filtros": filtros or {}}
    )
    lexico = BM25Retriever.desde_documentos(documentos_del_corpus(filtros), k=CANDIDATOS_POR_RAMA)

    # Sin `id_key`, el ensemble deduplica por contenido. Es lo correcto acá: dos
    # fragmentos con el mismo texto son el mismo fragmento, venga de la rama que
    # venga, y el corpus no lleva un id propio por fragmento.
    ensemble = EnsembleRetriever(retrievers=[vectorial, lexico], weights=list(PESOS))

    return _Acotado(interno=ensemble, k=k)


class _Acotado(BaseRetriever):
    """Envuelve un retriever para devolver a lo sumo `k` fragmentos.

    `EnsembleRetriever` no acepta un `k`: fusiona las listas de cada rama y
    devuelve la unión, que con dos ramas de diez candidatos puede llegar a
    veinte. Eso no es un detalle de prolijidad — cada fragmento entra entero al
    contexto del modelo, así que veinte triplican el presupuesto que el troceado
    calculó para seis.

    Se corta acá y no bajando el `k` de cada rama porque la fusión necesita
    candidatos de sobra para poder reordenar: si cada rama trajera seis, el
    ensemble no tendría con qué mejorar el orden.
    """

    interno: BaseRetriever
    k: int = FRAGMENTOS_FINALES

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        return self.interno.invoke(query)[: self.k]

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        return (await self.interno.ainvoke(query))[: self.k]


def construir_retriever_vigente(
    almacen: FirestoreVectorStore, *, tipo_documento: str | None = None, k: int = FRAGMENTOS_FINALES
) -> BaseRetriever:
    """El retriever que usa el dominio: solo normativa vigente.

    Existe como función aparte para que ningún call site tenga que acordarse de
    pasar `{"vigencia": "vigente"}`. Olvidarlo no rompe nada visible: devuelve
    resultados, y algunos citan un procedimiento derogado.
    """
    filtros: dict[str, Any] = {"vigencia": "vigente"}
    if tipo_documento:
        filtros["tipo_documento"] = tipo_documento
    return construir_retriever(almacen, filtros=filtros, k=k)
