"""Contrato de los agentes especialistas.

Dos propiedades gobiernan estos tests:

1. **Cada especialista ve solo su subconjunto.** Un agente con las nueve
   herramientas delante elige mal más seguido, y cada una ocupa contexto en cada
   turno.
2. **Un especialista nunca amplía los permisos del rol.** Las herramientas salen
   de `compile_tools(onto, rol)`, así que estrechar es lo único que puede hacer.

El tercero, que no es una propiedad sino una decisión que se puede perder en un
refactor: el agente de cálculo **reporta, no estima**. Si el modelo empieza a
reinterpretar los números, el compromiso 3 se rompe sin que nada falle, porque la
respuesta sigue sonando técnica.

Ver docs/plan/fases/F5-grafo.md § F5.2
"""

from __future__ import annotations

from typing import Any

import pytest

from synapseflow.agents.especialistas import (
    HERRAMIENTAS_POR_ESPECIALISTA,
    PROMPT_CALCULO,
    agente_calculo,
    agente_datos,
    agente_normativa,
    especialistas_disponibles,
)
from synapseflow.config import Provider, Settings
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.fake import FakeChatModel, Llamada, Respuesta
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import compile_tools, get_ontology

# Importar el paquete registra las implementaciones de las nueve acciones.
import synapseflow.domain  # noqa: F401  isort: skip

ONTOLOGIA = get_ontology()
INSPECTOR = ExecutionContext(usuario="uid-1", rol="inspector", thread_id="hilo-1")
CONSULTA = ExecutionContext(usuario="uid-2", rol="consulta", thread_id="hilo-2")


def ajustes(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"SYNAPSEFLOW_PROVIDER": Provider.FAKE}
    base.update(overrides)
    return Settings(**base)


def gateway_falso(modelo: FakeChatModel | None = None) -> Gateway:
    return Gateway(settings=ajustes(), falso=modelo)


def herramientas_de(agente: Any) -> set[str]:
    """Nombres de las herramientas que el agente compilado tiene ligadas.

    Se inspecciona el nodo `tools` del grafo compilado y no la lista que se le
    pasó a `create_agent`: lo que importa es lo que el agente terminó teniendo,
    no lo que se pidió. Un middleware que filtrara herramientas quedaría fuera
    del alcance del test si se mirara la entrada.
    """
    return {h.name for h in agente.nodes["tools"].bound._tools_by_name.values()}


# ─────────────────────────────────────────────────────────────────────────────
# Cada uno ve solo lo suyo
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("constructor", "esperadas"),
    [
        (agente_normativa, HERRAMIENTAS_POR_ESPECIALISTA["normativa"]),
        (agente_datos, HERRAMIENTAS_POR_ESPECIALISTA["datos"]),
        (agente_calculo, HERRAMIENTAS_POR_ESPECIALISTA["calculo"]),
    ],
)
def test_cada_especialista_recibe_su_subconjunto(
    constructor: Any, esperadas: tuple[str, ...]
) -> None:
    agente = constructor(ONTOLOGIA, INSPECTOR, gateway=gateway_falso(), settings=ajustes())
    assert herramientas_de(agente) == set(esperadas)


def test_ningun_especialista_ve_las_nueve() -> None:
    """El punto del particionado: menos herramientas, mejores elecciones."""
    todas = {h.name for h in compile_tools(ONTOLOGIA, INSPECTOR.rol, context=INSPECTOR)}

    for constructor in (agente_normativa, agente_datos, agente_calculo):
        agente = constructor(ONTOLOGIA, INSPECTOR, gateway=gateway_falso(), settings=ajustes())
        assert herramientas_de(agente) < todas


def test_los_subconjuntos_no_se_pisan() -> None:
    """Dos especialistas con la misma herramienta hacen ambiguo el ruteo."""
    vistos: list[set[str]] = [set(v) for v in HERRAMIENTAS_POR_ESPECIALISTA.values()]

    for i, uno in enumerate(vistos):
        for otro in vistos[i + 1 :]:
            assert not (uno & otro), f"herramienta compartida entre especialistas: {uno & otro}"


