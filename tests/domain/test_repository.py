"""Contrato del repositorio del dominio.

Los tests crean sus propios documentos en lugar de apoyarse en que
`scripts/seed.py` haya corrido: un test que depende del estado de la base falla
por razones que no tienen que ver con lo que verifica, y el que lo hereda pierde
media hora averiguándolo.

Cada test usa un sufijo único en los TAG, así que dos corridas simultáneas —o una
base sucia de una corrida anterior— no se pisan.

Ver docs/plan/fases/F2-dominio.md § F2.1
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from synapseflow.domain.repository import LIMITE_MAXIMO, RepositorioDominio
from synapseflow.persistence.client import Collections, get_client

pytestmark = pytest.mark.emulator


@pytest.fixture
def sufijo() -> str:
    """Discriminante por test, para que los datos no se pisen entre sí."""
    return uuid.uuid4().hex[:8]


def activo(tag: str, **campos: Any) -> dict[str, Any]:
    base = {
        "tag": tag,
        "descripcion": "Recipiente de prueba",
        "clase": "recipiente_presion",
        "instalacion": "BAT-TEST-01",
        "criticidad": "A",
        "estado": "en_servicio",
        "espesor_minimo_requerido_mm": 7.1,
    }
    base.update(campos)
    return base


@pytest.fixture
async def repo(requiere_emulador: None, sufijo: str) -> AsyncIterator[RepositorioDominio]:
    """Repositorio con un puñado de activos e inspecciones propios del test."""
    cliente = get_client()
    escritos: list[tuple[str, str]] = []

    async def sembrar(coleccion: str, id_doc: str, datos: dict[str, Any]) -> None:
        await cliente.collection(coleccion).document(id_doc).set(datos)
        escritos.append((coleccion, id_doc))

    await sembrar(Collections.ASSETS, f"P-{sufijo}-A", activo(f"P-{sufijo}-A"))
    await sembrar(
        Collections.ASSETS,
        f"P-{sufijo}-B",
        activo(f"P-{sufijo}-B", criticidad="C", estado="fuera_de_servicio"),
    )
    await sembrar(
        Collections.ASSETS,
        f"P-{sufijo}-C",
        activo(f"P-{sufijo}-C", instalacion="BAT-OTRA-99"),
    )

    # Tres inspecciones del mismo activo, sembradas en desorden a propósito: si
    # el repositorio no ordenara, el test de orden pasaría por casualidad.
    for fecha, espesor in (("2021-03-10", 8.4), ("2026-02-20", 6.8), ("2019-05-02", 9.0)):
        id_insp = f"INS-{sufijo}-{fecha}"
        await sembrar(
            Collections.INSPECTIONS,
            id_insp,
            {
                "id_inspeccion": id_insp,
                "activo": f"P-{sufijo}-A",
                "fecha": fecha,
                "espesor_medido_mm": espesor,
                "tecnica": "ultrasonido",
                "inspector_legajo": "LEG-00042",
            },
        )

    yield RepositorioDominio(cliente)

    for coleccion, id_doc in escritos:
        await cliente.collection(coleccion).document(id_doc).delete()


# ─────────────────────────────────────────────────────────────────────────────
# Activos
# ─────────────────────────────────────────────────────────────────────────────


async def test_un_activo_se_busca_por_su_tag(repo: RepositorioDominio, sufijo: str) -> None:
    """El id del documento es la clave natural, así que es un `get` y no un escaneo."""
    encontrado = await repo.activo_por_tag(f"P-{sufijo}-A")

    assert encontrado is not None
    assert encontrado["tag"] == f"P-{sufijo}-A"
    assert encontrado["espesor_minimo_requerido_mm"] == 7.1


async def test_un_tag_inexistente_devuelve_none_y_no_lanza(repo: RepositorioDominio) -> None:
    """Que el usuario tipee mal un TAG es una respuesta, no una excepción.

    La acción tiene que poder decirlo en lenguaje natural en lugar de propagar
    un error hasta el agente.
    """
    assert await repo.activo_por_tag("NO-EXISTE-JAMAS") is None


async def test_los_filtros_se_combinan(repo: RepositorioDominio, sufijo: str) -> None:
    encontrados = await repo.listar_activos(instalacion="BAT-TEST-01", criticidad="C")
    tags = {a["tag"] for a in encontrados}

    assert f"P-{sufijo}-B" in tags
    assert f"P-{sufijo}-A" not in tags, "el filtro de criticidad no se aplicó"
    assert f"P-{sufijo}-C" not in tags, "el filtro de instalación no se aplicó"


async def test_sin_filtros_devuelve_activos(repo: RepositorioDominio) -> None:
    assert await repo.listar_activos(limite=5)


async def test_un_limite_alucinado_no_produce_una_lectura_gigante(
    repo: RepositorioDominio,
) -> None:
    """El límite lo elige el modelo: es un parámetro de herramienta.

    Un `limite=10000` inventado no puede convertirse en diez mil documentos
    facturados.
    """
    encontrados = await repo.listar_activos(limite=10_000)
    assert len(encontrados) <= LIMITE_MAXIMO


async def test_un_limite_absurdo_por_abajo_devuelve_al_menos_uno(
    repo: RepositorioDominio,
) -> None:
    """`limite=0` en Firestore no devuelve nada; acotarlo evita una lista vacía
    que el modelo leería como «no hay activos»."""
    assert len(await repo.listar_activos(limite=0)) == 1


async def test_un_activo_se_puede_actualizar(repo: RepositorioDominio, sufijo: str) -> None:
    await repo.actualizar_activo(f"P-{sufijo}-A", {"criticidad": "B"})
    actualizado = await repo.activo_por_tag(f"P-{sufijo}-A")

    assert actualizado is not None
    assert actualizado["criticidad"] == "B"
    assert actualizado["clase"] == "recipiente_presion", "una actualización parcial borró campos"


# ─────────────────────────────────────────────────────────────────────────────
# Inspecciones
# ─────────────────────────────────────────────────────────────────────────────


async def test_las_inspecciones_vuelven_de_la_mas_reciente_a_la_mas_antigua(
    repo: RepositorioDominio, sufijo: str
) -> None:
    """El orden no es cosmético.

    El cálculo de velocidad de corrosión de F2.3 se apoya en la secuencia
    temporal: con la lista al revés, la velocidad saldría con el signo invertido
    y la vida remanente sería positiva justo en el activo que hay que sacar de
    servicio. Nada fallaría.
    """
    inspecciones = await repo.inspecciones_de(f"P-{sufijo}-A")
    fechas = [i["fecha"] for i in inspecciones]

    assert fechas == sorted(fechas, reverse=True)
    assert fechas[0] == "2026-02-20"


async def test_solo_vuelven_las_inspecciones_del_activo_pedido(
    repo: RepositorioDominio, sufijo: str
) -> None:
    inspecciones = await repo.inspecciones_de(f"P-{sufijo}-A")
    assert {i["activo"] for i in inspecciones} == {f"P-{sufijo}-A"}


async def test_un_activo_sin_inspecciones_devuelve_lista_vacia(
    repo: RepositorioDominio, sufijo: str
) -> None:
    assert await repo.inspecciones_de(f"P-{sufijo}-B") == []


async def test_una_inspeccion_se_busca_por_id(repo: RepositorioDominio, sufijo: str) -> None:
    encontrada = await repo.inspeccion_por_id(f"INS-{sufijo}-2026-02-20")
    assert encontrada is not None
    assert encontrada["espesor_medido_mm"] == 6.8


# ─────────────────────────────────────────────────────────────────────────────
# Órdenes de trabajo
# ─────────────────────────────────────────────────────────────────────────────


async def test_guardar_la_misma_orden_dos_veces_no_duplica(
    repo: RepositorioDominio, sufijo: str
) -> None:
    """Una orden duplicada moviliza una cuadrilla dos veces.

    El id del documento es `id_ot`, así que un reintento tras un timeout
    sobreescribe.
    """
    orden = {
        "id_ot": f"OT-{sufijo}",
        "activo": f"P-{sufijo}-A",
        "estado": "borrador",
        "prioridad": "alta",
    }
    try:
        assert await repo.guardar_orden(orden) == f"OT-{sufijo}"
        await repo.guardar_orden(orden)

        assert len(await repo.ordenes_de(f"P-{sufijo}-A")) == 1
    finally:
        await get_client().collection(Collections.WORK_ORDERS).document(f"OT-{sufijo}").delete()


async def test_una_orden_se_puede_leer_y_actualizar(repo: RepositorioDominio, sufijo: str) -> None:
    id_ot = f"OT-{sufijo}-B"
    try:
        await repo.guardar_orden({"id_ot": id_ot, "activo": "X", "estado": "borrador"})
        await repo.actualizar_orden(id_ot, {"estado": "emitida"})

        recuperada = await repo.orden_por_id(id_ot)
        assert recuperada is not None
        assert recuperada["estado"] == "emitida"
    finally:
        await get_client().collection(Collections.WORK_ORDERS).document(id_ot).delete()


async def test_una_orden_inexistente_devuelve_none(repo: RepositorioDominio) -> None:
    assert await repo.orden_por_id("OT-QUE-NO-EXISTE") is None
