"""La frontera de datos, probada sobre un agente que corre de verdad.

**Este es el test que convierte el compromiso 5 en un hecho.**

Los tests de `test_pii.py` verifican el tokenizador aislado y los de
`test_middleware.py` la capa con un pedido de mentira. Ninguno de los dos prueba
lo que el proyecto promete, que es una afirmación sobre el sistema completo:
*ningún campo `restricted` llega al proveedor*.

Acá se arma un agente con `create_agent`, el pipeline de gobernanza real y una
herramienta que devuelve un legajo, y se le pregunta al modelo qué vio.
`FakeChatModel.texto_recibido` es todo lo que le llegó: si el legajo aparece ahí,
cruzó.

La otra mitad importa igual: la respuesta que recibe el usuario tiene que traer
el legajo de vuelta. Un sistema que protege el dato destruyéndolo protege
también al usuario de poder trabajar.

Ver docs/plan/fases/F4-gobernanza.md § F4.6
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.tools import tool

from synapseflow.config import Settings
from synapseflow.governance.middleware import construir_middleware
from synapseflow.governance.pii import Tokenizador, detectar_legajos
from synapseflow.llm.fake import FakeChatModel, Llamada, Respuesta
from synapseflow.ontology import get_ontology

ONTOLOGIA = get_ontology()
LEGAJO = "LEG-00042"
OTRO_LEGAJO = "LEG-00099"


def ajustes(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"SYNAPSEFLOW_PROVIDER": "fake"}
    base.update(overrides)
    return Settings(**base)


@tool
def historial_de_prueba(tag: str) -> str:
    """Devuelve el historial de inspecciones de un activo."""
    return (
        f"Inspecciones de {tag}:\n"
        f"- 2026-02-20 · 6,8 mm · firmada por {LEGAJO}\n"
        f"- 2021-03-10 · 8,4 mm · firmada por {OTRO_LEGAJO}"
    )


def agente_con_gobernanza(
    respuestas: list[Respuesta], *, tokenizador: Tokenizador | None = None, rol: str = "inspector"
) -> tuple[Any, FakeChatModel]:
    """Un agente real con el pipeline de gobernanza y un modelo programado."""
    modelo = FakeChatModel(respuestas=respuestas)
    agente = create_agent(
        modelo,
        [historial_de_prueba],
        middleware=construir_middleware(
            ONTOLOGIA,
            rol,
            settings=ajustes(),
            tokenizador=tokenizador,
        ),
    )
    return agente, modelo


# ─────────────────────────────────────────────────────────────────────────────
# La garantía
# ─────────────────────────────────────────────────────────────────────────────


async def test_un_campo_restricted_nunca_llega_al_proveedor() -> None:
    """**El compromiso 5, sobre el sistema completo.**

    La herramienta devuelve dos legajos en su resultado. Ese resultado vuelve al
    modelo como `ToolMessage` en el turno siguiente, que es el camino por el que
    un dato personal se filtra sin que nadie lo escriba a propósito: nadie puso
    el legajo en el prompt, lo puso la base de datos.
    """
    agente, modelo = agente_con_gobernanza(
        [
            Respuesta(
                herramientas=[Llamada(nombre="historial_de_prueba", argumentos={"tag": "P-1"})]
            ),
            Respuesta(texto="El activo tiene dos inspecciones registradas."),
        ]
    )

    await agente.ainvoke({"messages": [{"role": "user", "content": "Historial de P-1"}]})

    visto = modelo.texto_recibido
    assert LEGAJO not in visto, "un campo `restricted` cruzó hacia el proveedor"
    assert OTRO_LEGAJO not in visto
    assert detectar_legajos(visto) == [], f"se filtró PII: {detectar_legajos(visto)}"


async def test_el_modelo_si_ve_el_token() -> None:
    """El control positivo.

    Sin esto, un middleware que borrara el resultado entero de la herramienta
    pasaría el test anterior. El modelo tiene que poder razonar sobre el
    referente, no quedarse sin dato.
    """
    agente, modelo = agente_con_gobernanza(
        [
            Respuesta(
                herramientas=[Llamada(nombre="historial_de_prueba", argumentos={"tag": "P-1"})]
            ),
            Respuesta(texto="Listo."),
        ]
    )

    await agente.ainvoke({"messages": [{"role": "user", "content": "Historial de P-1"}]})

    visto = modelo.texto_recibido
    assert "«INSPECTOR_1»" in visto
    assert "«INSPECTOR_2»" in visto
    assert "6,8 mm" in visto, "se perdió el dato técnico junto con el personal"


async def test_la_respuesta_al_usuario_trae_el_legajo_de_vuelta() -> None:
    """La otra mitad de la garantía.

    Un sistema que protege el dato destruyéndolo protege también al usuario de
    poder trabajar: si la respuesta dice «avisale a «INSPECTOR_1»», el técnico no
    sabe a quién llamar.
    """
    agente, _ = agente_con_gobernanza(
        [
            Respuesta(
                herramientas=[Llamada(nombre="historial_de_prueba", argumentos={"tag": "P-1"})]
            ),
            Respuesta(texto="Corresponde que «INSPECTOR_1» recalibre el equipo."),
        ]
    )

    estado = await agente.ainvoke({"messages": [{"role": "user", "content": "Historial de P-1"}]})
    final = str(estado["messages"][-1].content)

    assert LEGAJO in final, "la respuesta no se rehidrató: el usuario ve el token"
    assert "«INSPECTOR_1»" not in final


async def test_el_legajo_que_escribe_el_usuario_tampoco_cruza() -> None:
    """La fuga no siempre viene de la base: a veces la escribe la persona."""
    agente, modelo = agente_con_gobernanza([Respuesta(texto="Entendido.")])

    await agente.ainvoke(
        {"messages": [{"role": "user", "content": f"¿Qué firmó {LEGAJO} este año?"}]}
    )

    assert LEGAJO not in modelo.texto_recibido


async def test_el_mapa_de_tokenizacion_queda_disponible_para_la_auditoria() -> None:
    """El log guarda qué vio el proveedor contra qué había de verdad.

    Sin el mapa, un auditor no puede reconstruir a quién se refería el modelo
    cuando dijo «INSPECTOR_1».
    """
    tokenizador = Tokenizador()
    agente, _ = agente_con_gobernanza(
        [
            Respuesta(
                herramientas=[Llamada(nombre="historial_de_prueba", argumentos={"tag": "P-1"})]
            ),
            Respuesta(texto="Listo."),
        ],
        tokenizador=tokenizador,
    )

    await agente.ainvoke({"messages": [{"role": "user", "content": "Historial de P-1"}]})

    assert set(tokenizador.mapa.values()) == {LEGAJO, OTRO_LEGAJO}


# ─────────────────────────────────────────────────────────────────────────────
# El control negativo: sin la capa, el dato SÍ se filtra
# ─────────────────────────────────────────────────────────────────────────────


async def test_sin_la_capa_de_redaccion_el_legajo_llega_al_modelo() -> None:
    """**Sin este test, los de arriba pasarían aunque la herramienta no
    devolviera ningún legajo.**

    Es la comprobación de que la garantía protege de algo real: con la bandera
    apagada, el mismo recorrido filtra los dos legajos.
    """
    modelo = FakeChatModel(
        respuestas=[
            Respuesta(
                herramientas=[Llamada(nombre="historial_de_prueba", argumentos={"tag": "P-1"})]
            ),
            Respuesta(texto="Listo."),
        ]
    )
    agente = create_agent(
        modelo,
        [historial_de_prueba],
        middleware=construir_middleware(
            ONTOLOGIA, "inspector", settings=ajustes(SYNAPSEFLOW_REDACT_PII=False)
        ),
    )

    await agente.ainvoke({"messages": [{"role": "user", "content": "Historial de P-1"}]})

    assert LEGAJO in modelo.texto_recibido, (
        "con la redacción apagada el legajo tendría que filtrarse: si no, el "
        "test de la garantía no está probando nada"
    )


# ─────────────────────────────────────────────────────────────────────────────
# El gate sigue funcionando con la redacción encima
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rol", ["inspector", "supervisor_mantenimiento", "tecnico"])
def test_todo_rol_con_acciones_irreversibles_recibe_las_tres_capas(rol: str) -> None:
    """Las garantías no se pisan entre sí: conviven en el mismo pipeline."""
    from synapseflow.governance.middleware import describir_pipeline

    capas = describir_pipeline(construir_middleware(ONTOLOGIA, rol, settings=ajustes()))

    assert "RedaccionDePII" in capas
    assert "HumanInTheLoopMiddleware" in capas
    assert "ModelCallLimitMiddleware" in capas


async def test_la_redaccion_no_rompe_el_flujo_de_herramientas() -> None:
    """Tokenizar mensajes no puede alterar `tool_calls` ni sus ids.

    Si la copia del mensaje perdiera el `tool_call_id`, el agente quedaría con
    una invocación sin respuesta y el grafo no cerraría el turno.
    """
    agente, modelo = agente_con_gobernanza(
        [
            Respuesta(
                herramientas=[Llamada(nombre="historial_de_prueba", argumentos={"tag": "P-1"})]
            ),
            Respuesta(texto="El activo tiene dos inspecciones."),
        ]
    )

    estado = await agente.ainvoke({"messages": [{"role": "user", "content": "Historial"}]})

    assert modelo.llamadas == 2, "el agente no completó el bucle de herramienta"
    assert str(estado["messages"][-1].content) == "El activo tiene dos inspecciones."
