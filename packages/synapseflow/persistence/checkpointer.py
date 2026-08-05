"""`BaseCheckpointSaver` de LangGraph sobre Firestore.

Es la pieza que hace posible el human-in-the-loop asincrónico: cuando el grafo se
detiene en un gate de aprobación, todo su estado queda acá. La instancia de Cloud
Run que lo ejecutaba puede morir; horas después, otra instancia reanuda desde el
mismo checkpoint con los argumentos de la acción intactos.

Ver docs/adr/0005-hitl-con-interrupt-de-langgraph.md

## Esquema

Tres colecciones, siguiendo la misma separación que usan los checkpointers de
LangGraph para backends SQL:

    lg_checkpoints          un documento por checkpoint, sin los valores de canal
    lg_checkpoint_blobs     un documento por (canal, versión)
    lg_checkpoint_writes    un documento por escritura pendiente de una tarea

Los valores de canal van aparte y no dentro del checkpoint por el límite de
1 MiB por documento de Firestore. El canal `messages` de una conversación larga
lo supera; separado por canal y versión, cada documento crece de forma acotada y
además no se reescribe lo que no cambió en ese paso.

Los ids de documento son hashes de la clave compuesta, porque un `thread_id` o un
`checkpoint_ns` pueden contener caracteres que Firestore no admite en un id. Los
componentes quedan como campos para poder consultarlos.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

from synapseflow.persistence.client import Collections, get_client

BLOBS_COLLECTION = "lg_checkpoint_blobs"


class FirestoreSaver(BaseCheckpointSaver[str]):
    """Checkpointer durable respaldado por Firestore.

    Async only: la plataforma corre sobre FastAPI y el cliente de Firestore es
    `AsyncClient`. Los métodos sincrónicos de la interfaz fallan con un mensaje
    explícito en lugar de bloquear el event loop.
    """

    def __init__(self, client: firestore.AsyncClient | None = None) -> None:
        super().__init__()
        self._client = client or get_client()

    # ── Lectura ──────────────────────────────────────────────────────────────

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        if checkpoint_id:
            snapshot = await self._checkpoints.document(
                _clave(thread_id, checkpoint_ns, checkpoint_id)
            ).get()
            if not snapshot.exists:
                return None
            documento = snapshot.to_dict() or {}
        else:
            # Sin checkpoint_id explícito: el más reciente del hilo. El id de
            # checkpoint de LangGraph es un UUID v6, monótono en el tiempo, así
            # que ordenar por id descendente da el último.
            consulta = (
                self._checkpoints.where(filter=FieldFilter("thread_id", "==", thread_id))
                .where(filter=FieldFilter("checkpoint_ns", "==", checkpoint_ns))
                .order_by("checkpoint_id", direction=firestore.Query.DESCENDING)
                .limit(1)
            )
            documentos = [d async for d in consulta.stream()]
            if not documentos:
                return None
            documento = documentos[0].to_dict() or {}
            checkpoint_id = documento["checkpoint_id"]

        return await self._armar_tupla(thread_id, checkpoint_ns, checkpoint_id, documento)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        # `filter` sombrea un builtin: es la firma de BaseCheckpointSaver y no
        # se puede cambiar sin romper el contrato con LangGraph.
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Historial de checkpoints, del más reciente al más antiguo.

        Es lo que alimenta la vista de trazabilidad de la consola: permite
        reconstruir el razonamiento que llevó a una propuesta de acción, no solo
        el hecho de que se propuso.
        """
        consulta: Any = self._checkpoints

        if config is not None:
            thread_id = config["configurable"]["thread_id"]
            consulta = consulta.where(filter=FieldFilter("thread_id", "==", thread_id))
            if (ns := config["configurable"].get("checkpoint_ns")) is not None:
                consulta = consulta.where(filter=FieldFilter("checkpoint_ns", "==", ns))

        # Los metadatos que se pueden filtrar están desnormalizados como campos
        # al escribir: `source` y `step`. El resto viaja serializado y no es
        # consultable, que es una limitación conocida de este backend.
        for campo, valor in (filter or {}).items():
            if campo in ("source", "step"):
                consulta = consulta.where(filter=FieldFilter(f"meta_{campo}", "==", valor))

        consulta = consulta.order_by("checkpoint_id", direction=firestore.Query.DESCENDING)

        if before is not None and (tope := get_checkpoint_id(before)):
            consulta = consulta.where(filter=FieldFilter("checkpoint_id", "<", tope))

        if limit is not None:
            consulta = consulta.limit(limit)

        async for snapshot in consulta.stream():
            documento = snapshot.to_dict() or {}
            yield await self._armar_tupla(
                documento["thread_id"],
                documento["checkpoint_ns"],
                documento["checkpoint_id"],
                documento,
            )

    async def _armar_tupla(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        documento: dict[str, Any],
    ) -> CheckpointTuple:
        checkpoint: Checkpoint = self.serde.loads_typed(
            (documento["checkpoint_type"], documento["checkpoint"])
        )
        metadata: CheckpointMetadata = self.serde.loads_typed(
            (documento["metadata_type"], documento["metadata"])
        )

        canales = await self._cargar_blobs(
            thread_id, checkpoint_ns, checkpoint.get("channel_versions", {})
        )
        escrituras = await self._cargar_escrituras(thread_id, checkpoint_ns, checkpoint_id)

        padre = documento.get("parent_checkpoint_id")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint={**checkpoint, "channel_values": canales},
            metadata=metadata,
            pending_writes=escrituras,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": padre,
                    }
                }
                if padre
                else None
            ),
        )

    async def _cargar_blobs(
        self, thread_id: str, checkpoint_ns: str, channel_versions: ChannelVersions
    ) -> dict[str, Any]:
        """Reconstruye `channel_values` desde los blobs.

        Una sola llamada a `get_all` para todos los canales: un round trip en
        lugar de uno por canal, que es la diferencia entre un checkpoint que
        carga en decenas de milisegundos y uno que carga en cientos.
        """
        if not channel_versions:
            return {}

        refs = [
            self._blobs.document(_clave(thread_id, checkpoint_ns, canal, str(version)))
            for canal, version in channel_versions.items()
        ]
        valores: dict[str, Any] = {}
        async for snapshot in self._client.get_all(refs):
            if not snapshot.exists:
                continue
            datos = snapshot.to_dict() or {}
            tipo = datos.get("type")
            # "empty" marca un canal que existía en esa versión pero sin valor.
            if tipo in (None, "empty"):
                continue
            valores[datos["channel"]] = self.serde.loads_typed((tipo, datos["blob"]))
        return valores

    async def _cargar_escrituras(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        consulta = (
            self._writes.where(filter=FieldFilter("thread_id", "==", thread_id))
            .where(filter=FieldFilter("checkpoint_ns", "==", checkpoint_ns))
            .where(filter=FieldFilter("checkpoint_id", "==", checkpoint_id))
            .order_by("idx")
        )
        resultado: list[tuple[str, str, Any]] = []
        async for snapshot in consulta.stream():
            d = snapshot.to_dict() or {}
            resultado.append(
                (d["task_id"], d["channel"], self.serde.loads_typed((d["type"], d["value"])))
            )
        return resultado

    # ── Escritura ────────────────────────────────────────────────────────────

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]

        copia = dict(checkpoint)
        valores: dict[str, Any] = copia.pop("channel_values", {})  # type: ignore[assignment]

        batch = self._client.batch()

        # Solo los canales que cambiaron en este paso. Es la razón de separar los
        # blobs: reescribir el historial completo de mensajes en cada paso del
        # grafo multiplicaría el costo de escritura sin necesidad.
        for canal, version in new_versions.items():
            if canal in valores:
                tipo, blob = self.serde.dumps_typed(valores[canal])
            else:
                tipo, blob = "empty", b""
            batch.set(
                self._blobs.document(_clave(thread_id, checkpoint_ns, canal, str(version))),
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "channel": canal,
                    "version": str(version),
                    "type": tipo,
                    "blob": blob,
                },
            )

        tipo_ckpt, blob_ckpt = self.serde.dumps_typed(copia)
        metadata_completa = get_checkpoint_metadata(config, metadata)
        tipo_meta, blob_meta = self.serde.dumps_typed(metadata_completa)

        batch.set(
            self._checkpoints.document(_clave(thread_id, checkpoint_ns, checkpoint_id)),
            {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
                "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
                "checkpoint_type": tipo_ckpt,
                "checkpoint": blob_ckpt,
                "metadata_type": tipo_meta,
                "metadata": blob_meta,
                # Desnormalizados para poder filtrar en `alist` sin deserializar.
                "meta_source": metadata_completa.get("source"),
                "meta_step": metadata_completa.get("step"),
                "creado_en": firestore.SERVER_TIMESTAMP,
            },
        )

        await batch.commit()

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Guarda las escrituras pendientes de una tarea.

        Acá es donde aterriza el `interrupt()`: LangGraph escribe el canal
        `__interrupt__` con el payload de la interrupción, y ese registro es el
        que permite que la consola muestre qué acción está esperando aprobación.

        Los canales especiales de `WRITES_IDX_MAP` usan índices negativos fijos
        para que una reescritura de la misma tarea sobreescriba en lugar de
        acumular duplicados.
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        batch = self._client.batch()
        for i, (canal, valor) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(canal, i)
            tipo, blob = self.serde.dumps_typed(valor)
            batch.set(
                self._writes.document(
                    _clave(thread_id, checkpoint_ns, checkpoint_id, task_id, str(idx))
                ),
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "task_id": task_id,
                    "task_path": task_path,
                    "idx": idx,
                    "channel": canal,
                    "type": tipo,
                    "value": blob,
                },
            )
        await batch.commit()

    async def adelete_thread(self, thread_id: str) -> None:
        """Borra todo el estado de un hilo.

        Existe para cumplir pedidos de borrado y para limpiar hilos de prueba. No
        es una operación transaccional: si falla a mitad de camino deja huérfanos,
        que el job de limpieza recoge por `thread_id`.
        """
        for coleccion in (self._checkpoints, self._blobs, self._writes):
            consulta = coleccion.where(filter=FieldFilter("thread_id", "==", thread_id))
            async for snapshot in consulta.stream():
                await snapshot.reference.delete()

    # ── Camino sincrónico: no soportado a propósito ───────────────────────────

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        raise NotImplementedError(_MENSAJE_SYNC)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        raise NotImplementedError(_MENSAJE_SYNC)

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise NotImplementedError(_MENSAJE_SYNC)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        raise NotImplementedError(_MENSAJE_SYNC)

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError(_MENSAJE_SYNC)

    # ── Colecciones ──────────────────────────────────────────────────────────

    @property
    def _checkpoints(self) -> Any:
        return self._client.collection(Collections.CHECKPOINTS)

    @property
    def _blobs(self) -> Any:
        return self._client.collection(BLOBS_COLLECTION)

    @property
    def _writes(self) -> Any:
        return self._client.collection(Collections.CHECKPOINT_WRITES)


_MENSAJE_SYNC = (
    "FirestoreSaver es async. Compilar el grafo y invocarlo por el camino "
    "asincrónico (`ainvoke` / `astream` / `astream_events`). El camino "
    "sincrónico bloquearía el event loop de la API."
)


def _clave(*partes: str) -> str:
    """Id de documento determinístico a partir de una clave compuesta.

    Se hashea porque `thread_id` y `checkpoint_ns` pueden contener barras y otros
    caracteres que Firestore no admite en un id de documento. Los componentes
    quedan además como campos del documento, así que las consultas siguen siendo
    posibles: el hash solo sirve para el acceso directo por clave.
    """
    semilla = "\x1f".join(partes)
    return hashlib.sha256(semilla.encode("utf-8")).hexdigest()
