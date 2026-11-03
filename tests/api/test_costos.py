"""El costo de cada consulta queda registrado, y el panel lo resume.

El test que más importa es `test_una_consulta_por_la_api_deja_su_costo_en_llm_usage`.
`ContabilidadDeCosto` existió desde F1 con tests propios y **nadie la
conectaba**: en producción `llm_usage` tenía cero documentos. Un test del
callback aislado no lo podía detectar. Este recorre una consulta por HTTP y mira
la colección.

Ver services/api/costos.py
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from services.api.costos import (
    SinAccesoACostosError,
    contabilizar,
    exigir_acceso,
    resumen,
)
from synapseflow.config import Provider, Settings
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.callbacks import CLAVE_PERFIL, ContabilidadDeCosto
from synapseflow.llm.fake import FakeChatModel, Respuesta
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import get_ontology
from synapseflow.persistence.client import Collections
from tests.api.firestore_en_memoria import ClienteEnMemoria

ONTOLOGIA = get_ontology()
AHORA = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.UTC)

INSPECTOR = ExecutionContext(usuario="uid-inspector", rol="inspector")
SUPERVISOR = ExecutionContext(usuario="uid-supervisor", rol="supervisor_mantenimiento")
AUDITOR = ExecutionContext(usuario="uid-auditor", rol="auditor")
TECNICO = ExecutionContext(usuario="uid-tecnico", rol="tecnico")
CONSULTA = ExecutionContext(usuario="uid-consulta", rol="consulta")

# Un modelo con precio en el catálogo: el proveedor falso cotiza a cero.
MODELO_TARIFADO = "gemini-3.6-flash"


@pytest.fixture
def db() -> ClienteEnMemoria:
    return ClienteEnMemoria()


def fila(**campos: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "thread_id": "hilo-1",
        "usuario": INSPECTOR.usuario,
        "perfil": "synthesis",
        "modelo": MODELO_TARIFADO,
        "proveedor": "gemini",
        "tokens_entrada": 1000,
        "tokens_salida": 500,
        "costo_usd": 0.01,
        "latencia_ms": 800,
        "momento": (AHORA - dt.timedelta(days=1)).isoformat(),
        "modelo_no_catalogado": False,
    }
    base.update(campos)
    return base


async def sembrar(db: ClienteEnMemoria, *filas: dict[str, Any]) -> None:
    for f in filas:
        await db.collection(Collections.USAGE).document(f["run_id"]).set(f)


# ─────────────────────────────────────────────────────────────────────────────
# La conexión, de punta a punta
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
def api(monkeypatch: pytest.MonkeyPatch, db: ClienteEnMemoria) -> Any:
    """La API con un agente de verdad sobre el modelo falso y Firestore en memoria."""
    from langchain.agents import create_agent

    from services.api import main
    from services.api.auth import usuario_actual
    from services.api.hilos import reclamar_hilo

    modelo = FakeChatModel(
        por_defecto=Respuesta(
            texto="El activo sigue apto.", tokens_entrada=1200, tokens_salida=300
        ),
        modelo=MODELO_TARIFADO,
    )
    monkeypatch.setattr(main, "grafo_para", lambda _ctx: create_agent(modelo, tools=[]))
    monkeypatch.setattr(
        main, "reclamar_hilo", lambda ctx, hilo: reclamar_hilo(ctx, hilo, cliente=db)
    )
    monkeypatch.setattr(
        main,
        "contabilizar",
        lambda fuente, contabilidad: contabilizar(fuente, contabilidad, cliente=db),
    )
    monkeypatch.setattr(
        main,
        "resumen",
        lambda ctx, dias: resumen(ctx, dias=dias, cliente=db, ontologia=ONTOLOGIA),
    )

    def como(ctx: ExecutionContext) -> None:
        async def _quien() -> ExecutionContext:
            return ctx

        main.app.dependency_overrides[usuario_actual] = _quien

    yield como
    main.app.dependency_overrides.clear()


async def test_una_consulta_por_la_api_deja_su_costo_en_llm_usage(
    cliente: Any, api: Any, db: ClienteEnMemoria
) -> None:
    """**Lo que en producción no pasaba.**"""
    api(INSPECTOR)

    respuesta = await cliente.post(
        "/api/consultas", json={"pregunta": "¿P-2101-A sigue apto?", "thread_id": "hilo-9"}
    )
    assert respuesta.status_code == 200

    filas = list(db.contenido(Collections.USAGE).values())
    assert len(filas) == 1
    assert filas[0]["thread_id"] == "hilo-9"
    assert filas[0]["usuario"] == INSPECTOR.usuario
    assert filas[0]["modelo"] == MODELO_TARIFADO
    assert (filas[0]["tokens_entrada"], filas[0]["tokens_salida"]) == (1200, 300)
    assert filas[0]["costo_usd"] > 0


async def test_el_panel_muestra_lo_que_acaba_de_costar_una_consulta(cliente: Any, api: Any) -> None:
    """La pregunta que justifica el panel, contestada con datos de verdad."""
    api(INSPECTOR)
    await cliente.post("/api/consultas", json={"pregunta": "¿P-2101-A sigue apto?"})

    api(SUPERVISOR)
    cuerpo = (await cliente.get("/api/costos")).json()

    assert cuerpo["totales"]["llamadas"] == 1
    assert cuerpo["totales"]["conversaciones"] == 1
    assert cuerpo["totales"]["costo_usd"] > 0
    assert [g["clave"] for g in cuerpo["por_usuario"]] == [INSPECTOR.usuario]


async def test_un_rol_sin_el_nivel_requerido_recibe_403(cliente: Any, api: Any) -> None:
    api(TECNICO)

    respuesta = await cliente.get("/api/costos")

    assert respuesta.status_code == 403
    assert "confidential" in respuesta.json()["error"]


async def test_los_costos_exigen_identidad(cliente: Any) -> None:
    assert (await cliente.get("/api/costos")).status_code == 401


async def test_un_flujo_cortado_a_mitad_igual_deja_su_costo(db: ClienteEnMemoria) -> None:
    """El modelo ya cobró esas llamadas aunque nadie leyera la respuesta."""
    from services.api.streaming import Evento

    contabilidad = ContabilidadDeCosto(thread_id="hilo-1", usuario=INSPECTOR.usuario)
    modelo = FakeChatModel(por_defecto=Respuesta(texto="ok"), modelo=MODELO_TARIFADO)
    await modelo.ainvoke("hola", config={"callbacks": [contabilidad]})

    async def fuente() -> Any:
        yield Evento(tipo="token", datos={"texto": "uno"})
        yield Evento(tipo="token", datos={"texto": "dos"})

    flujo = contabilizar(fuente(), contabilidad, cliente=db)
    await anext(flujo)
    await flujo.aclose()

    assert len(db.contenido(Collections.USAGE)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# El perfil de cada llamada
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("perfil", ["router", "synthesis", "verifier"])
def test_el_gateway_etiqueta_cada_modelo_con_su_perfil(perfil: str) -> None:
    """Sin la etiqueta, todas las filas decían perfil «desconocido» y el panel no
    podía responder qué tarea se lleva el presupuesto."""
    gateway = Gateway(
        Provider.GEMINI,
        settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.GEMINI, GOOGLE_API_KEY="clave-de-test"),
    )

    modelo: Any = gateway.chat(perfil)

    assert modelo.metadata[CLAVE_PERFIL] == perfil


async def test_la_etiqueta_llega_a_la_fila_de_costo() -> None:
    modelo = FakeChatModel(por_defecto=Respuesta(texto="ok"), modelo=MODELO_TARIFADO)
    modelo.metadata = {CLAVE_PERFIL: "router"}
    contabilidad = ContabilidadDeCosto()

    await modelo.ainvoke("hola", config={"callbacks": [contabilidad]})

    assert contabilidad.consumos[0].perfil == "router"


# ─────────────────────────────────────────────────────────────────────────────
# Quién ve los costos: lo decide la clasificación de la ontología
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ctx", [INSPECTOR, SUPERVISOR, AUDITOR], ids=lambda c: c.rol)
def test_los_roles_con_nivel_confidential_ven_los_costos(ctx: ExecutionContext) -> None:
    exigir_acceso(ctx, ONTOLOGIA)


@pytest.mark.parametrize("ctx", [TECNICO, CONSULTA], ids=lambda c: c.rol)
def test_los_roles_por_debajo_no_los_ven(ctx: ExecutionContext) -> None:
    with pytest.raises(SinAccesoACostosError):
        exigir_acceso(ctx, ONTOLOGIA)


def test_ningun_rol_esta_escrito_a_mano_en_el_modulo() -> None:
    """Si el YAML cambia el nivel de un rol, el panel lo tiene que seguir."""
    from pathlib import Path

    import services.api.costos as modulo

    fuente = Path(modulo.__file__).read_text(encoding="utf-8")
    for rol in ONTOLOGIA.roles:
        assert f'"{rol.id}"' not in fuente


# ─────────────────────────────────────────────────────────────────────────────
# El resumen
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_resumen_agrupa_por_perfil_modelo_usuario_y_dia(db: ClienteEnMemoria) -> None:
    await sembrar(
        db,
        fila(perfil="router", costo_usd=0.001, thread_id="h1"),
        fila(perfil="synthesis", costo_usd=0.02, thread_id="h1"),
        fila(perfil="synthesis", costo_usd=0.03, thread_id="h2", usuario=TECNICO.usuario),
    )

    cuerpo = await resumen(SUPERVISOR, cliente=db, ontologia=ONTOLOGIA, ahora=AHORA)

    assert cuerpo["totales"]["llamadas"] == 3
    assert cuerpo["totales"]["conversaciones"] == 2
    assert cuerpo["totales"]["costo_usd"] == pytest.approx(0.051)
    assert cuerpo["totales"]["costo_por_conversacion_usd"] == pytest.approx(0.0255)
    # De mayor a menor costo: es lo primero que se quiere ver.
    assert [g["clave"] for g in cuerpo["por_perfil"]] == ["synthesis", "router"]
    assert {g["clave"] for g in cuerpo["por_usuario"]} == {INSPECTOR.usuario, TECNICO.usuario}
    assert [g["clave"] for g in cuerpo["por_dia"]] == ["2026-11-02"]


async def test_lo_que_queda_fuera_de_la_ventana_no_se_suma(db: ClienteEnMemoria) -> None:
    await sembrar(
        db,
        fila(costo_usd=0.01),
        fila(costo_usd=5.0, momento=(AHORA - dt.timedelta(days=40)).isoformat()),
    )

    cuerpo = await resumen(SUPERVISOR, dias=30, cliente=db, ontologia=ONTOLOGIA, ahora=AHORA)

    assert cuerpo["totales"]["costo_usd"] == pytest.approx(0.01)


async def test_las_llamadas_sin_precio_se_cuentan_aparte(db: ClienteEnMemoria) -> None:
    """Costo cero por ignorancia no es lo mismo que gratis."""
    await sembrar(db, fila(), fila(modelo="otro", costo_usd=0.0, modelo_no_catalogado=True))

    cuerpo = await resumen(SUPERVISOR, cliente=db, ontologia=ONTOLOGIA, ahora=AHORA)

    assert cuerpo["totales"]["llamadas_sin_precio"] == 1


async def test_pasado_el_tope_el_resumen_avisa(
    db: ClienteEnMemoria, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sumar una parte como si fuera el total sería mentir en el número que
    sostiene las decisiones de costo."""
    import services.api.costos as modulo

    monkeypatch.setattr(modulo, "TOPE_DE_FILAS", 2)
    await sembrar(db, fila(), fila(), fila())

    cuerpo = await resumen(SUPERVISOR, cliente=db, ontologia=ONTOLOGIA, ahora=AHORA)

    assert cuerpo["truncado"] is True
    assert cuerpo["totales"]["llamadas"] == 2


