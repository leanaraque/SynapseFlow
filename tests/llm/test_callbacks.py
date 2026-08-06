"""Contrato de la contabilidad de costo.

El panel de costos se usa para decidir qué modelo va en qué perfil. Una
contabilidad que miente es peor que no tenerla, así que lo que se verifica acá
son las tres formas en que podría mentir:

1. Cobrando el precio del perfil pedido y no el del modelo que se ejecutó.
2. Sumando cero por un modelo desconocido, indistinguible de uno gratis.
3. Perdiendo la tarea que causó el gasto, que es lo que se le pregunta al panel.

El modelo falso reporta el nombre de un modelo real del catálogo, así que la
resolución de precio se ejercita de verdad sin salir a la red.

Ver docs/plan/fases/F1-gateway.md § F1.4
"""

from __future__ import annotations

from typing import Any

import pytest
from google.cloud.firestore_v1.base_query import FieldFilter

from synapseflow.llm import registry
from synapseflow.llm.callbacks import CLAVE_PERFIL, CLAVE_THREAD, ContabilidadDeCosto
from synapseflow.llm.fake import FakeChatModel, Respuesta
from synapseflow.persistence.client import Collections, get_client

# Un modelo con precio distinto de cero en el catálogo. El proveedor falso
# cotiza a cero a propósito, así que no serviría para verificar el cálculo.
MODELO_TARIFADO = "gemini-3.5-flash"


def modelo_falso(**kwargs: Any) -> FakeChatModel:
    kwargs.setdefault("modelo", MODELO_TARIFADO)
    return FakeChatModel(
        por_defecto=Respuesta(texto="ok", tokens_entrada=1000, tokens_salida=500),
        **kwargs,
    )


async def invocar(
    contabilidad: ContabilidadDeCosto,
    *,
    metadata: dict[str, Any] | None = None,
    modelo: FakeChatModel | None = None,
) -> None:
    await (modelo or modelo_falso()).ainvoke(
        "una pregunta",
        config={"callbacks": [contabilidad], "metadata": metadata or {}},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lo que se cuenta
# ─────────────────────────────────────────────────────────────────────────────


async def test_una_llamada_produce_una_fila() -> None:
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad)

    assert len(contabilidad.consumos) == 1
    consumo = contabilidad.consumos[0]
    assert consumo.tokens_entrada == 1000
    assert consumo.tokens_salida == 500


async def test_el_costo_sale_del_precio_del_modelo_que_se_ejecuto() -> None:
    """Y no del perfil que se pidió.

    Con un respaldo configurado, `synthesis` puede terminar corriendo en el
    modelo de otro proveedor. Cobrarle el precio del primero produciría un panel
    que miente justo cuando algo salió mal.
    """
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad, metadata={CLAVE_PERFIL: "router"})

    spec = registry.spec_por_modelo(MODELO_TARIFADO)
    assert spec is not None
    esperado = registry.costo_de(spec, 1000, 500)

    assert contabilidad.consumos[0].costo_usd == pytest.approx(esperado)
    assert contabilidad.consumos[0].costo_usd > 0, (
        "una llamada con mil tokens de entrada no puede costar cero"
    )


async def test_un_modelo_fuera_del_catalogo_se_marca_en_lugar_de_valer_cero() -> None:
    """Un costo cero indistinguible de un costo real es peor que un hueco visible.

    Si el catálogo se queda atrás respecto de lo que el código invoca, el panel
    tiene que poder mostrarlo como lo que es: consumo sin precio conocido.
    """
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad, modelo=modelo_falso(modelo="modelo-que-nadie-declaro"))

    consumo = contabilidad.consumos[0]
    assert consumo.modelo_no_catalogado is True
    assert consumo.costo_usd == 0.0
    assert consumo.tokens_entrada == 1000, "los tokens se cuentan igual, aunque no haya precio"


async def test_se_registra_la_latencia() -> None:
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad)
    assert contabilidad.consumos[0].latencia_ms >= 0


# ─────────────────────────────────────────────────────────────────────────────
# La tarea que causó el gasto
# ─────────────────────────────────────────────────────────────────────────────


async def test_el_perfil_sale_de_la_metadata_de_la_invocacion() -> None:
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad, metadata={CLAVE_PERFIL: "synthesis"})
    assert contabilidad.consumos[0].perfil == "synthesis"


async def test_el_perfil_cae_al_declarado_en_el_constructor() -> None:
    """Para call sites que hacen una sola clase de llamada."""
    contabilidad = ContabilidadDeCosto(perfil="verifier")
    await invocar(contabilidad)
    assert contabilidad.consumos[0].perfil == "verifier"


async def test_sin_perfil_se_registra_como_desconocido() -> None:
    """Y no se descarta la fila: el gasto ocurrió igual."""
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad)
    assert contabilidad.consumos[0].perfil == "desconocido"


async def test_el_thread_id_correlaciona_la_conversacion() -> None:
    """Es lo que permite responder «cuánto costó esta conversación»."""
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad, metadata={CLAVE_THREAD: "hilo-42"})
    assert contabilidad.consumos[0].thread_id == "hilo-42"


