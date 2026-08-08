"""Contrato del corredor de evaluación.

Lo que se verifica es la **lógica del corredor**, no la calidad del sistema: la
carga de casos, la agregación, la detección de regresión y el reporte. La calidad
la mide la suite cuando corre de verdad contra un proveedor.

El test que más importa es el de la regresión, porque de él depende que el CI
bloquee un merge. Un detector de regresión que no detecta es peor que ninguno:
da la sensación de que hay una red.

Ver docs/plan/fases/F8-evals.md § F8.3
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from evals import run
from evals.evaluadores.base import Caso, Fragmento, RespuestaDelSistema
from evals.run import MARGEN, Corrida, cargar_casos, ejecuto_algo, regresiones, reportar

VIGENTE = "API-570-2016 §7.4"

FRAGMENTO = Fragmento(
    doc_id="API-570-2016",
    seccion="7.4",
    contenido="Un componente por debajo del mínimo requerido se retira de servicio.",
)


def corrida(**metricas: float) -> Corrida:
    return Corrida(
        id="c-1",
        ts=dt.datetime.now(dt.UTC),
        suite="normativa",
        rama="local",
        metricas=dict(metricas),
        total_casos=10,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Carga de casos
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("suite", ["normativa", "datos", "calculos", "rechazo"])
def test_cada_suite_se_carga(suite: str) -> None:
    casos = cargar_casos(suite)
    assert casos
    assert all(isinstance(c, Caso) for c in casos)


def test_all_carga_las_cuatro_suites() -> None:
    total = sum(len(cargar_casos(s)) for s in ("normativa", "datos", "calculos", "rechazo"))
    assert len(cargar_casos("all")) == total


def test_una_suite_inexistente_falla_con_mensaje_util() -> None:
    with pytest.raises(FileNotFoundError, match="no existe la suite"):
        cargar_casos("inventada")


# ─────────────────────────────────────────────────────────────────────────────
# Detección de regresión · de esto depende que el CI bloquee un merge
# ─────────────────────────────────────────────────────────────────────────────


def test_una_metrica_deterministica_que_baja_es_regresion() -> None:
    """No hay ruido que la explique: si baja, algo se rompió."""
    caidas = regresiones(corrida(precision_de_citas=0.90), corrida(precision_de_citas=0.95))

    assert caidas
    assert "precision_de_citas" in caidas[0]


def test_una_metrica_que_sube_no_es_regresion() -> None:
    assert regresiones(corrida(precision_de_citas=0.99), corrida(precision_de_citas=0.95)) == []


def test_una_metrica_igual_no_es_regresion() -> None:
    assert regresiones(corrida(precision_de_citas=0.95), corrida(precision_de_citas=0.95)) == []


def test_la_fidelidad_tiene_margen_porque_usa_un_modelo() -> None:
    """**Sin margen, el CI fallaría de manera intermitente.**

    Y un CI intermitente enseña al equipo a reintentar hasta que pase, que es la
    peor forma de tener un CI.
    """
    dentro = regresiones(corrida(fidelidad=0.92), corrida(fidelidad=0.95))
    fuera = regresiones(corrida(fidelidad=0.80), corrida(fidelidad=0.95))

    assert dentro == [], "una caída dentro del margen no debería ser regresión"
    assert fuera, "una caída grande sí"


def test_las_deterministicas_no_tienen_margen() -> None:
    """El margen es para la varianza del juez, no indulgencia general."""
    for metrica in ("precision_de_citas", "correccion_del_rechazo", "exactitud_del_calculo"):
        assert MARGEN[metrica] == 0.0


def test_una_metrica_nueva_no_cuenta_como_regresion() -> None:
    """Agregar un evaluador no puede bloquear el PR que lo agrega."""
    assert regresiones(corrida(fidelidad=0.5, metrica_nueva=0.1), corrida(fidelidad=0.5)) == []


def test_se_reportan_todas_las_caidas_y_no_solo_la_primera() -> None:
    """Arreglar de a una obliga a tantas corridas de CI como métricas rotas."""
    caidas = regresiones(
        corrida(precision_de_citas=0.5, correccion_del_rechazo=0.5),
        corrida(precision_de_citas=0.9, correccion_del_rechazo=0.9),
    )
    assert len(caidas) == 2


def test_una_corrida_donde_todo_revento_no_paso_por_regresion() -> None:
    """**Una corrida rota no es una regresión: es un entorno mal configurado.**

    Sin esta distinción, un CI con el proveedor mal puesto reporta fracaso total
    y sale en verde cuando todavía no hay línea base contra la cual comparar.
    """
    assert ejecuto_algo(corrida(ejecucion=0.0)) is False
    assert ejecuto_algo(corrida(ejecucion=0.3)) is True


def test_una_corrida_sin_metrica_de_ejecucion_se_considera_rota() -> None:
    """Es el estado de una corrida que ni siquiera llegó a cargar casos."""
    assert ejecuto_algo(corrida()) is False


# ─────────────────────────────────────────────────────────────────────────────
# Reporte
# ─────────────────────────────────────────────────────────────────────────────


def test_el_reporte_nombra_los_casos_rotos() -> None:
    """**Saber que la fidelidad bajó no sirve; saber qué casos, sí.**"""
    con_fallos = Corrida(
        id="c-2",
        ts=dt.datetime.now(dt.UTC),
        suite="normativa",
        rama="local",
        metricas={"precision_de_citas": 0.5},
        total_casos=2,
        fallados=1,
        casos=[
            {
                "caso": "norm-003",
                "metrica": "precision_de_citas",
                "puntaje": 0.0,
                "aprobado": False,
                "detalle": "cita a secciones que no existen",
            }
        ],
    )

    texto = reportar(con_fallos)

    assert "norm-003" in texto
    assert "no existen" in texto


def test_el_reporte_muestra_el_delta_contra_la_base() -> None:
    texto = reportar(corrida(fidelidad=0.87), corrida(fidelidad=0.91))

    assert "0.870" in texto
    assert "-0.040" in texto or "−0.040" in texto


def test_el_reporte_lo_dice_cuando_no_fallo_nada() -> None:
    assert "Ningún caso falló" in reportar(corrida(fidelidad=1.0))


# ─────────────────────────────────────────────────────────────────────────────
# La CLI
# ─────────────────────────────────────────────────────────────────────────────


def test_la_cli_acepta_comparar_linea_base() -> None:
    """Es la bandera que el CI usa. Si se renombra, el workflow deja de fallar
    ante regresión y nadie se entera."""
    args = run._parser().parse_args(["--suite", "normativa", "--comparar-linea-base"])

    assert args.comparar_linea_base is True
    assert args.suite == "normativa"


def test_la_cli_corre_todas_las_suites_por_defecto() -> None:
    assert run._parser().parse_args([]).suite == "all"


def test_la_cli_rechaza_una_suite_inexistente() -> None:
    with pytest.raises(SystemExit):
        run._parser().parse_args(["--suite", "inventada"])


# ─────────────────────────────────────────────────────────────────────────────
# Evaluación de un caso
# ─────────────────────────────────────────────────────────────────────────────


async def test_un_caso_reune_los_evaluadores_que_aplican() -> None:
    """Los que no corresponden devuelven `None` y no entran al promedio."""
    caso = Caso(
        id="c-1",
        pregunta="p",
        respuesta_esperada="r",
        debe_rechazar=False,
        fuentes=(VIGENTE,),
    )
    respuesta = RespuestaDelSistema(
        texto=f"Corresponde retirar [{VIGENTE}].",
        citas=(VIGENTE,),
        fragmentos=(FRAGMENTO,),
        veredicto="fundamentada",
    )

    resultados = await run.evaluar_caso(caso, respuesta, gateway=_gateway_falso())
    metricas = {r.metrica for r in resultados}

    assert "precision_de_citas" in metricas
    assert "correccion_del_rechazo" in metricas
    # Este caso no declara valor esperado ni cadenas prohibidas.
    assert "exactitud_del_calculo" not in metricas
    assert "no_exposicion_de_datos" not in metricas


def _gateway_falso() -> Any:
    from synapseflow.config import Provider, Settings
    from synapseflow.llm.fake import FakeChatModel
    from synapseflow.llm.gateway import Gateway
    from synapseflow.rag.fundamento import Afirmacion, Dictamen

    falso = FakeChatModel(
        estructurados=[Dictamen(afirmaciones=[Afirmacion(texto="x", respaldada=True)])]
    )
    return Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.emulator
async def test_una_corrida_se_guarda_y_se_recupera_como_linea_base(
    requiere_emulador: None,
) -> None:
    """El CI compara contra la última corrida de `main`."""
    import uuid

    from synapseflow.persistence.client import Collections, get_client

    suite = f"suite-{uuid.uuid4().hex[:8]}"
    cliente = get_client()

    guardada = Corrida(
        id=f"run-{uuid.uuid4().hex[:8]}",
        ts=dt.datetime.now(dt.UTC),
        suite=suite,
        rama="main",
        metricas={"fidelidad": 0.91},
        total_casos=5,
    )

    try:
        await run.guardar(guardada)
        base = await run.linea_base(suite, rama="main")

        assert base is not None
        assert base.metricas["fidelidad"] == 0.91
    finally:
        await cliente.collection(Collections.EVAL_RUNS).document(guardada.id).delete()


@pytest.mark.emulator
async def test_sin_corridas_previas_no_hay_linea_base(requiere_emulador: None) -> None:
    """La primera corrida no puede regresar contra nada.

    Tratarla como fallo bloquearía el primer PR que agregue evals.
    """
    import uuid

    assert await run.linea_base(f"inexistente-{uuid.uuid4().hex[:8]}", rama="main") is None
