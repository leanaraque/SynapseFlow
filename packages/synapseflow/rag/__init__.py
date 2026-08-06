"""Recuperación con citas obligatorias.

La regla del proyecto: toda afirmación normativa viene con documento y sección, y
un verificador comprueba ese respaldo antes de emitir. Si no lo hay, el sistema
dice que no sabe.

Ver docs/plan/fases/F3-rag.md
"""

from synapseflow.rag.ingesta import (
    CORPUS,
    MAXIMO_POR_FRAGMENTO,
    DocumentoFuente,
    IngestaError,
    indexar,
    ingestar_corpus,
    leer_corpus,
    leer_documento,
    trocear,
)

__all__ = [
    "CORPUS",
    "MAXIMO_POR_FRAGMENTO",
    "DocumentoFuente",
    "IngestaError",
    "indexar",
    "ingestar_corpus",
    "leer_corpus",
    "leer_documento",
    "trocear",
]