def test_los_ids_declarados_existen_en_la_ontologia() -> None:
    """Si alguno se renombrara en el YAML, el agente quedaría sin herramientas.

    Fallar al construir el grafo es mejor que fallar en la primera consulta, que
    es donde el usuario lo pagaría.
    """
    declaradas = {a.id for a in ONTOLOGIA.actions}

    for especialista, pedidas in HERRAMIENTAS_POR_ESPECIALISTA.items():
        faltantes = set(pedidas) - declaradas
        assert not faltantes, f"'{especialista}' pide acciones inexistentes: {faltantes}"


# ─────────────────────────────────────────────────────────────────────────────
# Un especialista nunca amplía los permisos del rol
# ─────────────────────────────────────────────────────────────────────────────


def test_un_rol_sin_la_herramienta_no_la_recibe() -> None:
    """**El mínimo privilegio no depende de que el autor se acuerde.**

    El rol `consulta` solo lee normativa pública: no ve `consultar_activo`. El
    agente de datos para ese rol se arma **sin** esa herramienta.

    Antes esto lanzaba `ValueError`, y estaba mal: el catálogo depende del rol
    por diseño, así que exigir las herramientas dejaba a tres de los cinco roles
    sin poder construir el grafo. Quien decide qué hacer con un especialista
    vacío es `especialistas_utiles`, que directamente no lo construye.
    """
    from synapseflow.agents.especialistas import _herramientas_de

    del_rol = {h.name for h in compile_tools(ONTOLOGIA, CONSULTA.rol, context=CONSULTA)}
    assert "consultar_activo" not in del_rol

    assert _herramientas_de("datos", ONTOLOGIA, CONSULTA) == []


def test_un_especialista_vacio_no_entra_al_grafo() -> None:
    """La otra mitad: si no le queda ninguna, el nodo no existe y el supervisor
    no puede rutear a un agente que no tiene con qué contestar."""
    from synapseflow.agents.especialistas import especialistas_utiles

    utiles = especialistas_utiles(ONTOLOGIA, CONSULTA)

    assert "normativa" in utiles
    assert "datos" not in utiles
    assert "calculo" not in utiles


def test_el_rol_de_consulta_si_puede_armar_el_de_normativa() -> None:
    """El control positivo: lo que el rol sí puede ver, lo puede usar."""
    agente = agente_normativa(ONTOLOGIA, CONSULTA, gateway=gateway_falso(), settings=ajustes())
    assert herramientas_de(agente) == {"buscar_normativa"}


# ─────────────────────────────────────────────────────────────────────────────
# La gobernanza va en cada especialista
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_legajo_no_cruza_desde_un_especialista() -> None:
    """El pipeline se aplica a cada uno y no una vez al grafo entero.

    El middleware envuelve la llamada al modelo, y cada especialista hace la
    suya: ponerlo solo en el supervisor dejaría a los tres llamando al proveedor
    sin redacción.
    """
    modelo = FakeChatModel(respuestas=[Respuesta(texto="ok")])
    agente = agente_datos(ONTOLOGIA, INSPECTOR, gateway=gateway_falso(modelo), settings=ajustes())

    await agente.ainvoke({"messages": [{"role": "user", "content": "¿Qué firmó LEG-00042?"}]})

    assert "LEG-00042" not in modelo.texto_recibido


async def test_una_consulta_normal_atraviesa_las_tres_capas() -> None:
    """El pipeline no puede romper el bucle de herramientas.

    Tres middlewares encadenados sobre `wrap_model_call` son tres oportunidades
    de perder un `tool_call_id` y dejar al agente con una invocación sin
    respuesta. Se verifica que el turno cierre.
    """
    modelo = FakeChatModel(
        respuestas=[
            Respuesta(herramientas=[Llamada(nombre="consultar_activo", argumentos={"tag": "X"})]),
            Respuesta(texto="No existe."),
        ]
    )
    agente = agente_datos(ONTOLOGIA, INSPECTOR, gateway=gateway_falso(modelo), settings=ajustes())

    estado = await agente.ainvoke({"messages": [{"role": "user", "content": "Ficha de X"}]})

    assert modelo.llamadas == 2
    assert "No existe." in str(estado["messages"][-1].content)


