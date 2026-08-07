"""Contrato del supervisor.

Dos clases de propiedad, y la segunda es la que importa:

1. Que rutee según lo que falta. Se ejercita con el modelo falso programado, así
   que lo que se verifica es el cableado, no la calidad del juicio del modelo
   —eso se mide en las evals de F8—.
2. **Que las invariantes del grafo se apliquen en Python, después de que el
   modelo eligió.** Un modelo puede elegir un especialista ya consultado o
   inventar un nombre; las dos cosas se corrigen acá, porque un prompt más severo
   baja la frecuencia del error y no lo elimina.

Ver docs/plan/fases/F5-grafo.md § F5.4
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage

from synapseflow.agents.state import AgentState, estado_inicial
from synapseflow.agents.supervisor import (
    INSTRUCCION,
    NODO_VERIFICADOR,
    Ruteo,
    destinos_posibles,
    nodo_supervisor,
)
from synapseflow.config import Provider, Settings
from synapseflow.llm.fake import FakeChatModel
from synapseflow.llm.gateway import Gateway


def gateway_que_elige(*destinos: str) -> Gateway:
    """Gateway cuyo perfil `router` devuelve estos ruteos, en orden."""
    falso = FakeChatModel(estructurados=[Ruteo(destino=d) for d in destinos])  # type: ignore[arg-type]
    return Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)


def estado(
    consultados: list[str] | None = None, pregunta: str = "¿P-2101-A sigue apto?"
) -> AgentState:
    base: dict[str, Any] = {
        "messages": [HumanMessage(content=pregunta)],
        "especialistas_consultados": consultados or [],
    }
    return base  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# El ruteo
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("destino", ["datos", "calculo", "normativa"])
async def test_rutea_al_especialista_que_el_modelo_eligio(destino: str) -> None:
    comando = await nodo_supervisor(estado(), gateway=gateway_que_elige(destino))
    assert comando.goto == destino


async def test_el_especialista_elegido_queda_registrado() -> None:
    """Es lo que impide consultarlo dos veces en el mismo turno."""
    comando = await nodo_supervisor(estado(), gateway=gateway_que_elige("datos"))
    assert comando.update["especialistas_consultados"] == ["datos"]


async def test_el_registro_conserva_el_orden_de_consulta() -> None:
    """El orden es parte de la trazabilidad: el log reconstruye el recorrido."""
    comando = await nodo_supervisor(
        estado(consultados=["datos"]), gateway=gateway_que_elige("calculo")
    )
    assert comando.update["especialistas_consultados"] == ["datos", "calculo"]


async def test_el_modelo_puede_mandar_directo_al_verificador() -> None:
    """Una consulta que no necesita a nadie más no debería pagar tres llamadas."""
    comando = await nodo_supervisor(estado(), gateway=gateway_que_elige(NODO_VERIFICADOR))

    assert comando.goto == NODO_VERIFICADOR
    assert comando.update is None or "especialistas_consultados" not in (comando.update or {})


# ─────────────────────────────────────────────────────────────────────────────
# Las invariantes se aplican en Python
# ─────────────────────────────────────────────────────────────────────────────


async def test_con_los_tres_consultados_va_al_verificador_sin_preguntar() -> None:
    """**No se llama al modelo cuando no queda nada que elegir.**

    Insistir no aporta, y el modelo —si se lo deja elegir— tiende a repetir el
    último que le resultó útil.
    """
    falso = FakeChatModel(estructurados=[])
    puerta = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)

    comando = await nodo_supervisor(
        estado(consultados=["datos", "calculo", "normativa"]), gateway=puerta
    )

    assert comando.goto == NODO_VERIFICADOR
    assert falso.llamadas == 0, "se pagó una llamada para una decisión que ya estaba tomada"


async def test_un_especialista_ya_consultado_no_se_repite() -> None:
    """El modelo puede elegirlo igual. La invariante se aplica después.

    Un prompt más severo baja la frecuencia del error y no lo elimina.
    """
    comando = await nodo_supervisor(
        estado(consultados=["datos"]), gateway=gateway_que_elige("datos")
    )

    assert comando.goto != "datos"
    assert comando.goto in ("calculo", "normativa")


async def test_una_salida_que_no_valida_no_mata_el_turno() -> None:
    """**Una decisión de ruteo que falla no puede matar la consulta.**

    El esquema impide un destino inventado —pydantic lo rechaza antes— pero el
    rechazo es una excepción, y propagarla haría que el usuario pierda la
    consulta entera por un problema de formato del que no tiene culpa ni forma
    de enterarse. El fallback es consultar al que falta, que es lo que el modelo
    iba a elegir la mayoría de las veces.
    """
    falso = FakeChatModel(estructurados=[{"destino": "inventado", "motivo": ""}])
    puerta = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)

    comando = await nodo_supervisor(estado(), gateway=puerta)

    assert comando.goto in destinos_posibles()


async def test_el_fallback_respeta_lo_ya_consultado() -> None:
    """Caerse al primero de la lista no puede reintroducir un repetido."""
    falso = FakeChatModel(estructurados=[{"destino": "inventado", "motivo": ""}])
    puerta = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)

    comando = await nodo_supervisor(estado(consultados=["datos"]), gateway=puerta)

    assert comando.goto != "datos"


async def test_nunca_rutea_fuera_de_los_destinos_declarados() -> None:
    """El ensamblado del grafo declara las aristas desde `destinos_posibles`.

    Un destino fuera de esa lista sería una arista que no existe.
    """
    for eleccion in ("datos", "calculo", "normativa", NODO_VERIFICADOR):
        comando = await nodo_supervisor(estado(), gateway=gateway_que_elige(eleccion))
        assert comando.goto in destinos_posibles()


# ─────────────────────────────────────────────────────────────────────────────
# El costo
# ─────────────────────────────────────────────────────────────────────────────


async def test_una_sola_llamada_al_modelo_por_decision() -> None:
    """Es la llamada más frecuente del sistema.

    Dos llamadas por decisión duplicarían el costo del ruteo, que es la mayor
    parte del costo de una consulta compleja.
    """
    falso = FakeChatModel(estructurados=[Ruteo(destino="datos")])
    puerta = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)

    await nodo_supervisor(estado(), gateway=puerta)

    assert falso.llamadas == 1


def test_el_supervisor_usa_el_perfil_router() -> None:
    """Usar el modelo caro acá es el error de costos más común en multi-nodo.

    Se verifica sobre el código porque el gateway falso devuelve el mismo objeto
    para los tres perfiles: la elección no se puede observar en la salida.
    """
    import inspect

    from synapseflow.agents import supervisor

    fuente = inspect.getsource(supervisor._elegir)
    assert '"router"' in fuente
    assert '"synthesis"' not in fuente


# ─────────────────────────────────────────────────────────────────────────────
# El caso de referencia
# ─────────────────────────────────────────────────────────────────────────────


def test_el_prompt_declara_el_orden_del_caso_de_referencia() -> None:
    """datos → cálculo → normativa.

    Es el recorrido que atraviesa toda la documentación del proyecto y el que
    produce el gate de parada. El cálculo necesita que datos haya corrido antes,
    y eso el modelo no lo puede deducir del nombre de los especialistas.
    """
    assert "datos → calculo → normativa" in INSTRUCCION
    assert "Necesita que `datos` haya corrido antes" in INSTRUCCION


def test_el_prompt_dice_que_no_responde_la_pregunta() -> None:
    """Un supervisor que empieza a contestar duplica el trabajo del especialista
    y gasta el modelo barato en una tarea que no le corresponde."""
    assert "No respondés la pregunta" in INSTRUCCION


async def test_el_recorrido_del_caso_de_referencia_es_alcanzable() -> None:
    """Tres decisiones seguidas, arrancando de cero, llegan al verificador."""
    consultados: list[str] = []
    recorrido: list[str] = []

    for eleccion in ("datos", "calculo", "normativa"):
        comando = await nodo_supervisor(
            estado(consultados=consultados), gateway=gateway_que_elige(eleccion)
        )
        recorrido.append(str(comando.goto))
        consultados = comando.update["especialistas_consultados"]

    final = await nodo_supervisor(estado(consultados=consultados), gateway=gateway_que_elige())

    assert recorrido == ["datos", "calculo", "normativa"]
    assert final.goto == NODO_VERIFICADOR


def test_los_destinos_incluyen_a_los_tres_especialistas_y_al_verificador() -> None:
    assert set(destinos_posibles()) == {"datos", "calculo", "normativa", NODO_VERIFICADOR}


def test_el_estado_inicial_arranca_sin_especialistas_consultados() -> None:
    assert estado_inicial("x")["especialistas_consultados"] == []
