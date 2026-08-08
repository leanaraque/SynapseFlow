"""Precisión de citas. **Determinístico: no hace falta un juez.**

Que un documento y una sección existan, y que estén entre lo que el sistema
recuperó, se comprueba. Poner un LLM a juzgarlo introduce varianza sin aportar
nada: una eval que empeora tendría dos explicaciones posibles —el sistema empeoró
o el juez tuvo un mal día— y entonces deja de servir para decidir.

Mide tres cosas distintas, y las tres importan:

1. **Existencia.** La cita apunta a una sección real del corpus.
2. **Respaldo.** La cita está entre los fragmentos que el sistema recuperó. Una
   cita a una sección real que no estaba en el contexto no se leyó: se recordó.
3. **Cobertura.** Las fuentes que el caso esperaba aparecen en la respuesta.

Ver docs/plan/fases/F8-evals.md § F8.2
"""

from __future__ import annotations

import functools

from evals.evaluadores.base import Caso, RespuestaDelSistema, Resultado
from synapseflow.rag.ingesta import leer_corpus, trocear

METRICA = "precision_de_citas"


@functools.lru_cache(maxsize=1)
def citas_del_corpus() -> frozenset[str]:
    """Todas las citas que el corpus hace posibles.

    Se cachea porque el corredor evalúa decenas de casos y trocear el corpus en
    cada uno cuesta más que todo lo demás junto.
    """
    return frozenset(
        f"{f.metadata['doc_id']} §{f.metadata['seccion']}"
        for documento in leer_corpus()
        for f in trocear(documento)
    )


@functools.lru_cache(maxsize=1)
def citas_derogadas() -> frozenset[str]:
    return frozenset(
        f"{f.metadata['doc_id']} §{f.metadata['seccion']}"
        for documento in leer_corpus()
        if documento.vigencia == "derogado"
        for f in trocear(documento)
    )


def evaluar(caso: Caso, respuesta: RespuestaDelSistema) -> Resultado | None:
    """Dictamina sobre las citas de una respuesta.

    Devuelve `None` cuando el caso no espera citas y el sistema no produjo
    ninguna: no hay nada que medir, y aprobarlo inflaría la métrica con casos
    que nunca se verificaron.
    """
    if not caso.fuentes and not respuesta.citas and not caso.prohibidas:
        return None

    inexistentes = [c for c in respuesta.citas if c not in citas_del_corpus()]
    if inexistentes:
        return Resultado.falla(
            caso.id,
            METRICA,
            f"cita a secciones que no existen en el corpus: {inexistentes}",
        )

    sin_respaldo = [
        c for c in respuesta.citas if respuesta.recuperados and c not in respuesta.recuperados
    ]
    if sin_respaldo:
        return Resultado.falla(
            caso.id,
            METRICA,
            (
                f"cita fuentes que no se recuperaron: {sin_respaldo}. Una sección "
                "real que no estaba en el contexto no se leyó, se recordó."
            ),
        )

    prohibidas_citadas = [c for c in respuesta.citas if c in caso.prohibidas]
    if prohibidas_citadas:
        return Resultado.falla(
            caso.id,
            METRICA,
            (
                f"cita normativa derogada: {prohibidas_citadas}. No es un resultado "
                "de baja calidad: es un error normativo."
            ),
        )

    faltantes = [f for f in caso.fuentes if f not in respuesta.citas]
    if faltantes:
        cubiertas = len(caso.fuentes) - len(faltantes)
        return Resultado.falla(
            caso.id,
            METRICA,
            f"no citó las fuentes esperadas: {faltantes}",
            puntaje=cubiertas / len(caso.fuentes),
        )

    return Resultado.aprueba(
        caso.id, METRICA, f"{len(respuesta.citas)} cita(s), todas existentes y respaldadas"
    )


def limpiar_cache() -> None:
    """Solo para tests: fuerza a releer el corpus."""
    citas_del_corpus.cache_clear()
    citas_derogadas.cache_clear()
