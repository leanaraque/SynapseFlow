"""Contrato del pipeline de gobernanza.

Lo que se verifica es que las garantías estén **activas por omisión** y que la
redacción sea reversible. Lo segundo es la diferencia entre este middleware y el
`PIIMiddleware` de LangChain: todas sus estrategias destruyen el dato, y una
respuesta que dice ««REDACTED» debe recalibrar el equipo» no le sirve a nadie.

Ver docs/plan/fases/F4-gobernanza.md § F4.5
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from langchain.agents.middleware import (
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from synapseflow.config import Settings
from synapseflow.governance.middleware import (
    RedaccionDePII,
    construir_middleware,
    describir_pipeline,
)
from synapseflow.governance.pii import Tokenizador
from synapseflow.ontology import get_ontology

ONTOLOGIA = get_ontology()


def ajustes(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"SYNAPSEFLOW_PROVIDER": "fake"}
    base.update(overrides)
    return Settings(**base)


@dataclasses.dataclass
class _PedidoFalso:
    """Sustituto de `ModelRequest` con lo que el middleware toca.

    Se usa un doble y no un `ModelRequest` real porque construirlo exige un
    `Runtime` y un `AgentState` completos, y nada de eso participa de lo que este
    middleware hace.
    """

    messages: list[Any]
    system_message: Any = None

    def override(self, **cambios: Any) -> _PedidoFalso:
        return dataclasses.replace(self, **cambios)


def texto_de(respuesta: ModelResponse[Any]) -> str:
    return " ".join(str(m.content) for m in respuesta.result)


# ─────────────────────────────────────────────────────────────────────────────
# La redacción reversible
# ─────────────────────────────────────────────────────────────────────────────


def test_el_legajo_no_llega_al_modelo() -> None:
    """**Es el compromiso 5, ejercitado en el punto donde el texto sale.**"""
    visto: list[str] = []

    def handler(pedido: Any) -> ModelResponse[Any]:
        visto.append(" ".join(str(m.content) for m in pedido.messages))
        return ModelResponse(result=[AIMessage(content="ok")], structured_response=None)

    capa = RedaccionDePII()
    pedido = _PedidoFalso(messages=[HumanMessage(content="¿Qué firmó LEG-00042?")])

    capa.wrap_model_call(pedido, handler)  # type: ignore[arg-type]

    assert "LEG-00042" not in visto[0]
    assert "«INSPECTOR_1»" in visto[0]


def test_la_respuesta_vuelve_rehidratada() -> None:
    """La diferencia con `PIIMiddleware`: sus estrategias destruyen el dato.

    Una respuesta que dice ««REDACTED» debe recalibrar el equipo» no le sirve al
    usuario, que necesita saber a quién avisarle.
    """

    def handler(_: Any) -> ModelResponse[Any]:
        return ModelResponse(
            result=[AIMessage(content="«INSPECTOR_1» debe recalibrar el equipo.")],
            structured_response=None,
        )

    capa = RedaccionDePII()
    capa.wrap_model_call(  # type: ignore[arg-type]
        _PedidoFalso(messages=[HumanMessage(content="LEG-00042 firmó.")]), handler
    )
    respuesta = capa.wrap_model_call(  # type: ignore[arg-type]
        _PedidoFalso(messages=[HumanMessage(content="¿Y ahora?")]), handler
    )

    assert texto_de(respuesta) == "LEG-00042 debe recalibrar el equipo."


async def test_el_camino_async_hace_lo_mismo() -> None:
    """La plataforma invoca por `ainvoke`: si solo el sync redactara, la garantía
    no existiría en producción."""
    visto: list[str] = []

    async def handler(pedido: Any) -> ModelResponse[Any]:
        visto.append(" ".join(str(m.content) for m in pedido.messages))
        return ModelResponse(
            result=[AIMessage(content="Avisar a «INSPECTOR_1».")], structured_response=None
        )

    capa = RedaccionDePII()
    respuesta = await capa.awrap_model_call(  # type: ignore[arg-type]
        _PedidoFalso(messages=[HumanMessage(content="LEG-00042 firmó.")]), handler
    )

    assert "LEG-00042" not in visto[0]
    assert texto_de(respuesta) == "Avisar a LEG-00042."


def test_el_mensaje_de_sistema_tambien_se_tokeniza() -> None:
    """«No debería llevar PII» no es una garantía.

    Si algún día un prompt se arma con datos del hilo, esto lo cubre sin que
    nadie tenga que acordarse.
    """
    visto: list[str] = []

    def handler(pedido: Any) -> ModelResponse[Any]:
        visto.append(str(pedido.system_message.content))
        return ModelResponse(result=[AIMessage(content="ok")], structured_response=None)

    RedaccionDePII().wrap_model_call(  # type: ignore[arg-type]
        _PedidoFalso(
            messages=[HumanMessage(content="hola")],
            system_message=SystemMessage(content="El inspector de guardia es LEG-00042."),
        ),
        handler,
    )

    assert "LEG-00042" not in visto[0]


def test_un_mensaje_sin_pii_no_se_reconstruye() -> None:
    """Copiarlo perdería atributos que una subclase pudiera agregar."""
    original = HumanMessage(content="El espesor medido es 6,8 mm.")
    visto: list[Any] = []

    def handler(pedido: Any) -> ModelResponse[Any]:
        visto.append(pedido.messages[0])
        return ModelResponse(result=[AIMessage(content="ok")], structured_response=None)

    RedaccionDePII().wrap_model_call(_PedidoFalso(messages=[original]), handler)  # type: ignore[arg-type]

    assert visto[0] is original


def test_el_contenido_en_bloques_conserva_lo_que_no_es_texto() -> None:
    """Convertir un bloque de imagen a `str` y de vuelta rompería el mensaje."""
    visto: list[Any] = []

    def handler(pedido: Any) -> ModelResponse[Any]:
        visto.append(pedido.messages[0].content)
        return ModelResponse(result=[AIMessage(content="ok")], structured_response=None)

    mensaje = HumanMessage(
        content=[
            {"type": "text", "text": "Lo firmó LEG-00042."},
            {"type": "image_url", "image_url": {"url": "https://ejemplo/x.png"}},
        ]
    )
    RedaccionDePII().wrap_model_call(_PedidoFalso(messages=[mensaje]), handler)  # type: ignore[arg-type]

    bloques = visto[0]
    assert "LEG-00042" not in bloques[0]["text"]
    assert bloques[1]["image_url"]["url"] == "https://ejemplo/x.png"


def test_el_tokenizador_se_puede_inyectar_para_compartirlo_con_la_auditoria() -> None:
    """El log guarda el mapa de tokenización, así que necesita el mismo objeto."""
    tok = Tokenizador()
    capa = RedaccionDePII(tok)

    capa.wrap_model_call(  # type: ignore[arg-type]
        _PedidoFalso(messages=[HumanMessage(content="LEG-00042")]),
        lambda _: ModelResponse(result=[AIMessage(content="ok")], structured_response=None),
    )

    assert tok.mapa == {"«INSPECTOR_1»": "LEG-00042"}


# ─────────────────────────────────────────────────────────────────────────────
# El ensamblado
# ─────────────────────────────────────────────────────────────────────────────


def test_las_tres_garantias_vienen_activas_de_fabrica() -> None:
    """Desactivar una es una decisión que queda escrita en el entorno."""
    pipeline = construir_middleware(ONTOLOGIA, "inspector", settings=ajustes())
    capas = describir_pipeline(pipeline)

    assert "ModelCallLimitMiddleware" in capas
    assert "RedaccionDePII" in capas
    assert "HumanInTheLoopMiddleware" in capas


def test_el_limite_de_llamadas_va_primero() -> None:
    """Un agente en bucle es el modo de falla más caro que existe.

    Cortarlo temprano ahorra las llamadas que las otras capas igual habrían
    procesado.
    """
    pipeline = construir_middleware(ONTOLOGIA, "inspector", settings=ajustes())
    assert isinstance(pipeline[0], ModelCallLimitMiddleware)


def test_el_gate_va_ultimo() -> None:
    """Opera sobre la invocación ya decidida, después de que el modelo respondió."""
    pipeline = construir_middleware(ONTOLOGIA, "inspector", settings=ajustes())
    assert isinstance(pipeline[-1], HumanInTheLoopMiddleware)


@pytest.mark.parametrize(
    ("bandera", "ausente"),
    [
        ("SYNAPSEFLOW_REDACT_PII", "RedaccionDePII"),
        ("SYNAPSEFLOW_REQUIRE_APPROVAL", "HumanInTheLoopMiddleware"),
    ],
)
def test_cada_garantia_se_puede_desactivar_explicitamente(bandera: str, ausente: str) -> None:
    pipeline = construir_middleware(ONTOLOGIA, "inspector", settings=ajustes(**{bandera: False}))
    assert ausente not in describir_pipeline(pipeline)


def test_sin_techo_de_llamadas_no_se_agrega_el_limite() -> None:
    pipeline = construir_middleware(
        ONTOLOGIA, "inspector", settings=ajustes(SYNAPSEFLOW_MAX_MODEL_CALLS=0)
    )
    assert "ModelCallLimitMiddleware" not in describir_pipeline(pipeline)


def test_un_rol_sin_acciones_con_gate_no_recibe_el_middleware_de_aprobacion() -> None:
    """`consulta` solo lee normativa pública: no tiene nada que aprobar.

    Agregarle un gate vacío sería una capa que no hace nada y que alguien tendría
    que entender antes de descartarla.
    """
    pipeline = construir_middleware(ONTOLOGIA, "consulta", settings=ajustes())
    assert "HumanInTheLoopMiddleware" not in describir_pipeline(pipeline)


def test_los_gates_salen_de_la_ontologia_y_no_del_codigo() -> None:
    """Un desarrollador no puede agregar una acción irreversible y olvidarse del
    gate, porque no es él quien lo escribe."""
    from synapseflow.ontology import interrupt_config

    esperados = set(interrupt_config(ONTOLOGIA, "inspector"))
    assert esperados, "el rol inspector debería tener acciones con gate"

    pipeline = construir_middleware(ONTOLOGIA, "inspector", settings=ajustes())
    gate = next(c for c in pipeline if isinstance(c, HumanInTheLoopMiddleware))

    assert set(gate.interrupt_on) == esperados


def test_el_limite_falla_en_lugar_de_devolver_una_respuesta_parcial() -> None:
    """Un turno cortado por límite no produjo una respuesta.

    Devolver lo que había hasta ahí como si fuera la respuesta final es peor que
    fallar: el usuario no tiene forma de saber que está incompleta.
    """
    pipeline = construir_middleware(ONTOLOGIA, "inspector", settings=ajustes())
    limite = next(c for c in pipeline if isinstance(c, ModelCallLimitMiddleware))

    assert limite.exit_behavior == "error"