# ─────────────────────────────────────────────────────────────────────────────
# El de cálculo reporta, no estima
# ─────────────────────────────────────────────────────────────────────────────


def test_el_prompt_de_calculo_prohibe_recalcular() -> None:
    """Es la decisión que sostiene el compromiso 3 del lado del modelo.

    La función determinística ya existe; lo que falta impedir es que el modelo
    "mejore" el número al presentarlo. Si eso pasa, nada falla: la respuesta
    sigue sonando técnica.
    """
    assert "No recalcules" in PROMPT_CALCULO
    assert "no redondees" in PROMPT_CALCULO
    assert "reportás" in PROMPT_CALCULO


def test_el_prompt_de_calculo_declara_valida_la_vida_negativa() -> None:
    """Es el caso crítico del proyecto.

    Un modelo que lo trate como error dejaría al sistema sin nada que decir justo
    donde tiene que escalar a un humano.
    """
    assert "negativa es un resultado válido" in PROMPT_CALCULO


def test_los_tres_especialistas_estan_declarados() -> None:
    assert set(especialistas_disponibles()) == {"normativa", "datos", "calculo"}


# ─────────────────────────────────────────────────────────────────────────────
# El grafo se construye para TODOS los roles
# ─────────────────────────────────────────────────────────────────────────────
#
# Esta sección existe porque un supervisor de mantenimiento no podía construir el
# grafo: el especialista de acciones exigía `reclasificar_criticidad`, que el
# YAML solo le da al inspector.
#
# El rol que existe para APROBAR no podía usar el sistema, y se descubrió
# aprobando una parada contra el sistema desplegado. Los tests previos usaban
# `inspector`, que tiene el catálogo más amplio, así que ninguno lo tocaba.


@pytest.mark.parametrize("rol", [r.id for r in ONTOLOGIA.roles])
def test_todo_rol_puede_construir_su_grafo(rol: str) -> None:
    """**El test que faltaba.**

    Un rol que no puede construir el grafo no puede usar la plataforma, y eso no
    se ve hasta que alguien con ese rol entra.
    """
    from synapseflow.agents.graph import construir_grafo

    ctx = ExecutionContext(usuario=f"uid-{rol}", rol=rol, thread_id="hilo-1")
    ajustes = Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE, GOOGLE_API_KEY="x")

    grafo = construir_grafo(
        ONTOLOGIA,
        ctx,
        gateway=Gateway(settings=ajustes, falso=FakeChatModel(ciclico=True)),
        settings=ajustes,
    )

    assert grafo is not None


@pytest.mark.parametrize("rol", [r.id for r in ONTOLOGIA.roles])
def test_ningun_especialista_recibe_herramientas_fuera_del_rol(rol: str) -> None:
    """La invariante que sí valía desde el principio: un especialista estrecha
    los permisos del rol, nunca los amplía."""
    from synapseflow.agents.especialistas import _herramientas_de

    ctx = ExecutionContext(usuario=f"uid-{rol}", rol=rol, thread_id="hilo-1")
    del_rol = {h.name for h in compile_tools(ONTOLOGIA, rol, context=ctx)}

    for especialista in ("datos", "calculo", "normativa", "acciones"):
        try:
            herramientas = _herramientas_de(especialista, ONTOLOGIA, ctx)
        except ValueError:
            # Un rol al que no le queda ninguna herramienta de ese especialista.
            # Es legítimo —`consulta` no ejecuta acciones— y el error lo dice.
            continue

        fuera = {h.name for h in herramientas} - del_rol
        assert not fuera, f"'{especialista}' le dio a '{rol}' herramientas que no tiene: {fuera}"
