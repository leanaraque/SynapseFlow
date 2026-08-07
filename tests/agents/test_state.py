"""Contrato del estado del agente.

El test que define el módulo es el de serialización. El estado se persiste en
cada checkpoint y se reconstruye después de que el proceso murió — que es toda
la promesa del human-in-the-loop asincrónico. Un objeto vivo adentro funciona en
memoria y falla al reanudar, horas después, cuando el supervisor aprieta Aprobar.

Ver docs/plan/fases/F5-grafo.md § F5.1
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from synapseflow.agents.state import (
    MAX_REINTENTOS_DE_FUNDAMENTO,
    AgentState,
    campos_no_serializables,
    estado_inicial,
    puede_reintentar,
)


def estado_poblado() -> AgentState:
    return {
        "messages": [HumanMessage(content="¿P-2101-A sigue apto?"), AIMessage(content="No.")],
        "recuperados": [
            Document(page_content="texto", metadata={"doc_id": "API-570-2016", "seccion": "7.4"})
        ],
        "citas": [{"doc_id": "API-570-2016", "seccion": "7.4"}],
        "calculos": {"vida_remanente_anios": -1.43, "velocidad_mm_anio": 0.21},
        "veredicto": "fundamentada",
        "reintentos": 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
# La restricción que impone el checkpointer
# ─────────────────────────────────────────────────────────────────────────────


def test_el_estado_completo_es_serializable() -> None:
    """**Es la restricción del checkpointer, no una guía de estilo.**

    Se usa el mismo `serde` que `FirestoreSaver`: si esto pasa, el estado
    sobrevive a la muerte del proceso.
    """
    assert campos_no_serializables(estado_poblado()) == []


def test_el_estado_serializado_se_puede_reconstruir() -> None:
    """Serializar sin poder volver no sirve de nada."""
    serde = JsonPlusSerializer()
    original = estado_poblado()

    recuperado = serde.loads_typed(serde.dumps_typed(original))

    assert recuperado["calculos"]["vida_remanente_anios"] == -1.43
    assert recuperado["veredicto"] == "fundamentada"
    assert len(recuperado["messages"]) == 2


def test_un_objeto_vivo_en_el_estado_se_detecta() -> None:
    """El control negativo.

    Sin esto, `campos_no_serializables` podría devolver siempre vacío y el test
    de arriba pasaría sin verificar nada.
    """

    class Cliente:
        """Un cliente de base de datos, que es lo que nunca debe entrar."""

        def __init__(self) -> None:
            self.socket = lambda: None  # una lambda no se serializa

    sucio: dict[str, Any] = dict(estado_poblado())
    sucio["cliente"] = Cliente()

    assert "cliente" in campos_no_serializables(sucio)  # type: ignore[arg-type]


def test_el_estado_inicial_tambien_es_serializable() -> None:
    assert campos_no_serializables(estado_inicial("¿P-2101-A sigue apto?")) == []


# ─────────────────────────────────────────────────────────────────────────────
# El estado inicial
# ─────────────────────────────────────────────────────────────────────────────


def test_el_estado_inicial_trae_la_pregunta() -> None:
    estado = estado_inicial("¿P-2101-A sigue apto?")
    assert estado["messages"][0]["content"] == "¿P-2101-A sigue apto?"  # type: ignore[index]


def test_el_estado_inicial_no_deja_campos_sin_definir() -> None:
    """Un campo que falte se manifiesta como `KeyError` en el nodo que lo lee,
    lejos de donde se armó el diccionario."""
    estado = estado_inicial("x")

    for campo in ("messages", "recuperados", "citas", "calculos", "veredicto", "reintentos"):
        assert campo in estado, f"el estado inicial no define '{campo}'"


def test_el_veredicto_arranca_en_none_y_no_en_una_cadena() -> None:
    """`None` significa «todavía no se verificó».

    Arrancar en `"sin_fundamento"` haría que un grafo que nunca llamó al
    verificador pareciera haberlo hecho y haber fallado.
    """
    assert estado_inicial("x")["veredicto"] is None


# ─────────────────────────────────────────────────────────────────────────────
# El techo del ciclo
# ─────────────────────────────────────────────────────────────────────────────


def test_un_estado_nuevo_puede_reintentar() -> None:
    assert puede_reintentar(estado_inicial("x")) is True


def test_al_llegar_al_techo_no_se_reintenta() -> None:
    """Dos vueltas sin fundamento significan que no está en el corpus.

    Una tercera gasta cuota para llegar a la misma conclusión.
    """
    estado = estado_inicial("x")
    estado["reintentos"] = MAX_REINTENTOS_DE_FUNDAMENTO

    assert puede_reintentar(estado) is False


def test_el_contador_de_reintentos_vive_en_el_estado() -> None:
    """El ciclo puede atravesar un checkpoint.

    Con el contador en una variable del nodo, reanudar reiniciaría la cuenta y el
    grafo podría no terminar nunca.
    """
    assert "reintentos" in AgentState.__annotations__


def test_sin_el_campo_reintentos_se_asume_cero() -> None:
    """Un estado que viene de una versión anterior no puede romper el grafo."""
    assert puede_reintentar({"messages": []}) is True  # type: ignore[typeddict-item]


# ─────────────────────────────────────────────────────────────────────────────
# La forma del estado
# ─────────────────────────────────────────────────────────────────────────────


def test_los_mensajes_se_acumulan_con_add_messages() -> None:
    """Cada nodo devuelve solo lo que agrega.

    Devolver la lista entera desde cada nodo funcionaría y duplicaría el
    historial en cada paso.
    """
    from typing import get_type_hints

    anotacion = get_type_hints(AgentState, include_extras=True)["messages"]
    assert "add_messages" in str(anotacion)


@pytest.mark.parametrize("campo", ["recuperados", "citas", "calculos", "veredicto", "reintentos"])
def test_todo_lo_que_no_son_mensajes_es_opcional(campo: str) -> None:
    """El estado inicial de una conversación solo trae `messages`.

    Exigir el resto obligaría a cada call site a inventar valores vacíos para
    campos que el grafo va a llenar.
    """
    assert campo in AgentState.__optional_keys__


def test_los_recuperados_son_los_que_vio_el_redactor() -> None:
    """Viven en el estado y no se vuelven a pedir al retriever.

    Si el verificador los pidiera de nuevo, estaría contrastando contra otro
    conjunto y la garantía del compromiso 4 dejaría de valer.
    """
    estado = estado_poblado()
    assert estado["recuperados"][0].metadata["doc_id"] == "API-570-2016"


def test_los_calculos_llegan_como_numeros_y_no_como_texto() -> None:
    """El compromiso 3: el número no pasa por la interpretación del modelo."""
    estado = estado_poblado()
    assert isinstance(estado["calculos"]["vida_remanente_anios"], float)
