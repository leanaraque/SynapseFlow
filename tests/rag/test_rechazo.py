"""Negarse a responder es una métrica de éxito, no un fallo.

Un asistente que siempre contesta algo es más peligroso que uno que a veces dice
que no sabe, y en este dominio la diferencia se mide en paradas de planta. Este
archivo verifica el recorrido completo de la negativa, con el corpus real
indexado y las dos ramas de recuperación funcionando.

Los tests de acá son distintos de los de `test_fundamento.py`: allá se programa
el dictamen del modelo y se verifica qué hace el sistema con él; acá se ejercita
la cadena entera —recuperación, citas, verificación— contra Firestore.

Ver docs/plan/fases/F3-rag.md § F3.5
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from synapseflow.config import Provider, Settings
from synapseflow.llm.fake import FakeChatModel
from synapseflow.llm.gateway import Gateway
from synapseflow.persistence.client import get_client
from synapseflow.persistence.vectorstore import FirestoreVectorStore
from synapseflow.rag.citas import validar_texto
from synapseflow.rag.fundamento import (
    SIN_FUNDAMENTO,
    Afirmacion,
    Dictamen,
    Resultado,
    VerificadorDeFundamento,
)
from synapseflow.rag.ingesta import ingestar_corpus
from synapseflow.rag.retrievers import construir_retriever_vigente

pytestmark = pytest.mark.emulator


@pytest.fixture
def gateway() -> Gateway:
    return Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE))


@pytest.fixture
async def almacen(requiere_emulador: None, gateway: Gateway) -> AsyncIterator[FirestoreVectorStore]:
    """El corpus real, indexado con embeddings determinísticos."""
    coleccion = f"corpus_rechazo_{uuid.uuid4().hex[:8]}"
    tienda = FirestoreVectorStore(gateway.embeddings(), collection=coleccion)

    await ingestar_corpus(tienda)
    yield tienda

    cliente = get_client()
    async for documento in cliente.collection(coleccion).stream():
        await documento.reference.delete()


def verificador_que_dictamina(*pares: tuple[str, bool]) -> VerificadorDeFundamento:
    falso = FakeChatModel(
        estructurados=[
            Dictamen(afirmaciones=[Afirmacion(texto=t, respaldada=ok) for t, ok in pares])
        ]
    )
    return VerificadorDeFundamento(
        Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)
    )


# ─────────────────────────────────────────────────────────────────────────────
# La negativa
# ─────────────────────────────────────────────────────────────────────────────


async def test_se_niega_cuando_no_hay_fundamento(almacen: FirestoreVectorStore) -> None:
    """**Negarse a responder es una métrica de éxito.**

    Se pregunta por un dominio ajeno al corpus. El sistema tiene que declarar que
    no encontró base en lugar de improvisar con el conocimiento general del
    modelo, que es exactamente lo que un asistente sin esta capa haría: contestar
    algo plausible sobre biología marina con el tono de un código de inspección.
    """
    retriever = construir_retriever_vigente(almacen)
    recuperados = await retriever.ainvoke(
        "cuál es el ciclo reproductivo del plancton bioluminiscente en aguas frías"
    )

    respuesta = "El ciclo reproductivo se completa en catorce días."
    veredicto = await verificador_que_dictamina((respuesta, False)).verificar(
        respuesta, recuperados
    )

    assert veredicto.resultado is Resultado.SIN_FUNDAMENTO
    assert veredicto.se_emite is False
    assert veredicto.texto_emitible == SIN_FUNDAMENTO
    assert respuesta not in veredicto.texto_emitible


async def test_no_cita_normativa_derogada(almacen: FirestoreVectorStore) -> None:
    """El corpus incluye `DEROGADO-PROC-INT-009.md` justamente para esto.

    Ese procedimiento contradice al vigente en el criterio de aceptación, así que
    fundamentar una respuesta en él no es un resultado de baja calidad: es un
    error normativo que un auditor detectaría.
    """
    retriever = construir_retriever_vigente(almacen)

    for consulta in (
        "criterio de aceptación de espesores medidos",
        "procedimiento interno de evaluación de integridad",
        "qué hacer cuando el espesor cae por debajo del mínimo",
    ):
        recuperados = await retriever.ainvoke(consulta)
        derogados = [d for d in recuperados if d.metadata.get("vigencia") == "derogado"]

        assert not derogados, (
            f"la consulta «{consulta}» recuperó normativa derogada: "
            f"{[d.metadata.get('doc_id') for d in derogados]}"
        )


async def test_una_cita_al_documento_derogado_no_queda_respaldada(
    almacen: FirestoreVectorStore,
) -> None:
    """La otra mitad: aunque el modelo lo cite de memoria, no está respaldado.

    El filtro impide que el derogado llegue al contexto; esto impide que se cite
    igual. Son dos defensas del mismo error y las dos hacen falta: la primera
    protege del retriever, la segunda del modelo.
    """
    retriever = construir_retriever_vigente(almacen)
    recuperados = await retriever.ainvoke("criterio de aceptación de espesores")

    resultado = validar_texto("Según PROC-INT-009 §2.1 el criterio es otro.", recuperados)

    assert resultado.inventadas, "una cita al derogado tendría que quedar sin respaldo"
    assert resultado.todas_respaldadas is False


# ─────────────────────────────────────────────────────────────────────────────
# El otro lado: cuando sí hay fundamento, responde
# ─────────────────────────────────────────────────────────────────────────────


async def test_con_fundamento_real_la_respuesta_se_emite(
    almacen: FirestoreVectorStore,
) -> None:
    """El control positivo.

    Sin esto, un sistema que se niega a todo pasaría los dos tests de arriba. La
    negativa vale como métrica de éxito solo si el sistema también sabe
    responder.
    """
    retriever = construir_retriever_vigente(almacen)
    recuperados = await retriever.ainvoke("espesor mínimo requerido y retiro de servicio")

    assert recuperados, "el corpus tendría que responder a esta consulta"

    cita = f"{recuperados[0].metadata['doc_id']} §{recuperados[0].metadata['seccion']}"
    respuesta = f"El componente debe retirarse de servicio [{cita}]."

    veredicto = await verificador_que_dictamina((respuesta, True)).verificar(respuesta, recuperados)

    assert veredicto.resultado is Resultado.FUNDAMENTADA
    assert veredicto.se_emite is True
    assert veredicto.texto_emitible == respuesta


async def test_la_accion_del_dominio_usa_la_recuperacion_hibrida(
    almacen: FirestoreVectorStore, monkeypatch: pytest.MonkeyPatch, gateway: Gateway
) -> None:
    """`buscar_normativa` dejó de ser la versión provisoria de F2.2.

    Se verifica sobre la acción que el agente invoca de verdad, no sobre el
    retriever aislado: es donde el cambio tiene efecto.
    """
    from synapseflow.domain import lecturas

    monkeypatch.setattr(lecturas, "_GATEWAY", gateway)
    monkeypatch.setattr(lecturas, "FirestoreVectorStore", lambda *_a, **_k: almacen)

    resultado = await lecturas.buscar_normativa("espesor mínimo requerido")

    assert resultado.artifact["fragmentos"]
    vigencias = {f.get("vigencia", "vigente") for f in resultado.artifact["fragmentos"]}
    assert vigencias <= {"vigente"}
    for fragmento in resultado.artifact["fragmentos"]:
        assert fragmento["doc_id"] and fragmento["seccion"]
