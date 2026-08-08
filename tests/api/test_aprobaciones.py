"""Contrato de las aprobaciones. **Los negativos son el commit.**

Un gate que frena bien y aprueba mal no protege de nada. Los dos tests que el
plan exige —rol sin permiso, y proponente aprobándose a sí mismo— están abajo, y
con ellos el que sostiene la promesa entera: **lo aprobado es lo ejecutado**.

Esa última no se verifica comparando argumentos: se verifica comprobando que al
aprobar **no se manden argumentos**. El grafo retoma la llamada que ya tenía en
su checkpoint, así que no hay ningún lugar donde meter otros. Una garantía por
estructura no se puede olvidar; una por validación, sí.

Casi todo corre sin emulador, contra un Firestore en memoria. Los tests de
autoridad son los que más importan y no deberían necesitar infraestructura para
poder ejecutarse: un test que solo corre con el emulador levantado es un test que
alguien va a saltear.

Ver docs/plan/fases/F6-api.md § F6.3
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from services.api.aprobaciones import (
    PENDIENTE,
    Decision,
    ErrorDeAprobacion,
    PedidoDeDecision,
    decidir,
    pendientes,
    registrar_al_pasar,
    registrar_pendiente,
)
from services.api.streaming import APROBACION_REQUERIDA, eventos
from synapseflow.governance.auditoria import TipoEvento
from synapseflow.governance.middleware import construir_middleware
from synapseflow.governance.rbac import AutoridadInsuficienteError, ExecutionContext
from synapseflow.llm.fake import FakeChatModel, Llamada, Respuesta
from synapseflow.ontology import get_ontology
from synapseflow.persistence.client import Collections
from tests.api.firestore_en_memoria import ClienteEnMemoria

ONTOLOGIA = get_ontology()

HILO = "hilo-1"
ACCION = "solicitar_parada_equipo"
ARGUMENTOS = {"tag": "P-2101-A", "motivo": "espesor por debajo de t_min"}

INSPECTOR = ExecutionContext(usuario="uid-inspector", rol="inspector", thread_id=HILO)
SUPERVISOR = ExecutionContext(
    usuario="uid-supervisor", rol="supervisor_mantenimiento", thread_id=HILO
)
OTRO_SUPERVISOR = ExecutionContext(
    usuario="uid-supervisor-2", rol="supervisor_mantenimiento", thread_id=HILO
)
TECNICO = ExecutionContext(usuario="uid-tecnico", rol="tecnico", thread_id=HILO)


def gate(nombre: str = ACCION, **args: Any) -> dict[str, Any]:
    """Lo que el traductor de streaming emite cuando se abre un gate."""
    return {
        "herramienta": nombre,
        "argumentos": args or ARGUMENTOS,
        "descripcion": f"[{nombre}] acción irreversible",
        "decisiones": ["approve", "edit", "reject"],
    }


@pytest.fixture
def db() -> ClienteEnMemoria:
    return ClienteEnMemoria()


@pytest.fixture
async def propuesto(db: ClienteEnMemoria) -> ClienteEnMemoria:
    """Un gate ya abierto por el inspector, esperando a un supervisor."""
    await registrar_pendiente(
        INSPECTOR, gate(), checkpoint_id="ckpt-1", cliente=db, ontologia=ONTOLOGIA
    )
    return db


async def aprobar(ctx: ExecutionContext, db: ClienteEnMemoria, **extra: Any) -> Any:
    return await decidir(
        ctx,
        HILO,
        PedidoDeDecision(decision=Decision.APROBAR, **extra),
        cliente=db,
        ontologia=ONTOLOGIA,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Los dos negativos que el plan exige
# ─────────────────────────────────────────────────────────────────────────────


async def test_un_rol_sin_autoridad_no_puede_aprobar(propuesto: ClienteEnMemoria) -> None:
    """**El primer negativo del plan.**

    El catálogo filtra quién *propone*, no quién *aprueba*, y son conjuntos
    distintos: un técnico puede proponer una orden de trabajo y no puede
    emitirla.
    """
    with pytest.raises(AutoridadInsuficienteError) as excinfo:
        await aprobar(TECNICO, propuesto)

    assert "supervisor_mantenimiento" in str(excinfo.value)


async def test_el_proponente_no_puede_aprobar_su_propia_accion(
    propuesto: ClienteEnMemoria,
) -> None:
    """**El segundo negativo, y el que más fácil se deja pasar.**

    Un supervisor que propone una parada y la aprueba él mismo produce
    exactamente el mismo registro de auditoría que uno que la aprobó sin leerla.
    Separación de funciones: en una empresa regulada no es una preferencia.
    """
    await registrar_pendiente(
        SUPERVISOR.model_copy(update={"thread_id": HILO}),
        gate(),
        cliente=propuesto,
        ontologia=ONTOLOGIA,
    )

    with pytest.raises(AutoridadInsuficienteError) as excinfo:
        await aprobar(SUPERVISOR, propuesto)

    assert "no puede aprobarla" in str(excinfo.value)


async def test_un_rechazo_tampoco_lo_puede_firmar_cualquiera(
    propuesto: ClienteEnMemoria,
) -> None:
    """Rechazar también es una decisión de autoridad.

    Si cualquiera pudiera rechazar, cualquiera podría bloquear una parada de
    equipo — que en este dominio es una acción de seguridad.
    """
    with pytest.raises(AutoridadInsuficienteError):
        await decidir(
            TECNICO,
            HILO,
            PedidoDeDecision(decision=Decision.RECHAZAR, motivo="no hace falta"),
            cliente=propuesto,
            ontologia=ONTOLOGIA,
        )


async def test_un_intento_sin_autoridad_no_resuelve_el_pendiente(
    propuesto: ClienteEnMemoria,
) -> None:
    """Si el intento fallido marcara el gate como resuelto, negar el permiso
    habría dejado la propuesta sin poder aprobarse nunca."""
    with pytest.raises(AutoridadInsuficienteError):
        await aprobar(TECNICO, propuesto)

    assert propuesto.contenido(Collections.APPROVALS)[HILO]["estado"] == PENDIENTE


# ─────────────────────────────────────────────────────────────────────────────
# Lo aprobado es lo ejecutado
# ─────────────────────────────────────────────────────────────────────────────


async def test_aprobar_no_manda_argumentos(propuesto: ClienteEnMemoria) -> None:
    """**La garantía central, y es estructural.**

    El comando de reanudación no lleva argumentos: el grafo retoma la llamada
    que ya tenía en su checkpoint. No hay validación que asegure que lo ejecutado
    es lo propuesto porque no hace falta ninguna — no existe el lugar donde
    meter otros argumentos.
    """
    comando, _ = await aprobar(SUPERVISOR, propuesto)

    decisiones = comando.resume["decisions"]
    assert decisiones == [{"type": "approve"}]


async def test_los_argumentos_que_manden_al_aprobar_se_ignoran(
    propuesto: ClienteEnMemoria,
) -> None:
    """Aceptarlos abriría exactamente el agujero que este módulo cierra: aprobar
    la parada de P-2101-A y ejecutar la de otro equipo."""
    comando, _ = await aprobar(SUPERVISOR, propuesto, argumentos={"tag": "P-9999-Z"})

    assert comando.resume["decisions"] == [{"type": "approve"}]


async def test_editar_si_cambia_lo_que_se_ejecuta(propuesto: ClienteEnMemoria) -> None:
    """Es la excepción explícita, y por eso se audita como tal."""
    comando, _ = await decidir(
        SUPERVISOR,
        HILO,
        PedidoDeDecision(
            decision=Decision.EDITAR, argumentos={"tag": "P-2101-B", "motivo": "otro"}
        ),
        cliente=propuesto,
        ontologia=ONTOLOGIA,
    )

    decision = comando.resume["decisions"][0]
    assert decision["type"] == "edit"
    assert decision["edited_action"]["args"] == {"tag": "P-2101-B", "motivo": "otro"}
    assert decision["edited_action"]["name"] == ACCION


async def test_editar_sin_argumentos_nuevos_se_rechaza(propuesto: ClienteEnMemoria) -> None:
    """Editar sin cambiar nada es aprobar, y se registra distinto: dejarlo pasar
    ensucia el log con ediciones que no editaron."""
    with pytest.raises(ErrorDeAprobacion) as excinfo:
        await decidir(
            SUPERVISOR,
            HILO,
            PedidoDeDecision(decision=Decision.EDITAR),
            cliente=propuesto,
            ontologia=ONTOLOGIA,
        )

    assert excinfo.value.status_code == 400


async def test_el_motivo_del_rechazo_vuelve_al_modelo(propuesto: ClienteEnMemoria) -> None:
    """Sin él, el agente puede reintentar la misma acción sin entender por qué se
    la negaron."""
    comando, _ = await decidir(
        SUPERVISOR,
        HILO,
        PedidoDeDecision(decision=Decision.RECHAZAR, motivo="falta el informe de END"),
        cliente=propuesto,
        ontologia=ONTOLOGIA,
    )

    assert comando.resume["decisions"][0]["message"] == "falta el informe de END"


async def test_una_decision_no_permitida_se_rechaza(db: ClienteEnMemoria) -> None:
    """Las decisiones permitidas salen de la ontología, no de la consola."""
    await registrar_pendiente(
        INSPECTOR,
        {**gate(), "decisiones": ["approve", "reject"]},
        cliente=db,
        ontologia=ONTOLOGIA,
    )

    with pytest.raises(ErrorDeAprobacion) as excinfo:
        await decidir(
            SUPERVISOR,
            HILO,
            PedidoDeDecision(decision=Decision.EDITAR, argumentos={"tag": "X"}),
            cliente=db,
            ontologia=ONTOLOGIA,
        )

    assert excinfo.value.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# El estado del pendiente
# ─────────────────────────────────────────────────────────────────────────────


async def test_aprobar_deja_el_pendiente_resuelto(propuesto: ClienteEnMemoria) -> None:
    await aprobar(SUPERVISOR, propuesto)

    fila = propuesto.contenido(Collections.APPROVALS)[HILO]
    assert fila["estado"] == Decision.APROBAR
    assert fila["decidida_por"] == SUPERVISOR.usuario
    assert fila["rol_decisor"] == SUPERVISOR.rol


async def test_no_se_puede_aprobar_dos_veces(propuesto: ClienteEnMemoria) -> None:
    """**409 y no 403: el problema no es quién sos, es que llegaste tarde.**

    Dos supervisores mirando la misma bandeja es el caso normal, no el raro, y
    un 403 los mandaría a revisar permisos que están bien.
    """
    await aprobar(SUPERVISOR, propuesto)

    with pytest.raises(ErrorDeAprobacion) as excinfo:
        await aprobar(OTRO_SUPERVISOR, propuesto)

    assert excinfo.value.status_code == 409
    assert SUPERVISOR.usuario in excinfo.value.detail


async def test_un_hilo_sin_propuesta_es_404(db: ClienteEnMemoria) -> None:
    with pytest.raises(ErrorDeAprobacion) as excinfo:
        await aprobar(SUPERVISOR, db)

    assert excinfo.value.status_code == 404


async def test_una_accion_que_ya_no_existe_en_el_yaml_no_se_aprueba(
    db: ClienteEnMemoria,
) -> None:
    """La propuesta quedó huérfana de un cambio del dominio.

    Aprobarla sería ejecutarla contra reglas que ya no rigen: los aprobadores
    declarados pudieron haber cambiado junto con la acción.
    """
    await registrar_pendiente(INSPECTOR, gate("accion_que_no_existe"), cliente=db)

    with pytest.raises(ErrorDeAprobacion) as excinfo:
        await aprobar(SUPERVISOR, db)

    assert excinfo.value.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# El log de auditoría
# ─────────────────────────────────────────────────────────────────────────────


async def test_la_decision_queda_en_el_log(propuesto: ClienteEnMemoria) -> None:
    await aprobar(SUPERVISOR, propuesto)

    eventos_ = list(propuesto.contenido(Collections.AUDIT_LOG).values())
    assert [e["tipo"] for e in eventos_] == [TipoEvento.APROBACION_CONCEDIDA]


async def test_el_log_guarda_al_proponente_ademas_del_decisor(
    propuesto: ClienteEnMemoria,
) -> None:
    """**Es lo que permite verificar la separación de funciones mirando el log.**

    `user_id` es quien decidió. Sin el proponente registrado aparte, un auditor
    no puede comprobar que no fueron la misma persona.
    """
    await aprobar(SUPERVISOR, propuesto)

    evento = next(iter(propuesto.contenido(Collections.AUDIT_LOG).values()))
    assert evento["user_id"] == SUPERVISOR.usuario
    assert evento["resultado"]["propuesta_por"] == INSPECTOR.usuario


async def test_el_log_guarda_la_llave_del_razonamiento(propuesto: ClienteEnMemoria) -> None:
    """Con `thread_id` y `checkpoint_id` se reconstruye el estado exacto del
    grafo en el momento de la propuesta. Sin ellos, el log dice qué pasó y no
    permite defenderlo."""
    await aprobar(SUPERVISOR, propuesto)

    evento = next(iter(propuesto.contenido(Collections.AUDIT_LOG).values()))
    assert evento["thread_id"] == HILO
    assert evento["checkpoint_id"] == "ckpt-1"


async def test_el_rechazo_se_registra_igual_que_la_aprobacion(
    propuesto: ClienteEnMemoria,
) -> None:
    """Un log que solo guarda lo que se aprobó no permite medir cuántas veces
    alguien frenó algo, que es la métrica que un auditor busca primero."""
    await decidir(
        SUPERVISOR,
        HILO,
        PedidoDeDecision(decision=Decision.RECHAZAR, motivo="falta el END"),
        cliente=propuesto,
        ontologia=ONTOLOGIA,
    )

    evento = next(iter(propuesto.contenido(Collections.AUDIT_LOG).values()))
    assert evento["tipo"] == TipoEvento.APROBACION_RECHAZADA
    assert evento["detalle"] == "falta el END"


async def test_una_edicion_deja_los_argumentos_finales_registrados(
    propuesto: ClienteEnMemoria,
) -> None:
    """Si hay algo ahí, alguien cambió la propuesta. Es lo que hace legible el
    log de un lote de aprobaciones."""
    await decidir(
        SUPERVISOR,
        HILO,
        PedidoDeDecision(decision=Decision.EDITAR, argumentos={"tag": "P-2101-B"}),
        cliente=propuesto,
        ontologia=ONTOLOGIA,
    )

    assert propuesto.contenido(Collections.APPROVALS)[HILO]["argumentos_finales"] == {
        "tag": "P-2101-B"
    }


async def test_aprobar_no_deja_argumentos_finales(propuesto: ClienteEnMemoria) -> None:
    """Su ausencia es lo que distingue una aprobación limpia de una editada."""
    await aprobar(SUPERVISOR, propuesto)

    assert propuesto.contenido(Collections.APPROVALS)[HILO]["argumentos_finales"] == {}


# ─────────────────────────────────────────────────────────────────────────────
# La bandeja
# ─────────────────────────────────────────────────────────────────────────────


async def test_la_bandeja_muestra_lo_que_este_usuario_puede_aprobar(
    propuesto: ClienteEnMemoria,
) -> None:
    filas = await pendientes(SUPERVISOR, cliente=propuesto, ontologia=ONTOLOGIA)

    assert [f["action_id"] for f in filas] == [ACCION]


async def test_la_bandeja_de_quien_no_aprueba_esta_vacia(
    propuesto: ClienteEnMemoria,
) -> None:
    """**No es «todos los pendientes con un aviso».**

    Una bandeja que muestra lo que no se puede aprobar enseña a ignorarla.
    """
    assert await pendientes(TECNICO, cliente=propuesto, ontologia=ONTOLOGIA) == []


async def test_la_propia_propuesta_no_aparece_en_la_bandeja(db: ClienteEnMemoria) -> None:
    """El mismo filtro que el POST, aplicado antes: si la bandeja la ofreciera,
    el POST la rechazaría y el supervisor no entendería por qué."""
    await registrar_pendiente(SUPERVISOR, gate(), cliente=db, ontologia=ONTOLOGIA)

    assert await pendientes(SUPERVISOR, cliente=db, ontologia=ONTOLOGIA) == []


async def test_lo_resuelto_sale_de_la_bandeja(propuesto: ClienteEnMemoria) -> None:
    await aprobar(SUPERVISOR, propuesto)

    assert await pendientes(OTRO_SUPERVISOR, cliente=propuesto, ontologia=ONTOLOGIA) == []


async def test_la_bandeja_lleva_lo_que_la_consola_necesita_mostrar(
    propuesto: ClienteEnMemoria,
) -> None:
    """Sin la descripción y los argumentos, aprobar es firmar un identificador."""
    fila = (await pendientes(SUPERVISOR, cliente=propuesto, ontologia=ONTOLOGIA))[0]

    assert fila["argumentos"] == ARGUMENTOS
    assert fila["descripcion"]
    assert fila["aprobadores"] == ["supervisor_mantenimiento"]
    assert fila["propuesta_por"] == INSPECTOR.usuario


async def test_la_bandeja_ordena_lo_mas_reciente_primero(db: ClienteEnMemoria) -> None:
    """Es la propuesta que alguien está esperando ahora."""
    for indice, hilo in enumerate(("hilo-a", "hilo-b", "hilo-c")):
        ctx = INSPECTOR.model_copy(update={"thread_id": hilo})
        await registrar_pendiente(ctx, gate(), cliente=db, ontologia=ONTOLOGIA)
        db.contenido(Collections.APPROVALS)[hilo]["creado_en"] = f"2026-08-0{indice + 1}T00:00:00"

    filas = await pendientes(SUPERVISOR, cliente=db, ontologia=ONTOLOGIA)

    assert [f["thread_id"] for f in filas] == ["hilo-c", "hilo-b", "hilo-a"]


# ─────────────────────────────────────────────────────────────────────────────
# El registro al pasar
# ─────────────────────────────────────────────────────────────────────────────


class GrafoConEstado:
    """Grafo mínimo que reporta un checkpoint, para el registro al pasar."""

    def __init__(self, checkpoint_id: str = "ckpt-9", falla: bool = False) -> None:
        self._checkpoint_id = checkpoint_id
        self._falla = falla

    async def aget_state(self, _config: Any) -> Any:
        if self._falla:
            raise RuntimeError("Firestore no responde")

        return _Instantanea({"configurable": {"checkpoint_id": self._checkpoint_id}})


class _Instantanea:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


async def flujo(*eventos_: Any) -> Any:
    for evento in eventos_:
        yield evento


async def test_un_gate_del_flujo_queda_anotado_en_la_bandeja(db: ClienteEnMemoria) -> None:
    from services.api.streaming import Evento

    fuente = flujo(Evento(tipo=APROBACION_REQUERIDA, datos={"acciones": [gate()]}))
    emitidos = [
        e
        async for e in registrar_al_pasar(
            INSPECTOR, GrafoConEstado(), fuente, cliente=db, ontologia=ONTOLOGIA
        )
    ]

    assert [e.tipo for e in emitidos] == [APROBACION_REQUERIDA]
    assert db.contenido(Collections.APPROVALS)[HILO]["checkpoint_id"] == "ckpt-9"


async def test_si_la_bandeja_falla_el_flujo_no_se_cae(db: ClienteEnMemoria) -> None:
    """**El usuario ya vio la propuesta y el checkpoint ya está guardado.**

    Perder la fila de la bandeja es un problema de comodidad; romper la respuesta
    por eso sería un problema de verdad. Se anuncia como error propio para que no
    pase inadvertido.
    """
    from services.api.streaming import ERROR, Evento

    fuente = flujo(Evento(tipo=APROBACION_REQUERIDA, datos={"acciones": [gate()]}))
    emitidos = [
        e
        async for e in registrar_al_pasar(
            INSPECTOR, GrafoConEstado(falla=True), fuente, cliente=db, ontologia=ONTOLOGIA
        )
    ]

    assert [e.tipo for e in emitidos] == [APROBACION_REQUERIDA, ERROR]
    assert "sin registrar" in emitidos[-1].datos["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Contra un grafo de verdad
# ─────────────────────────────────────────────────────────────────────────────


def _agente_que_propone_una_parada() -> tuple[Any, list[dict[str, Any]]]:
    """Un agente que propone la parada de P-2101-A y frena en el gate."""
    ejecutado: list[dict[str, Any]] = []

    class Args(BaseModel):
        tag: str
        motivo: str

    async def _parar(tag: str, motivo: str) -> tuple[str, dict[str, Any]]:
        ejecutado.append({"tag": tag, "motivo": motivo})
        return f"parada solicitada para {tag}", {"action_id": ACCION}

    herramienta = StructuredTool(
        name=ACCION,
        description="Solicita la parada de un equipo.",
        args_schema=Args,
        coroutine=_parar,
        func=None,
        response_format="content_and_artifact",
    )

    modelo = FakeChatModel(
        respuestas=[
            Respuesta(
                texto="El activo no está apto. Propongo la parada.",
                herramientas=[Llamada(nombre=ACCION, argumentos=ARGUMENTOS)],
            ),
            Respuesta(texto="Parada registrada."),
        ],
        ciclico=True,
    )

    agente = create_agent(
        model=modelo,
        tools=[herramienta],
        middleware=construir_middleware(ONTOLOGIA, "inspector"),
        checkpointer=InMemorySaver(),
    )
    return agente, ejecutado


async def test_aprobar_ejecuta_exactamente_lo_propuesto() -> None:
    """**El test que hace verdadera la promesa del proyecto.**

    Corre el gate de verdad: el agente propone, frena, un supervisor aprueba y
    la herramienta se ejecuta con los argumentos que se propusieron — que nunca
    viajaron por la aprobación.
    """
    agente, ejecutado = _agente_que_propone_una_parada()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    await agente.ainvoke({"messages": [{"role": "user", "content": "¿sigue apto?"}]}, config)
    assert ejecutado == [], "la parada se ejecutó antes de que nadie la aprobara"

    db = ClienteEnMemoria()
    ctx = INSPECTOR.model_copy(update={"thread_id": config["configurable"]["thread_id"]})
    await registrar_pendiente(ctx, gate(), cliente=db, ontologia=ONTOLOGIA)

    comando, _ = await decidir(
        SUPERVISOR.model_copy(update={"thread_id": ctx.thread_id}),
        str(ctx.thread_id),
        PedidoDeDecision(decision=Decision.APROBAR),
        cliente=db,
        ontologia=ONTOLOGIA,
    )
    await agente.ainvoke(comando, config)

    assert ejecutado == [ARGUMENTOS]


async def test_rechazar_no_materializa_nada() -> None:
    """Un gate que frena y después ejecuta igual no es un gate."""
    agente, ejecutado = _agente_que_propone_una_parada()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    await agente.ainvoke({"messages": [{"role": "user", "content": "¿sigue apto?"}]}, config)

    db = ClienteEnMemoria()
    ctx = INSPECTOR.model_copy(update={"thread_id": config["configurable"]["thread_id"]})
    await registrar_pendiente(ctx, gate(), cliente=db, ontologia=ONTOLOGIA)

    comando, _ = await decidir(
        SUPERVISOR.model_copy(update={"thread_id": ctx.thread_id}),
        str(ctx.thread_id),
        PedidoDeDecision(decision=Decision.RECHAZAR, motivo="falta el END"),
        cliente=db,
        ontologia=ONTOLOGIA,
    )
    await agente.ainvoke(comando, config)

    assert ejecutado == []


async def test_el_flujo_de_la_aprobacion_muestra_la_ejecucion() -> None:
    """Aprobar no es un endpoint que devuelve ok: es el resto del recorrido.

    El supervisor ve ejecutarse la acción que aprobó, por el mismo canal.
    """
    from services.api.streaming import HERRAMIENTA_FIN

    agente, _ = _agente_que_propone_una_parada()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await agente.ainvoke({"messages": [{"role": "user", "content": "¿sigue apto?"}]}, config)

    db = ClienteEnMemoria()
    ctx = INSPECTOR.model_copy(update={"thread_id": config["configurable"]["thread_id"]})
    await registrar_pendiente(ctx, gate(), cliente=db, ontologia=ONTOLOGIA)

    comando, _ = await decidir(
        SUPERVISOR.model_copy(update={"thread_id": ctx.thread_id}),
        str(ctx.thread_id),
        PedidoDeDecision(decision=Decision.APROBAR),
        cliente=db,
        ontologia=ONTOLOGIA,
    )

    emitidos = [e async for e in eventos(agente, comando, config)]

    ejecutadas = [e.datos["herramienta"] for e in emitidos if e.tipo == HERRAMIENTA_FIN]
    assert ACCION in ejecutadas


# ─────────────────────────────────────────────────────────────────────────────
# Los endpoints
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def cliente() -> Any:
    import httpx

    from services.api.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as cliente:
        yield cliente


@pytest.fixture
def como(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Resuelve la identidad sin Firebase. Devuelve el que la fija."""
    from services.api import main
    from services.api.auth import usuario_actual

    def fijar(ctx: ExecutionContext) -> None:
        async def _quien() -> ExecutionContext:
            return ctx

        main.app.dependency_overrides[usuario_actual] = _quien

    yield fijar
    main.app.dependency_overrides.clear()


