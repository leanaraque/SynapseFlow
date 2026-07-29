"""Persistencia sobre Firestore.

Las tres integraciones son implementación propia contra las interfaces
abstractas de LangChain y LangGraph, no el paquete oficial
`langchain-google-firestore`, que todavía pinnea `langchain-core <1.0.0`.

Ver docs/adr/0002-integraciones-firestore-propias.md
"""

from synapseflow.persistence.checkpointer import FirestoreSaver
from synapseflow.persistence.client import Collections, get_client, reset_client_cache
from synapseflow.persistence.vectorstore import FirestoreVectorStore

__all__ = [
    "Collections",
    "FirestoreSaver",
    "FirestoreVectorStore",
    "get_client",
    "reset_client_cache",
]
