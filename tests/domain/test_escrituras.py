"""Contrato de las cuatro acciones de escritura.

**Lo que estos tests NO verifican es que exista el gate de aprobación.** No es un
olvido: estas funciones se escriben como si la aprobación ya hubiera ocurrido, y
el freno lo pone el grafo en F5 desde la ontología. Un chequeo de aprobación acá
duplicaría la lógica en un lugar donde se puede olvidar, y crearía la ilusión de
dos barreras cuando la que vale es una.

Lo que sí se verifica es el resto: que sin contexto no se escriba nada, que un
rol no autorizado no pase, que emitir dos veces no movilice dos cuadrillas, y que
el estado anterior quede registrado para que la auditoría pueda reconstruirlo.

Ver docs/plan/fases/F2-dominio.md § F2.4
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from google.cloud.firestore_v1.base_query import FieldFilter

from synapseflow.domain import escrituras
from synapseflow.domain.repository import RepositorioDominio
from synapseflow.governance.rbac import ContextoRequeridoError, ExecutionContext
from synapseflow.ontology import get_ontology
from synapseflow.persistence.client import Collections, get_client

pytestmark = pytest.mark.emulator

INSPECTOR = ExecutionContext(usuario="uid-inspector", rol="inspector", thread_id="hilo-1")
TECNICO = ExecutionContext(usuario="uid-tecnico", rol="tecnico")
CONSULTA = ExecutionContext(usuario="uid-consulta", rol="consulta")


@pytest.fixture
def sufijo() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture
async def tag(requiere_emulador: None, sufijo: str) -> AsyncIterator[str]:
    """Un activo en servicio con una inspección que lo respalda."""
    cliente = get_client()
    etiqueta = f"P-{sufijo}-A"
    escritos: list[tuple[str, str]] = [
        (Collections.ASSETS, etiqueta),
        (Collections.INSPECTIONS, f"INS-{sufijo}"),
        (Collections.INSPECTIONS, f"INS-{sufijo}-OTRO"),
    ]

    await (
        cliente.collection(Collections.ASSETS)
        .document(etiqueta)
        .set(
            {
                "tag": etiqueta,
                "descripcion": "Separador",
                "clase": "recipiente_presion",
                "instalacion": f"BAT-{sufijo}",
                "criticidad": "B",
                "estado": "en_servicio",
                "espesor_minimo_requerido_mm": 7.1,
            }
        )
    )
    await (
        cliente.collection(Collections.INSPECTIONS)
        .document(f"INS-{sufijo}")
        .set({"id_inspeccion": f"INS-{sufijo}", "activo": etiqueta, "fecha": "2026-02-20"})
    )
    # Una inspección de OTRO activo, para el test que verifica que no sirve como
    # fundamento cruzado.
    await (
        cliente.collection(Collections.INSPECTIONS)
        .document(f"INS-{sufijo}-OTRO")
        .set(
            {"id_inspeccion": f"INS-{sufijo}-OTRO", "activo": "OTRO-ACTIVO", "fecha": "2026-02-20"}
        )
    )

    yield etiqueta

    for coleccion, id_doc in escritos:
        await cliente.collection(coleccion).document(id_doc).delete()

    # El filtro va con `FieldFilter`: la forma posicional está deprecada y emite
    # un UserWarning que `filterwarnings = ["error"]` convierte en fallo — y en
    # un teardown eso rompe todos los tests del módulo, no solo el que lo usa.
    ordenes = cliente.collection(Collections.WORK_ORDERS).where(
        filter=FieldFilter("activo", "==", etiqueta)
    )
    async for doc in ordenes.stream():
        await doc.reference.delete()


# ─────────────────────────────────────────────────────────────────────────────
# Sin identidad no se escribe
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("accion", "argumentos"),
    [
        (
            "registrar_borrador_ot",
            {"tag": "X", "tipo": "correctivo", "descripcion_trabajo": "d", "prioridad": "alta"},
        ),
        ("emitir_orden_trabajo", {"id_ot": "OT-X"}),
        ("solicitar_parada_equipo", {"tag": "X", "motivo": "m", "id_inspeccion": "I"}),
        ("reclasificar_criticidad", {"tag": "X", "criticidad_nueva": "A", "justificacion": "j"}),
    ],
)
async def test_ninguna_escritura_corre_sin_contexto(
    accion: str, argumentos: dict[str, Any]
) -> None:
    """Una escritura sin identidad no se puede auditar.

    La acción se niega a correr en lugar de registrar «anónimo», que es lo que
    dejaría un log inservible justo para la acción que más importa.
    """
    with pytest.raises(ContextoRequeridoError, match=accion):
        await getattr(escrituras, accion)(**argumentos, ctx=None)


async def test_un_rol_no_autorizado_no_puede_reclasificar(tag: str) -> None:
    """`reclasificar_criticidad` la declara solo para `inspector`.

    Es defensa en profundidad: la barrera real es que la herramienta ni siquiera
    aparece en el catálogo de un rol que no puede ejecutarla. Esto cubre la
    invocación por fuera del catálogo.
    """
    with pytest.raises(PermissionError, match="tecnico"):
        await escrituras.reclasificar_criticidad(tag, "A", "porque sí", ctx=TECNICO)


async def test_el_rol_de_consulta_no_puede_pedir_una_parada(tag: str) -> None:
    with pytest.raises(PermissionError):
        await escrituras.solicitar_parada_equipo(tag, "motivo", "INS", ctx=CONSULTA)


async def test_los_roles_declarados_coinciden_con_la_ontologia() -> None:
    """La lista de roles se repite en el YAML y en el módulo.

    La duplicación es deliberada —leer la ontología en cada llamada acoplaría la
    capa de datos al cargador por un dato que cambia una vez por año— pero no
    puede desincronizarse en silencio. Este test es lo que lo impide.
    """
    onto = get_ontology()
    declarados = escrituras.roles_declarados()

    for accion in onto.actions:
        if accion.id in declarados:
            assert sorted(declarados[accion.id]) == sorted(accion.allowed_roles), (
                f"'{accion.id}' declara roles distintos en el YAML y en escrituras.py"
            )


# ─────────────────────────────────────────────────────────────────────────────
# registrar_borrador_ot · reversible
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_borrador_queda_en_estado_borrador(tag: str) -> None:
    """Crear no es emitir. La distinción es la que hace que esta acción no
    necesite aprobación."""
    resultado = await escrituras.registrar_borrador_ot(
        tag, "correctivo", "Reemplazo de tramo", "alta", ctx=TECNICO
    )

    assert resultado.artifact["creado"] is True
    assert resultado.artifact["orden"]["estado"] == "borrador"
    assert "requiere aprobación" in resultado.content


async def test_el_borrador_registra_quien_lo_pidio(tag: str) -> None:
    resultado = await escrituras.registrar_borrador_ot(
        tag, "preventivo", "Inspección interna", "media", ctx=TECNICO
    )
    assert resultado.artifact["orden"]["solicitante"] == TECNICO.usuario
    assert resultado.artifact["contexto"]["rol"] == "tecnico"


async def test_no_se_crea_un_borrador_sobre_un_activo_inexistente() -> None:
    resultado = await escrituras.registrar_borrador_ot(
        "NO-EXISTE", "correctivo", "d", "alta", ctx=TECNICO
    )
    assert resultado.artifact["creado"] is False


# ─────────────────────────────────────────────────────────────────────────────
# emitir_orden_trabajo · irreversible
# ─────────────────────────────────────────────────────────────────────────────


async def test_emitir_cambia_el_estado_y_deja_el_anterior(tag: str) -> None:
    borrador = await escrituras.registrar_borrador_ot(
        tag, "correctivo", "Reemplazo", "alta", ctx=TECNICO
    )
    id_ot = borrador.artifact["orden"]["id_ot"]

    resultado = await escrituras.emitir_orden_trabajo(id_ot, ctx=TECNICO)

    assert resultado.artifact["emitida"] is True
    assert resultado.artifact["estado_anterior"] == "borrador"

    guardada = await RepositorioDominio().orden_por_id(id_ot)
    assert guardada is not None
    assert guardada["estado"] == "emitida"


async def test_emitir_dos_veces_no_moviliza_dos_cuadrillas(tag: str) -> None:
    """El estado es la defensa contra un reintento del agente tras un timeout."""
    borrador = await escrituras.registrar_borrador_ot(
        tag, "correctivo", "Reemplazo", "alta", ctx=TECNICO
    )
    id_ot = borrador.artifact["orden"]["id_ot"]

    await escrituras.emitir_orden_trabajo(id_ot, ctx=TECNICO)
    segunda = await escrituras.emitir_orden_trabajo(id_ot, ctx=TECNICO)

    assert segunda.artifact["emitida"] is False
    assert "no en borrador" in segunda.content


async def test_no_se_emite_una_orden_inexistente() -> None:
    resultado = await escrituras.emitir_orden_trabajo("OT-QUE-NO-EXISTE", ctx=TECNICO)
    assert resultado.artifact["emitida"] is False


# ─────────────────────────────────────────────────────────────────────────────
# solicitar_parada_equipo · irreversible, impacta producción
# ─────────────────────────────────────────────────────────────────────────────


async def test_la_parada_exige_una_inspeccion_que_exista(tag: str) -> None:
    """Una parada apoyada en un id inventado es lo primero que busca un auditor."""
    resultado = await escrituras.solicitar_parada_equipo(
        tag, "espesor bajo t_min", "INS-INVENTADA", ctx=INSPECTOR
    )

    assert resultado.artifact["solicitada"] is False
    assert "hallazgo trazable" in resultado.content


async def test_una_inspeccion_de_otro_activo_no_fundamenta_la_parada(tag: str, sufijo: str) -> None:
    """Es el error que más fácil comete un modelo que arrastra ids de turnos anteriores."""
    resultado = await escrituras.solicitar_parada_equipo(
        tag, "espesor bajo t_min", f"INS-{sufijo}-OTRO", ctx=INSPECTOR
    )

    assert resultado.artifact["solicitada"] is False
    assert "No fundamenta" in resultado.content
    assert resultado.artifact["inspeccion_de"] == "OTRO-ACTIVO"


async def test_la_parada_registra_el_estado_anterior(tag: str, sufijo: str) -> None:
    """«Se cambió el estado» sin decir desde cuál no permite reconstruir nada."""
    resultado = await escrituras.solicitar_parada_equipo(
        tag, "espesor por debajo de t_min", f"INS-{sufijo}", ctx=INSPECTOR
    )

    assert resultado.artifact["solicitada"] is True
    assert resultado.artifact["estado_anterior"] == "en_servicio"

    activo = await RepositorioDominio().activo_por_tag(tag)
    assert activo is not None
    assert activo["estado"] == "parada_solicitada"
    assert activo["parada_inspeccion"] == f"INS-{sufijo}"


# ─────────────────────────────────────────────────────────────────────────────
# reclasificar_criticidad · irreversible
# ─────────────────────────────────────────────────────────────────────────────


async def test_reclasificar_guarda_la_criticidad_previa(tag: str) -> None:
    resultado = await escrituras.reclasificar_criticidad(
        tag, "A", "Vida remanente negativa", ctx=INSPECTOR
    )

    assert resultado.artifact["reclasificado"] is True
    assert resultado.artifact["criticidad_anterior"] == "B"

    activo = await RepositorioDominio().activo_por_tag(tag)
    assert activo is not None
    assert activo["criticidad"] == "A"
    assert activo["criticidad_anterior"] == "B"
    assert activo["criticidad_justificacion"] == "Vida remanente negativa"


async def test_reclasificar_a_la_misma_criticidad_no_escribe(tag: str) -> None:
    """Registrar un cambio que no cambió nada ensucia la auditoría."""
    resultado = await escrituras.reclasificar_criticidad(tag, "B", "sin motivo", ctx=INSPECTOR)

    assert resultado.artifact["reclasificado"] is False
    assert "ya tiene criticidad" in resultado.content
