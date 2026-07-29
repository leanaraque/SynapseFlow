"""Cliente de Firestore compartido por toda la plataforma.

Un solo cliente por proceso. El cliente de Firestore mantiene un pool de
conexiones gRPC: crear uno por request en Cloud Run degrada la latencia del
arranque en frío y agota descriptores bajo carga.
"""

from __future__ import annotations

from functools import lru_cache

from google.cloud import firestore

from synapseflow.config import get_settings


@lru_cache(maxsize=1)
def get_client() -> firestore.AsyncClient:
    """Cliente async de Firestore.

    En Cloud Run la autenticación sale de la identidad del servicio (ADC), sin
    archivo de clave. En local, del emulador o de GOOGLE_APPLICATION_CREDENTIALS.
    """
    settings = get_settings()
    return firestore.AsyncClient(
        project=settings.gcp_project,
        database=settings.firestore_database,
    )


def reset_client_cache() -> None:
    """Solo para tests: descarta el cliente cacheado."""
    get_client.cache_clear()


# ─────────────────────────────────────────────────────────────────────────────
# Nombres de colecciones
#
# Centralizados para que las reglas de seguridad de Firestore, los índices y el
# código no se desincronicen. Cambiar un nombre acá y no en firestore.rules
# abriría o cerraría acceso sin que nadie lo note.
# ─────────────────────────────────────────────────────────────────────────────


class Collections:
    # Dominio
    INSTALLATIONS = "installations"
    ASSETS = "assets"
    INSPECTIONS = "inspections"
    WORK_ORDERS = "work_orders"

    # RAG
    CORPUS_CHUNKS = "corpus_chunks"

    # Plataforma
    CHECKPOINTS = "lg_checkpoints"
    CHECKPOINT_WRITES = "lg_checkpoint_writes"
    MEMORY_STORE = "lg_store"
    AUDIT_LOG = "audit_log"
    APPROVALS = "approvals"
    USAGE = "llm_usage"
    PROMPTS = "prompt_registry"
    EVAL_RUNS = "eval_runs"