async def test_el_resumen_exige_el_nivel_antes_de_leer(db: ClienteEnMemoria) -> None:
    with pytest.raises(SinAccesoACostosError):
        await resumen(TECNICO, cliente=db, ontologia=ONTOLOGIA, ahora=AHORA)


# ─────────────────────────────────────────────────────────────────────────────
# Contra Firestore de verdad
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.emulator
async def test_el_filtro_por_fecha_funciona_en_firestore(requiere_emulador: None) -> None:
    """`momento` se guarda como texto ISO; esto confirma que el rango ordena bien
    contra Firestore y que el volcado real se lee desde el resumen."""
    hilo = f"test-costos-{uuid.uuid4()}"
    contabilidad = ContabilidadDeCosto(thread_id=hilo, usuario=INSPECTOR.usuario)
    modelo = FakeChatModel(
        por_defecto=Respuesta(texto="ok", tokens_entrada=1000, tokens_salida=500),
        modelo=MODELO_TARIFADO,
    )
    await modelo.ainvoke("hola", config={"callbacks": [contabilidad]})
    await contabilidad.volcar()

    cuerpo = await resumen(SUPERVISOR, dias=1, ontologia=ONTOLOGIA)

    hilos = {g["clave"] for g in cuerpo["por_usuario"]}
    assert INSPECTOR.usuario in hilos
    assert cuerpo["totales"]["costo_usd"] > 0


# ─────────────────────────────────────────────────────────────────────────────
# Lo que la consola necesita saber para ofrecer el panel
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ctx", "esperado"),
    [(SUPERVISOR, True), (AUDITOR, True), (TECNICO, False), (CONSULTA, False)],
    ids=lambda v: getattr(v, "rol", str(v)),
)
async def test_yo_dice_si_el_rol_puede_ver_los_costos(
    cliente: Any, api: Any, ctx: ExecutionContext, esperado: bool
) -> None:
    """La consola no deduce del rol qué mostrar: se lo pregunta a la API, que
    aplica la misma regla que `GET /api/costos`."""
    api(ctx)

    cuerpo = (await cliente.get("/api/yo")).json()

    assert cuerpo["puede_ver_costos"] is esperado