async def test_la_bandeja_exige_identidad(cliente: Any) -> None:
    """Un pendiente dice qué se propuso sobre qué equipo: no es público."""
    assert (await cliente.get("/api/aprobaciones")).status_code == 401


async def test_decidir_exige_identidad(cliente: Any) -> None:
    """**Sin identidad no hay separación de funciones posible.**

    Si el endpoint respondiera sin token, no habría contra quién comparar al
    proponente.
    """
    respuesta = await cliente.post("/api/aprobaciones/hilo-1", json={"decision": "aprobar"})

    assert respuesta.status_code == 401


async def test_la_bandeja_devuelve_los_pendientes_del_usuario(
    cliente: Any, como: Any, monkeypatch: pytest.MonkeyPatch, propuesto: ClienteEnMemoria
) -> None:
    from services.api import main

    como(SUPERVISOR)
    monkeypatch.setattr(
        main,
        "pendientes",
        lambda ctx: pendientes(ctx, cliente=propuesto, ontologia=ONTOLOGIA),
    )

    cuerpo = (await cliente.get("/api/aprobaciones")).json()

    assert [p["action_id"] for p in cuerpo["pendientes"]] == [ACCION]


async def test_una_decision_sin_autoridad_sale_como_403(
    cliente: Any, como: Any, monkeypatch: pytest.MonkeyPatch, propuesto: ClienteEnMemoria
) -> None:
    """`AutoridadInsuficienteError` hereda de `PermissionError` para esto: la
    capa que decide no sabe de HTTP y la que sabe de HTTP no decide."""
    from services.api import main

    como(TECNICO)
    monkeypatch.setattr(
        main,
        "decidir",
        lambda ctx, hilo, pedido: decidir(
            ctx, hilo, pedido, cliente=propuesto, ontologia=ONTOLOGIA
        ),
    )

    respuesta = await cliente.post(f"/api/aprobaciones/{HILO}", json={"decision": "aprobar"})

    assert respuesta.status_code == 403
    assert "supervisor_mantenimiento" in respuesta.json()["error"]


async def test_un_hilo_inexistente_sale_como_404(
    cliente: Any, como: Any, monkeypatch: pytest.MonkeyPatch, db: ClienteEnMemoria
) -> None:
    """404, 409 y 400 dicen cosas distintas sobre un gate y se resuelven
    distinto: un 400 genérico le deja a la consola adivinar cuál fue."""
    from services.api import main

    como(SUPERVISOR)
    monkeypatch.setattr(
        main,
        "decidir",
        lambda ctx, hilo, pedido: decidir(ctx, hilo, pedido, cliente=db, ontologia=ONTOLOGIA),
    )

    respuesta = await cliente.post(
        "/api/aprobaciones/hilo-que-no-existe", json={"decision": "aprobar"}
    )

    assert respuesta.status_code == 404


async def test_una_decision_que_no_existe_se_rechaza_por_esquema(cliente: Any, como: Any) -> None:
    """El conjunto de decisiones es cerrado: una tercera opción inventada por la
    consola no puede llegar hasta la gobernanza."""
    como(SUPERVISOR)

    respuesta = await cliente.post(f"/api/aprobaciones/{HILO}", json={"decision": "ignorar"})

    assert respuesta.status_code == 422
