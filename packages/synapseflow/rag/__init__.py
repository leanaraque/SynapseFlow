"""Recuperación con citas obligatorias.

La regla del proyecto: toda afirmación normativa viene con documento y sección, y
un verificador comprueba ese respaldo antes de emitir. Si no lo hay, el sistema
dice que no sabe.

Ver docs/plan/fases/F3-rag.md
"""

from synapseflow.rag.citas import (
    Cita,
    ResultadoValidacion,
    extraer_citas,
    validar_citas,
    validar_texto,
)
from synapseflow.rag.fundamento import (
    SIN_FUNDAMENTO,
    Afirmacion,
    Dictamen,
    Resultado,
    Veredicto,
    VerificadorDeFundamento,
)
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
from synapseflow.rag.retrievers import (
    BM25Retriever,
    construir_retriever,
    construir_retriever_vigente,
)

__all__ = [
    "CORPUS",
    "MAXIMO_POR_FRAGMENTO",
    "SIN_FUNDAMENTO",
    "Afirmacion",
    "BM25Retriever",
    "Cita",
    "Dictamen",
    "DocumentoFuente",
    "IngestaError",
    "Resultado",
    "ResultadoValidacion",
    "Veredicto",
    "VerificadorDeFundamento",
    "construir_retriever",
    "construir_retriever_vigente",
    "extraer_citas",
    "indexar",
    "ingestar_corpus",
    "leer_corpus",
    "leer_documento",
    "trocear",
    "validar_citas",
    "validar_texto",
]
