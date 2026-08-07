"""Contrato del nodo verificador.

Lo que se verifica acá no es el dictamen —eso es `tests/rag/test_fundamento.py`—
sino **la decisión de flujo** que el nodo toma con él: emitir, marcar, o volver a
buscar.

El ciclo de vuelta al agente de normativa es una de las dos razones por las que
el proyecto usa un motor de grafos. Su techo importa igual que su existencia: sin
él, una pregunta cuya respuesta no está en el corpus haría girar el grafo
indefinidamente.

Ver docs/plan/fases/F5-grafo.md § F5.3
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

from synapseflow.agents.state import MAX_REINTENTOS_DE_FUNDAMENTO, AgentState, estado_inicial
from synapseflow.agents.verificador import (
    NODO_EMITIR,
    NODO_NORMATIVA,
    documentos_recuperados,
    nodo_verificador,
)
from synapseflow.config import Provider, Settings
from synapseflow.llm.fake import FakeChatModel
from synapseflow.llm.gateway import Gateway
from synapseflow.rag.fundamento import (
    SIN_FUNDAMENTO,
    Afirmacion,
    Dictamen,
    Resultado,
    VerificadorDeFundamento,
)

RECUPERADOS = [
    Document(
        page_content="El componente bajo t_min se retira de servicio.",
        metadata={"doc_id": "API-570-2016", "seccion": "7.4", "vigencia": "vigente"},
    )
]

RESPUESTA = "El activo no está apto [API-570-2016 §7.4]."


def verificador(*pares: tuple[str, bool]) -> VerificadorDeFundamento:
    """Verificador con el dictamen del modelo ya programado."""
    falso = FakeChatModel(
        estructurados=[
            Dictamen(afirmaciones=[Afirmacion(texto=t, respaldada=ok) for t, ok in pares])
        ]
    )
    return VerificadorDeFundamento(
        Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)
    )


def estado_con(respuesta: str, **extra: Any) -> AgentState:
    base: dict[str, Any] = {
        "messages": [HumanMessage(content="¿P-2101-A sigue apto?"), AIMessage(content=respuesta)],
        "recuperados": RECUPERADOS,
        "reintentos": 0,
    }
    base.update(extra)
    return base  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# Las tres decisiones de flujo
# ─────────────────────────────────────────────────────────────────────────────


async def test_una_respuesta_fundamentada_va_a_emitir() -> None:
    comando = await nodo_verificador(
        estado_con(RESPUESTA), verificador=verificador(("El activo no está apto", True))
    )

    assert comando.goto == NODO_EMITIR
    assert comando.update["veredicto"] == Resultado.FUNDAMENTADA.value


async def test_una_respuesta_parcial_tambien_se_emite() -> None:
    """`parcial` es lo que hace utilizable al sistema: se emite marcando."""
    comando = await nodo_verificador(
        estado_con(RESPUESTA),
        verificador=verificador(("El activo no está apto", True), ("Hay que pararlo ya", False)),
    )

    assert comando.goto == NODO_EMITIR
    assert comando.update["veredicto"] == Resultado.PARCIAL.value
    assert "NO está respaldado" in comando.update["messages"][0]["content"]


async def test_sin_fundamento_vuelve_al_agente_de_normativa() -> None:
    """**El ciclo.**

    Es una de las dos razones por las que el proyecto usa un motor de grafos. Con
    una cadena, «volver a buscar» sería un bucle en Python alrededor de la cadena
    entera, y el estado intermedio quedaría fuera del checkpoint.
    """
    comando = await nodo_verificador(
        estado_con(RESPUESTA), verificador=verificador(("Hay que pararlo ya", False))
    )

    assert comando.goto == NODO_NORMATIVA
    assert comando.update["reintentos"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# El techo del ciclo
# ─────────────────────────────────────────────────────────────────────────────


async def test_al_agotar_los_reintentos_deja_de_ciclar() -> None:
    """Dos vueltas sin fundamento significan que no está en el corpus.

    Sin techo, una pregunta sin respuesta en el corpus haría girar el grafo
    indefinidamente.
    """
    comando = await nodo_verificador(
        estado_con(RESPUESTA, reintentos=MAX_REINTENTOS_DE_FUNDAMENTO),
        verificador=verificador(("Hay que pararlo ya", False)),
    )

    assert comando.goto == NODO_EMITIR
    assert comando.update["veredicto"] == Resultado.SIN_FUNDAMENTO.value


async def test_al_agotar_los_reintentos_emite_la_negativa_y_no_la_respuesta() -> None:
    """Rendirse no es emitir lo que no se pudo fundamentar."""
    comando = await nodo_verificador(
        estado_con(RESPUESTA, reintentos=MAX_REINTENTOS_DE_FUNDAMENTO),
        verificador=verificador(("Hay que pararlo ya", False)),
    )

    emitido = comando.update["messages"][0]["content"]
    assert emitido == SIN_FUNDAMENTO
    assert RESPUESTA not in emitido


async def test_el_contador_avanza_de_a_uno() -> None:
    """Si avanzara de a dos, el techo se alcanzaría en una vuelta."""
    comando = await nodo_verificador(
        estado_con(RESPUESTA, reintentos=1), verificador=verificador(("x", False))
    )
    assert comando.update["reintentos"] == 2


async def test_la_vuelta_lleva_el_motivo_del_rechazo() -> None:
    """Sin él, la segunda búsqueda repetiría la primera.

    El agente de normativa no tendría cómo saber qué le faltó.
    """
    comando = await nodo_verificador(
        estado_con(RESPUESTA),
        verificador=verificador(("Corresponde parada inmediata", False)),
    )

    instruccion = comando.update["messages"][0]["content"]
    assert "Corresponde parada inmediata" in instruccion
    assert "Buscá con otros términos" in instruccion


# ─────────────────────────────────────────────────────────────────────────────
# Casos de borde del estado
# ─────────────────────────────────────────────────────────────────────────────


async def test_un_turno_sin_respuesta_redactada_pasa_sin_verificar() -> None:
    """Un turno que solo invocó herramientas es legítimo y no afirma nada.

    Fallar acá rompería el bucle normal del agente antes de que redacte.
    """
    estado: AgentState = {
        "messages": [HumanMessage(content="Ficha de P-1")],
        "recuperados": [],
    }  # type: ignore[typeddict-item]

    comando = await nodo_verificador(estado, verificador=verificador())

    assert comando.goto == NODO_EMITIR
    assert comando.update["veredicto"] is None


async def test_un_mensaje_de_solo_invocacion_no_cuenta_como_respuesta() -> None:
    """Un `AIMessage` con `tool_calls` y sin texto no afirma nada."""
    estado: AgentState = {
        "messages": [
            HumanMessage(content="Ficha de P-1"),
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "consultar_activo", "args": {}, "id": "1", "type": "tool_call"}
                ],
            ),
        ],
        "recuperados": [],
    }  # type: ignore[typeddict-item]

    comando = await nodo_verificador(estado, verificador=verificador())

    assert comando.update["veredicto"] is None


async def test_sin_material_recuperado_no_se_emite_una_afirmacion() -> None:
    """El caso del agente que responde sin haber buscado."""
    comando = await nodo_verificador(
        estado_con(RESPUESTA, recuperados=[], reintentos=MAX_REINTENTOS_DE_FUNDAMENTO),
        verificador=verificador(),
    )

    assert comando.update["veredicto"] == Resultado.SIN_FUNDAMENTO.value
    assert comando.update["messages"][0]["content"] == SIN_FUNDAMENTO


# ─────────────────────────────────────────────────────────────────────────────
# Se verifica contra lo que el redactor tuvo delante
# ─────────────────────────────────────────────────────────────────────────────


def test_los_documentos_salen_del_estado_y_no_de_una_busqueda_nueva() -> None:
    """Si el verificador buscara de nuevo, contrastaría contra otro conjunto.

    La garantía del compromiso 4 dejaría de valer: el redactor habría visto unos
    fragmentos y el verificador otros.
    """
    assert documentos_recuperados(estado_con(RESPUESTA)) == RECUPERADOS


def test_un_estado_sin_recuperados_devuelve_lista_vacia() -> None:
    assert documentos_recuperados(estado_inicial("x")) == []


async def test_las_citas_respaldadas_quedan_en_el_estado() -> None:
    """La consola las muestra y el log de auditoría las guarda.

    Recalcularlas después obligaría a repetir la validación con el riesgo de que
    dé distinto.
    """
    comando = await nodo_verificador(
        estado_con(RESPUESTA), verificador=verificador(("El activo no está apto", True))
    )

    assert comando.update["citas"] == [{"doc_id": "API-570-2016", "seccion": "7.4"}]


@pytest.mark.parametrize(
    ("dictamenes", "destino"),
    [
        ((("a", True),), NODO_EMITIR),
        ((("a", True), ("b", False)), NODO_EMITIR),
        ((("a", False),), NODO_NORMATIVA),
    ],
)
async def test_la_tabla_de_destinos(dictamenes: tuple[tuple[str, bool], ...], destino: str) -> None:
    """Fundamentada y parcial siguen; sin fundamento vuelve a buscar."""
    comando = await nodo_verificador(estado_con(RESPUESTA), verificador=verificador(*dictamenes))
    assert comando.goto == destino
