"""Contrato de las cuatro acciones de lectura.

Dos propiedades gobiernan estos tests y ninguna es sobre el formato del texto:

1. **`content` es caro.** Lo que devuelve una acción entra al contexto del modelo
   y sigue ahí en cada turno siguiente. Un volcado de JSON crudo no es más
   informativo: desplaza la parte de la conversación que importaba.
2. **El legajo del inspector no puede llegar al modelo.** Está clasificado como
   `restricted` en la ontología. La redacción sistemática llega en F4, pero una
   fuga que hoy no se testea es una fuga que en F4 nadie va a buscar.

Ver docs/plan/fases/F2-dominio.md § F2.2
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from synapseflow.config import Provider, Settings
from synapseflow.domain import lecturas
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import ToolResult
from synapseflow.persistence.client import Collections, get_client
from synapseflow.persistence.vectorstore import FirestoreVectorStore

pytestmark = pytest.mark.emulator

LEGAJO = "LEG-00042"


@pytest.fixture
def sufijo() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(autouse=True)
def gateway_falso(monkeypatch: pytest.MonkeyPatch) -> None:
    """El gateway del módulo, apuntado al proveedor falso.

    Sin esto, `buscar_normativa` construiría un cliente de Gemini real y saldría
    a la red. Con el proveedor falso, los embeddings son determinísticos y tienen
    similitud léxica, así que la recuperación se puede verificar de verdad.
    """
    falso = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE))
    monkeypatch.setattr(lecturas, "_GATEWAY", falso)


@pytest.fixture
async def dominio(requiere_emulador: None, sufijo: str) -> AsyncIterator[str]:
    """Un activo con tres inspecciones, y dos fragmentos de normativa."""
    cliente = get_client()
    tag = f"P-{sufijo}-A"
    escritos: list[tuple[str, str]] = []

    async def sembrar(coleccion: str, id_doc: str, datos: dict[str, Any]) -> None:
        await cliente.collection(coleccion).document(id_doc).set(datos)
        escritos.append((coleccion, id_doc))

    await sembrar(
        Collections.ASSETS,
        tag,
        {
            "tag": tag,
            "descripcion": "Separador trifásico",
            "clase": "recipiente_presion",
            "instalacion": f"BAT-{sufijo}",
            "criticidad": "A",
            "estado": "en_servicio",
            "fluido": "gas_humedo",
            "espesor_nominal_mm": 9.5,
            "espesor_minimo_requerido_mm": 7.1,
        },
    )

    for fecha, espesor in (("2019-05-02", 9.0), ("2021-03-10", 8.4), ("2026-02-20", 6.8)):
        await sembrar(
            Collections.INSPECTIONS,
            f"INS-{sufijo}-{fecha}",
            {
                "id_inspeccion": f"INS-{sufijo}-{fecha}",
                "activo": tag,
                "fecha": fecha,
                "espesor_medido_mm": espesor,
                "tecnica": "ultrasonido",
                "hallazgo": "adelgazamiento generalizado",
                "severidad": "media",
                "inspector_legajo": LEGAJO,
            },
        )

    # El corpus se indexa por el vector store para que el vector lo escriba él y
    # no el test: si el formato del documento cambiara, este test tiene que
    # romperse igual que la ingesta real.
    almacen = FirestoreVectorStore(lecturas._gateway().embeddings())
    ids = await almacen.aadd_texts(
        [
            "El espesor mínimo requerido de un componente en servicio no puede ser "
            "inferior a t_min; corresponde retirarlo de servicio o evaluar aptitud.",
            "La frecuencia de calibración de válvulas de alivio se establece según "
            "la criticidad del servicio.",
        ],
        [
            {
                "doc_id": f"API-570-{sufijo}",
                "titulo": "Inspección de cañerías en servicio",
                "seccion": "7.4",
                "tipo_documento": "codigo_api",
                "vigencia": "vigente",
            },
            {
                "doc_id": f"PROC-{sufijo}",
                "titulo": "Procedimiento de válvulas",
                "seccion": "3.2",
                "tipo_documento": "procedimiento_interno",
                "vigencia": "derogado",
            },
        ],
    )
    escritos.extend((Collections.CORPUS_CHUNKS, i) for i in ids)

    yield tag

    for coleccion, id_doc in escritos:
        await cliente.collection(coleccion).document(id_doc).delete()


# ─────────────────────────────────────────────────────────────────────────────
# consultar_activo
# ─────────────────────────────────────────────────────────────────────────────


async def test_la_ficha_incluye_el_espesor_minimo_requerido(dominio: str) -> None:
    """`t_min` es lo que gobierna la aptitud para el servicio.

    Si no está en `content`, el modelo no puede concluir nada sobre si el activo
    sigue apto sin hacer otra llamada a herramienta.
    """
    resultado = await lecturas.consultar_activo(dominio)

    assert isinstance(resultado, ToolResult)
    assert "7.1 mm" in resultado.content
    assert dominio in resultado.content


async def test_un_tag_inexistente_responde_en_lugar_de_fallar() -> None:
    """El modelo tiene que poder decirle al usuario que se equivocó de TAG."""
    resultado = await lecturas.consultar_activo("NO-EXISTE-JAMAS")

    assert "No existe" in resultado.content
    assert resultado.artifact["encontrado"] is False


async def test_el_detalle_completo_va_al_artifact(dominio: str) -> None:
    resultado = await lecturas.consultar_activo(dominio)
    assert resultado.artifact["activo"]["clase"] == "recipiente_presion"


# ─────────────────────────────────────────────────────────────────────────────
# listar_activos
# ─────────────────────────────────────────────────────────────────────────────


async def test_listar_devuelve_una_linea_por_activo(dominio: str, sufijo: str) -> None:
    resultado = await lecturas.listar_activos(instalacion=f"BAT-{sufijo}")

    assert dominio in resultado.content
    assert resultado.artifact["cantidad"] == 1


async def test_listar_sin_resultados_dice_que_criterios_uso() -> None:
    """«No hay activos» a secas haría que el modelo reintente con los mismos filtros."""
    resultado = await lecturas.listar_activos(instalacion="INSTALACION-QUE-NO-EXISTE")

    assert "Ningún activo" in resultado.content
    assert "INSTALACION-QUE-NO-EXISTE" in resultado.content
    assert resultado.artifact["cantidad"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# historial_inspecciones · el test de PII
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_legajo_del_inspector_no_llega_al_modelo(dominio: str) -> None:
    """`inspector_legajo` está clasificado `restricted`: identifica a una persona.

    Este test existe antes que la redacción sistemática de F4 a propósito. Una
    fuga que hoy no se testea es una fuga que en F4 nadie va a buscar, porque
    para entonces la garantía va a figurar como resuelta.
    """
    resultado = await lecturas.historial_inspecciones(dominio)

    assert LEGAJO not in resultado.content, (
        "el legajo del inspector llegó al contexto del modelo: es un campo "
        "`restricted` de la ontología"
    )


async def test_el_legajo_si_va_al_artifact(dominio: str) -> None:
    """El artifact no cruza hacia el proveedor, y la auditoría necesita el dato.

    Redactarlo también acá rompería la trazabilidad sin ganar nada.
    """
    resultado = await lecturas.historial_inspecciones(dominio)
    legajos = {i["inspector_legajo"] for i in resultado.artifact["inspecciones"]}

    assert legajos == {LEGAJO}


async def test_el_historial_vuelve_de_la_mas_reciente_a_la_mas_antigua(dominio: str) -> None:
    resultado = await lecturas.historial_inspecciones(dominio)
    fechas = [i["fecha"] for i in resultado.artifact["inspecciones"]]

    assert fechas == sorted(fechas, reverse=True)
    assert resultado.content.index("2026-02-20") < resultado.content.index("2019-05-02")


async def test_un_activo_sin_inspecciones_lo_dice(sufijo: str) -> None:
    resultado = await lecturas.historial_inspecciones(f"P-{sufijo}-SIN-INSPECCIONES")
    assert "no tiene inspecciones" in resultado.content
    assert resultado.artifact["cantidad"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# buscar_normativa
# ─────────────────────────────────────────────────────────────────────────────


async def test_la_normativa_derogada_no_aparece_como_fundamento(dominio: str, sufijo: str) -> None:
    """El corpus incluye un procedimiento derogado a propósito.

    Que aparezca como fundamento no es un resultado de baja calidad: es un error
    normativo. El filtro por `vigencia` se aplica **antes** de la búsqueda
    vectorial, así que tampoco desperdicia cupo de resultados.
    """
    resultado = await lecturas.buscar_normativa("calibración de válvulas de alivio")

    documentos = {f["doc_id"] for f in resultado.artifact["fragmentos"]}
    assert f"PROC-{sufijo}" not in documentos, "un fragmento derogado se usó como fundamento"
    assert resultado.artifact["filtros"]["vigencia"] == "vigente"


async def test_cada_fragmento_vuelve_con_documento_y_seccion(dominio: str) -> None:
    """Una respuesta sin cita no se puede auditar."""
    resultado = await lecturas.buscar_normativa("espesor mínimo requerido")

    assert resultado.artifact["fragmentos"]
    for fragmento in resultado.artifact["fragmentos"]:
        assert fragmento["doc_id"]
        assert fragmento["seccion"]
        assert f"§{fragmento['seccion']}" in resultado.content


async def test_sin_fundamento_lo_declara_en_lugar_de_improvisar(dominio: str) -> None:
    """Negarse a responder es una métrica de éxito, no un fallo.

    Se filtra por un tipo de documento que no existe en el corpus, así que la
    recuperación vuelve vacía.
    """
    resultado = await lecturas.buscar_normativa("espesor mínimo", tipo_documento="norma_iso")

    assert "No hay fundamento documental" in resultado.content
    assert resultado.artifact["fragmentos"] == []


# ─────────────────────────────────────────────────────────────────────────────
# Contrato común
# ─────────────────────────────────────────────────────────────────────────────


async def test_las_cuatro_acciones_devuelven_toolresult(dominio: str) -> None:
    """El compilador lo verifica en runtime; acá se comprueba antes de compilar."""
    resultados = [
        await lecturas.consultar_activo(dominio),
        await lecturas.listar_activos(),
        await lecturas.historial_inspecciones(dominio),
        await lecturas.buscar_normativa("espesor"),
    ]
    assert all(isinstance(r, ToolResult) for r in resultados)
