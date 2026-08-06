"""Contrato del modelo falso.

Este módulo es infraestructura de test, así que sus tests son tests de tests. Se
justifican igual: si el falso miente —repite respuestas en silencio, pierde las
invocaciones de herramienta, devuelve ids distintos en cada corrida— todos los
tests que se apoyen en él pasarían sin probar nada.

Lo que se verifica es lo que las fases siguientes van a dar por sentado:

- que emita invocaciones de herramienta, que es lo que permite ejercitar los
  gates de aprobación sin un proveedor;
- que sea determinístico hasta en los ids de invocación;
- que cuente las llamadas, para poder verificar límites;
- que **falle** al agotarse la cola, porque un bucle silencioso es el modo de
  falla que este proyecto no se puede permitir.

Ver docs/plan/fases/F1-gateway.md § F1.2
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from synapseflow.llm.fake import FakeChatModel, FakeChatModelError, Llamada, Respuesta

# ─────────────────────────────────────────────────────────────────────────────
# Respuestas de texto
# ─────────────────────────────────────────────────────────────────────────────


def test_responde_lo_programado_en_orden() -> None:
    modelo = FakeChatModel(respuestas=[Respuesta(texto="primero"), Respuesta(texto="segundo")])
    assert modelo.invoke("hola").content == "primero"
    assert modelo.invoke("hola").content == "segundo"
    assert modelo.llamadas == 2


def test_responde_por_patron_sobre_el_ultimo_mensaje_humano() -> None:
    modelo = FakeChatModel(
        por_patron={
            r"espesor": Respuesta(texto="hay que comparar contra t_min"),
            r"permiso": Respuesta(texto="depende del área clasificada"),
        },
        por_defecto=Respuesta(texto="no sé"),
    )
    assert "t_min" in str(modelo.invoke("¿qué pasa si el espesor baja?").content)
    assert "área" in str(modelo.invoke("¿qué permiso necesito?").content)
    assert modelo.invoke("¿cuántos activos hay?").content == "no sé"


def test_sin_nada_programado_falla_con_mensaje_util() -> None:
    with pytest.raises(FakeChatModelError, match="no tiene respuesta"):
        FakeChatModel().invoke("hola")


# ─────────────────────────────────────────────────────────────────────────────
# Invocaciones de herramienta: lo que permite testear los gates
# ─────────────────────────────────────────────────────────────────────────────


def test_emite_invocaciones_de_herramienta() -> None:
    modelo = FakeChatModel(
        respuestas=[
            Respuesta(
                texto="Voy a consultar el activo.",
                herramientas=(Llamada(nombre="consultar_activo", argumentos={"tag": "P-2101-A"}),),
            )
        ]
    )
    mensaje = modelo.invoke("¿sigue apto el P-2101-A?")

    assert isinstance(mensaje, AIMessage)
    assert len(mensaje.tool_calls) == 1
    assert mensaje.tool_calls[0]["name"] == "consultar_activo"
    assert mensaje.tool_calls[0]["args"] == {"tag": "P-2101-A"}
    assert mensaje.content == "Voy a consultar el activo."


def test_puede_proponer_una_accion_irreversible() -> None:
    """El escenario que justifica todo el módulo.

    Sin poder programar esta invocación, no habría forma de verificar que el
    grafo se detiene en el gate sin llamar a un proveedor real.
    """
    modelo = FakeChatModel(
        respuestas=[
            Respuesta(
                herramientas=(
                    Llamada(
                        nombre="solicitar_parada_equipo",
                        argumentos={
                            "tag": "P-2101-A",
                            "motivo": "espesor 6,8 mm bajo t_min 7,1 mm",
                            "id_inspeccion": "INS-2026-00007",
                        },
                    ),
                )
            )
        ]
    )
    llamada = modelo.invoke("el P-2101-A midió 6,8 mm").tool_calls[0]
    assert llamada["name"] == "solicitar_parada_equipo"
    assert llamada["args"]["tag"] == "P-2101-A"


def test_emite_varias_invocaciones_en_una_sola_respuesta() -> None:
    modelo = FakeChatModel(
        respuestas=[
            Respuesta(
                herramientas=(
                    Llamada(nombre="consultar_activo", argumentos={"tag": "P-2101-A"}),
                    Llamada(nombre="historial_inspecciones", argumentos={"tag": "P-2101-A"}),
                )
            )
        ]
    )
    nombres = [t["name"] for t in modelo.invoke("x").tool_calls]
    assert nombres == ["consultar_activo", "historial_inspecciones"]


def test_los_ids_de_invocacion_son_determinísticos() -> None:
    """Dos corridas idénticas tienen que producir los mismos ids.

    Con uuids, cualquier aserción sobre el estado del grafo —donde los ids
    aparecen en los ToolMessage— sería inestable entre corridas.
    """

    def ids() -> list[str]:
        modelo = FakeChatModel(
            respuestas=[
                Respuesta(herramientas=(Llamada(nombre="a"),)),
                Respuesta(herramientas=(Llamada(nombre="b"),)),
            ]
        )
        return [modelo.invoke("x").tool_calls[0]["id"] or "" for _ in range(2)]

    assert ids() == ids()
    assert ids() == ["call_1_0", "call_2_0"]


# ─────────────────────────────────────────────────────────────────────────────
# Agotamiento de la cola
# ─────────────────────────────────────────────────────────────────────────────


def test_agotar_la_cola_falla_en_lugar_de_repetir() -> None:
    """Un bucle silencioso es el modo de falla más caro que hay.

    Si el falso repitiera la última respuesta, un agente que no converge pasaría
    el test y el problema aparecería en producción, en la factura.
    """
    modelo = FakeChatModel(respuestas=[Respuesta(texto="única")])
    modelo.invoke("x")
    with pytest.raises(FakeChatModelError, match="2 llamadas"):
        modelo.invoke("x")


def test_ciclico_permite_verificar_un_limite_de_llamadas() -> None:
    """El caso contrario: el bucle es deliberado y se mide.

    Es lo que necesita el test de `ModelCallLimitMiddleware`.
    """
    modelo = FakeChatModel(respuestas=[Respuesta(texto="otra vez")], ciclico=True)
    for _ in range(5):
        modelo.invoke("x")
    assert modelo.llamadas == 5


# ─────────────────────────────────────────────────────────────────────────────
# Observabilidad para los tests de gobernanza
# ─────────────────────────────────────────────────────────────────────────────


def test_registra_lo_que_recibio() -> None:
    """`texto_recibido` es lo que usa el test de la frontera de datos de F4.6.

    Si un legajo aparece ahí, cruzó hacia el proveedor.
    """
    modelo = FakeChatModel(respuestas=[Respuesta(texto="ok")])
    modelo.invoke([HumanMessage(content="inspector LEG-04412")])

    assert "LEG-04412" in modelo.texto_recibido
    assert len(modelo.recibidos) == 1


def test_bind_tools_registra_el_catalogo_recibido() -> None:
    """Verifica el mínimo privilegio: lo que no está acá, el modelo no lo ve."""

    @tool
    def consultar_activo(tag: str) -> str:
        """Ficha de un activo."""
        return tag

    @tool
    def buscar_normativa(consulta: str) -> str:
        """Busca en el corpus."""
        return consulta

    modelo = FakeChatModel(respuestas=[Respuesta(texto="ok")])
    ligado = modelo.bind_tools([consultar_activo, buscar_normativa])

    assert modelo.herramientas_ligadas == ["consultar_activo", "buscar_normativa"]
    assert ligado.invoke("x").content == "ok"


def test_reiniciar_limpia_el_estado_sin_borrar_lo_programado() -> None:
    modelo = FakeChatModel(respuestas=[Respuesta(texto="a")], ciclico=True)
    modelo.invoke("x")
    modelo.reiniciar()
    assert modelo.llamadas == 0
    assert modelo.recibidos == []
    assert modelo.invoke("x").content == "a"


# ─────────────────────────────────────────────────────────────────────────────
# Contabilidad de tokens
# ─────────────────────────────────────────────────────────────────────────────


def test_reporta_uso_de_tokens() -> None:
    """F1.4 necesita un `usage_metadata` para poder calcular costo."""
    modelo = FakeChatModel(respuestas=[Respuesta(texto="una respuesta cualquiera")])
    uso = modelo.invoke("una pregunta cualquiera").usage_metadata

    assert uso is not None
    assert uso["input_tokens"] > 0
    assert uso["output_tokens"] > 0
    assert uso["total_tokens"] == uso["input_tokens"] + uso["output_tokens"]


def test_los_tokens_se_pueden_fijar_para_un_costo_esperado() -> None:
    """Sin esto, el test de costo dependería del largo del texto del test."""
    modelo = FakeChatModel(
        respuestas=[Respuesta(texto="x", tokens_entrada=1000, tokens_salida=250)]
    )
    uso = modelo.invoke("y").usage_metadata
    assert uso is not None
    assert (uso["input_tokens"], uso["output_tokens"]) == (1000, 250)


# ─────────────────────────────────────────────────────────────────────────────
# Salida estructurada
# ─────────────────────────────────────────────────────────────────────────────


class Veredicto(BaseModel):
    fundamentada: bool
    motivo: str


def test_salida_estructurada_devuelve_el_objeto_programado() -> None:
    """La necesita el verificador de fundamento de F3.4."""
    modelo = FakeChatModel(
        estructurados=[Veredicto(fundamentada=False, motivo="sin cita en el corpus")]
    )
    resultado = modelo.with_structured_output(Veredicto).invoke("¿está fundamentada?")

    assert isinstance(resultado, Veredicto)
    assert resultado.fundamentada is False


def test_salida_estructurada_valida_un_dict_contra_el_schema() -> None:
    modelo = FakeChatModel(estructurados=[{"fundamentada": True, "motivo": "API 570 §7.4"}])
    resultado = modelo.with_structured_output(Veredicto).invoke("x")
    assert isinstance(resultado, Veredicto)
    assert resultado.motivo == "API 570 §7.4"


def test_salida_estructurada_falla_si_se_pide_de_mas() -> None:
    modelo = FakeChatModel(estructurados=[Veredicto(fundamentada=True, motivo="ok")])
    cadena = modelo.with_structured_output(Veredicto)
    cadena.invoke("x")
    with pytest.raises(FakeChatModelError, match="más veces"):
        cadena.invoke("x")


# ─────────────────────────────────────────────────────────────────────────────
# Camino asincrónico
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_camino_async_da_lo_mismo_que_el_sincronico() -> None:
    """Toda la plataforma es async: es el camino que se va a usar de verdad."""
    modelo = FakeChatModel(
        respuestas=[
            Respuesta(texto="hola", herramientas=(Llamada(nombre="t", argumentos={"a": 1}),))
        ]
    )
    mensaje = await modelo.ainvoke("x")
    assert mensaje.content == "hola"
    assert mensaje.tool_calls[0]["name"] == "t"
    assert modelo.llamadas == 1