async def test_la_metadata_de_la_invocacion_gana_sobre_el_constructor() -> None:
    """Un handler de sesión sirve para varios hilos si cada uno puede declararse."""
    contabilidad = ContabilidadDeCosto(thread_id="hilo-por-defecto")
    await invocar(contabilidad, metadata={CLAVE_THREAD: "hilo-explicito"})
    assert contabilidad.consumos[0].thread_id == "hilo-explicito"


async def test_el_costo_se_agrupa_por_perfil() -> None:
    """La pregunta al panel no es «cuánto gastamos» sino «en qué»."""
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad, metadata={CLAVE_PERFIL: "router"})
    await invocar(contabilidad, metadata={CLAVE_PERFIL: "router"})
    await invocar(contabilidad, metadata={CLAVE_PERFIL: "synthesis"})

    por_perfil = contabilidad.por_perfil()
    assert set(por_perfil) == {"router", "synthesis"}
    assert por_perfil["router"] == pytest.approx(2 * por_perfil["synthesis"])
    assert contabilidad.total_usd == pytest.approx(sum(por_perfil.values()))


async def test_varias_llamadas_acumulan() -> None:
    contabilidad = ContabilidadDeCosto()
    for _ in range(3):
        await invocar(contabilidad)

    assert len(contabilidad.consumos) == 3
    assert contabilidad.tokens_totales == 3 * 1500
    assert len({c.run_id for c in contabilidad.consumos}) == 3, "cada llamada, un run_id distinto"


# ─────────────────────────────────────────────────────────────────────────────
# Robustez de la instrumentación
# ─────────────────────────────────────────────────────────────────────────────


async def test_una_llamada_que_falla_no_deja_estado_colgado() -> None:
    """`_en_vuelo` crecería sin límite en un proceso de larga vida.

    Un proveedor que devuelve error no es un caso raro, y la contabilidad no
    puede ser una fuga de memoria.
    """
    contabilidad = ContabilidadDeCosto()
    from uuid import uuid4

    run_id = uuid4()
    await contabilidad.on_chat_model_start({}, [], run_id=run_id)
    assert contabilidad._en_vuelo

    await contabilidad.on_llm_error(RuntimeError("el proveedor devolvió 500"), run_id=run_id)
    assert not contabilidad._en_vuelo
    assert not contabilidad.consumos, "una llamada que falló no se contabiliza"


async def test_un_final_sin_comienzo_se_ignora() -> None:
    """Pasa si el handler se engancha a mitad de una ejecución.

    No es un error del sistema, así que no se inventa una fila con datos que no
    se tienen.
    """
    from uuid import uuid4

    from langchain_core.outputs import LLMResult

    contabilidad = ContabilidadDeCosto()
    await contabilidad.on_llm_end(LLMResult(generations=[]), run_id=uuid4())
    assert not contabilidad.consumos


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────


async def test_volcar_sin_nada_acumulado_no_escribe() -> None:
    assert await ContabilidadDeCosto().volcar() == 0


@pytest.mark.emulator
async def test_volcar_escribe_una_fila_por_llamada(requiere_emulador: None) -> None:
    contabilidad = ContabilidadDeCosto(thread_id=f"hilo-{id(object())}")
    await invocar(contabilidad, metadata={CLAVE_PERFIL: "synthesis"})
    await invocar(contabilidad, metadata={CLAVE_PERFIL: "router"})
    run_ids = [c.run_id for c in contabilidad.consumos]

    assert await contabilidad.volcar() == 2
    assert not contabilidad.consumos, "volcar limpia el acumulador"

    coleccion = get_client().collection(Collections.USAGE)
    for run_id in run_ids:
        documento = await coleccion.document(run_id).get()
        assert documento.exists, f"no se escribió la fila {run_id}"
        assert documento.to_dict()["tokens_entrada"] == 1000


@pytest.mark.emulator
async def test_volcar_dos_veces_no_duplica(requiere_emulador: None) -> None:
    """El id del documento es el run_id, que LangChain genera único por llamada.

    Un reintento del volcado —por un timeout de red, por ejemplo— tiene que
    sobreescribir y no duplicar: si no, un corte de red inflaría la factura
    reportada sin que nadie lo note.
    """
    contabilidad = ContabilidadDeCosto()
    await invocar(contabilidad)
    consumo = contabilidad.consumos[0]

    await contabilidad.volcar(limpiar=False)
    await contabilidad.volcar()

    # El filtro va con `FieldFilter` y no posicional: la forma posicional está
    # deprecada y emite un UserWarning que `filterwarnings = ["error"]` convierte
    # en fallo. Ver docs/plan/00-convenciones.md § Hallazgo 5.
    coleccion = get_client().collection(Collections.USAGE)
    consulta = coleccion.where(filter=FieldFilter("run_id", "==", consumo.run_id))
    encontrados = [doc async for doc in consulta.stream()]
    assert len(encontrados) == 1
