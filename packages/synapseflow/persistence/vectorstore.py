"""`VectorStore` de LangChain sobre la búsqueda vectorial nativa de Firestore.

Implementación propia y no `langchain-google-firestore`, porque ese paquete
pinnea `langchain-core <1.0.0`. Ver docs/adr/0002-integraciones-firestore-propias.md

La plataforma es async de punta a punta: la API es FastAPI, el grafo se ejecuta
con `astream_events` y el cliente de Firestore es `AsyncClient`. Los métodos
sincrónicos que la interfaz declara abstractos existen para poder instanciar la
clase, pero fallan con un mensaje explícito en lugar de bloquear el event loop.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from synapseflow.persistence.client import Collections, get_client

# Campo donde vive el vector. Tiene que coincidir con firestore.indexes.json:
# si no coinciden, Firestore rechaza la consulta pidiendo un índice vectorial.
VECTOR_FIELD = "embedding"
CONTENT_FIELD = "contenido"
DISTANCE_FIELD = "_distancia"

# Firestore acepta hasta 2048 dimensiones en un índice vectorial. Los modelos de
# embeddings de Gemini y OpenAI entran holgadamente, pero conviene verificarlo al
# indexar y no descubrirlo con un error del servidor a mitad de una carga.
MAX_DIMENSIONS = 2048


class FirestoreVectorStore(VectorStore):
    """Vector store respaldado por una colección de Firestore.

    El documento de Firestore guarda el texto, el vector y los metadatos
    aplanados al nivel raíz. Aplanarlos —en lugar de anidarlos bajo `metadata`—
    es lo que permite combinar filtros de igualdad con `find_nearest` en la misma
    consulta, que es el caso de uso real: buscar semánticamente *solo* dentro de
    la normativa vigente.
    """

    def __init__(
        self,
        embedding: Embeddings,
        *,
        collection: str = Collections.CORPUS_CHUNKS,
        client: firestore.AsyncClient | None = None,
        distance_measure: DistanceMeasure = DistanceMeasure.COSINE,
    ) -> None:
        self._embedding = embedding
        self._collection_name = collection
        self._client = client or get_client()
        self._distance_measure = distance_measure

    @property
    def embeddings(self) -> Embeddings:
        return self._embedding

    @property
    def _collection(self) -> Any:
        return self._client.collection(self._collection_name)

    # ── Escritura ────────────────────────────────────────────────────────────

    async def aadd_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """Indexa textos con sus metadatos.

        El id se deriva del contenido cuando no se provee, lo que hace la
        ingesta idempotente: correr el script de carga dos veces sobreescribe los
        mismos documentos en lugar de duplicar el corpus. Sin esto, cada
        reindexado ensucia la recuperación con fragmentos repetidos que compiten
        entre sí en el ranking.
        """
        textos = list(texts)
        if not textos:
            return []

        metas = list(metadatas or [{} for _ in textos])
        if len(metas) != len(textos):
            raise ValueError(
                f"llegaron {len(textos)} textos y {len(metas)} metadatos: "
                "tienen que ser la misma cantidad"
            )

        vectores = await self._embedding.aembed_documents(textos)
        self._validar_dimensiones(vectores)

        identificadores = ids or [_id_por_contenido(t, m) for t, m in zip(textos, metas)]

        # Firestore admite hasta 500 operaciones por batch. Los corpus de
        # normativa superan ese número con facilidad.
        for lote in _en_lotes(list(zip(identificadores, textos, metas, vectores)), 400):
            batch = self._client.batch()
            for doc_id, texto, meta, vector in lote:
                batch.set(
                    self._collection.document(doc_id),
                    {
                        CONTENT_FIELD: texto,
                        VECTOR_FIELD: Vector(vector),
                        **_limpiar_metadatos(meta),
                        "indexado_en": firestore.SERVER_TIMESTAMP,
                    },
                )
            await batch.commit()

        return identificadores

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        raise NotImplementedError(
            "FirestoreVectorStore es async: usar `aadd_texts`. "
            "El camino sincrónico bloquearía el event loop de la API."
        )

    async def adelete(self, ids: list[str] | None = None, **kwargs: Any) -> bool | None:
        if not ids:
            return False
        for lote in _en_lotes(ids, 400):
            batch = self._client.batch()
            for doc_id in lote:
                batch.delete(self._collection.document(doc_id))
            await batch.commit()
        return True

    def delete(self, ids: list[str] | None = None, **kwargs: Any) -> bool | None:
        raise NotImplementedError("FirestoreVectorStore es async: usar `adelete`.")

    # ── Lectura ──────────────────────────────────────────────────────────────

    async def asimilarity_search(
        self, query: str, k: int = 4, **kwargs: Any
    ) -> list[Document]:
        con_puntaje = await self.asimilarity_search_with_score(query, k, **kwargs)
        return [doc for doc, _ in con_puntaje]

    async def asimilarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        *,
        filtros: dict[str, Any] | None = None,
        umbral_distancia: float | None = None,
        **kwargs: Any,
    ) -> list[tuple[Document, float]]:
        """Búsqueda semántica con filtros de igualdad opcionales.

        Args:
            query: consulta en lenguaje natural.
            k: cantidad de fragmentos a devolver.
            filtros: igualdades sobre metadatos, por ejemplo
                `{"vigencia": "vigente"}`. Se aplican *antes* de la búsqueda
                vectorial, así que no desperdician cupo de `k` en documentos que
                después habría que descartar.
            umbral_distancia: descarta resultados por encima de esta distancia
                coseno. Es la palanca que hace que el sistema pueda decir "no
                encontré fundamento" en lugar de devolver el fragmento menos malo.
        """
        vector = await self._embedding.aembed_query(query)
        self._validar_dimensiones([vector])

        consulta: Any = self._collection
        for campo, valor in (filtros or {}).items():
            consulta = consulta.where(filter=FieldFilter(campo, "==", valor))

        vector_query = consulta.find_nearest(
            vector_field=VECTOR_FIELD,
            query_vector=Vector(vector),
            limit=k,
            distance_measure=self._distance_measure,
            distance_result_field=DISTANCE_FIELD,
            **({"distance_threshold": umbral_distancia} if umbral_distancia is not None else {}),
        )

        snapshots = await vector_query.get()
        return [_a_documento(s) for s in snapshots]

    def similarity_search(self, query: str, k: int = 4, **kwargs: Any) -> list[Document]:
        raise NotImplementedError(
            "FirestoreVectorStore es async: usar `asimilarity_search`. "
            "Los retrievers de LangChain toman el camino async automáticamente "
            "cuando se los invoca con `ainvoke`."
        )

    async def aget_by_ids(self, ids: Sequence[str], /) -> list[Document]:
        refs = [self._collection.document(i) for i in ids]
        snapshots = await self._client.get_all(refs)
        return [_a_documento(s)[0] for s in snapshots if s.exists]

    async def acount(self, filtros: dict[str, Any] | None = None) -> int:
        """Cantidad de fragmentos indexados. Lo usa el script de ingesta para
        reportar y el health check para detectar un índice vacío."""
        consulta: Any = self._collection
        for campo, valor in (filtros or {}).items():
            consulta = consulta.where(filter=FieldFilter(campo, "==", valor))
        resultado = await consulta.count().get()
        return int(resultado[0][0].value)

    # ── Constructores ────────────────────────────────────────────────────────

    @classmethod
    async def afrom_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> FirestoreVectorStore:
        store = cls(embedding, **kwargs)
        await store.aadd_texts(texts, metadatas, ids=ids)
        return store

    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: list[dict[str, Any]] | None = None,
        *,
        ids: list[str] | None = None,
        **kwargs: Any,
    ) -> FirestoreVectorStore:
        raise NotImplementedError(
            "FirestoreVectorStore es async: usar `afrom_texts`."
        )

    # ── Interno ──────────────────────────────────────────────────────────────

    @staticmethod
    def _validar_dimensiones(vectores: Sequence[Sequence[float]]) -> None:
        for v in vectores:
            if len(v) > MAX_DIMENSIONS:
                raise ValueError(
                    f"el modelo de embeddings devuelve {len(v)} dimensiones y "
                    f"Firestore admite hasta {MAX_DIMENSIONS} en un índice "
                    "vectorial. Reducir la dimensionalidad del modelo."
                )
            if not v:
                raise ValueError("el modelo de embeddings devolvió un vector vacío")


def _a_documento(snapshot: Any) -> tuple[Document, float]:
    """Convierte un snapshot de Firestore en Document + distancia."""
    datos = snapshot.to_dict() or {}
    contenido = datos.pop(CONTENT_FIELD, "")
    distancia = float(datos.pop(DISTANCE_FIELD, 0.0) or 0.0)
    datos.pop(VECTOR_FIELD, None)
    datos.pop("indexado_en", None)
    return (
        Document(id=snapshot.id, page_content=contenido, metadata=datos),
        distancia,
    )


def _limpiar_metadatos(meta: dict[str, Any]) -> dict[str, Any]:
    """Quita claves que colisionarían con los campos reservados del documento."""
    reservados = {CONTENT_FIELD, VECTOR_FIELD, DISTANCE_FIELD, "indexado_en"}
    return {k: v for k, v in meta.items() if k not in reservados and v is not None}


def _id_por_contenido(texto: str, meta: dict[str, Any]) -> str:
    """Id determinístico a partir del contenido y de la identidad del fragmento.

    Incluir `doc_id` y `seccion` además del texto evita que dos fragmentos
    idénticos de documentos distintos —una cláusula repetida entre una norma y un
    procedimiento interno que la transcribe— colapsen en un solo registro y se
    pierda una de las dos citas.
    """
    semilla = f"{meta.get('doc_id', '')}|{meta.get('seccion', '')}|{texto}"
    return hashlib.sha256(semilla.encode("utf-8")).hexdigest()[:32]


def _en_lotes(items: list[Any], tamano: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), tamano):
        yield items[i : i + tamano]
