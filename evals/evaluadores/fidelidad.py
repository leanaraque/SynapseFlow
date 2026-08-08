"""Fidelidad: que la respuesta no afirme lo que las fuentes no dicen.

**Es el único evaluador con LLM-as-judge**, y eso es deliberado. Los otros tres
—citas, rechazo y cálculos— se comprueban. Un juez que es otro modelo introduce
su propia varianza, y entonces una eval que empeora tiene dos explicaciones
posibles: el sistema empeoró o el juez tuvo un mal día. Con dos explicaciones, la
métrica deja de servir para decidir.

Acá no hay alternativa: «esta afirmación está respaldada por este fragmento» es
un juicio sobre lenguaje natural. Lo que sí se puede hacer es acotar la varianza:

- Usa el perfil `verifier`, con salida estructurada y temperatura cero.
- Reutiliza el `VerificadorDeFundamento` de F3.4, que es **el mismo componente
  que corre en producción**. Un juez distinto del verificador mediría otra cosa.
- El puntaje es la proporción de afirmaciones respaldadas, no un número que el
  modelo elige: pedirle a un LLM «puntuá del 1 al 10» produce ruido con forma de
  medición.

Ver docs/plan/fases/F8-evals.md § F8.2
"""

from __future__ import annotations

from langchain_core.documents import Document

from evals.evaluadores.base import Caso, RespuestaDelSistema, Resultado
from synapseflow.llm.gateway import Gateway
from synapseflow.rag.fundamento import Resultado as Veredicto
from synapseflow.rag.fundamento import VerificadorDeFundamento

METRICA = "fidelidad"


async def evaluar(
    caso: Caso,
    respuesta: RespuestaDelSistema,
    *,
    gateway: Gateway | None = None,
    verificador: VerificadorDeFundamento | None = None,
) -> Resultado | None:
    """Dictamina si cada afirmación de la respuesta tiene respaldo.

    Devuelve `None` cuando no hay nada que juzgar: una respuesta vacía, o un caso
    donde el sistema se negó correctamente. Juzgar la fidelidad de una negativa
    no mide nada — el rechazo lo mide `rechazo.py`.
    """
    if not respuesta.texto.strip():
        return None

    if caso.debe_rechazar and respuesta.se_nego:
        return None

    juez = verificador or VerificadorDeFundamento(gateway)
    veredicto = await juez.verificar(respuesta.texto, _fragmentos(respuesta))

    if veredicto.resultado is Veredicto.FUNDAMENTADA:
        return Resultado.aprueba(
            caso.id, METRICA, f"{len(veredicto.afirmaciones)} afirmación(es) respaldadas"
        )

    if veredicto.resultado is Veredicto.SIN_FUNDAMENTO:
        return Resultado.falla(caso.id, METRICA, veredicto.explicacion)

    # Parcial: el puntaje es la proporción respaldada, no un número inventado.
    total = len(veredicto.afirmaciones) or 1
    sin_respaldo = len(veredicto.sin_respaldo)
    return Resultado.falla(
        caso.id,
        METRICA,
        f"{sin_respaldo} de {total} afirmaciones sin respaldo: "
        + "; ".join(a.texto[:80] for a in veredicto.sin_respaldo),
        puntaje=(total - sin_respaldo) / total,
    )


def _fragmentos(respuesta: RespuestaDelSistema) -> list[Document]:
    """Los documentos contra los que se juzga, con su texto.

    Son los que el sistema tuvo delante. Volver a buscarlos en el corpus mediría
    otra cosa: si el retriever falló, la fidelidad tiene que **reflejarlo**, no
    compensarlo con material que el redactor nunca vio.
    """
    return [
        Document(
            page_content=f.contenido or f.cita,
            metadata={"doc_id": f.doc_id, "seccion": f.seccion},
        )
        for f in respuesta.fragmentos
    ]
