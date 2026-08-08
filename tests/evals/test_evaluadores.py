"""Contrato de los evaluadores.

**Un evaluador que no se puede equivocar no mide nada.** Cada uno se ejercita con
un caso que aprueba y con uno que falla, porque una métrica que siempre da 1 es
indistinguible de una que no se está calculando.

Los tres determinísticos corren sin red. El de fidelidad usa el modelo falso, así
que lo que se verifica es qué hace el evaluador con el dictamen, no la calidad
del juicio.

Ver docs/plan/fases/F8-evals.md § F8.2
"""

from __future__ import annotations

import pytest

from evals.evaluadores import calculos, citas, fidelidad, rechazo
from evals.evaluadores.base import Caso, Fragmento, RespuestaDelSistema
from synapseflow.config import Provider, Settings
from synapseflow.llm.fake import FakeChatModel
from synapseflow.llm.gateway import Gateway
from synapseflow.rag.fundamento import Afirmacion, Dictamen, VerificadorDeFundamento

VIGENTE = "API-570-2016 §7.4"
DEROGADA = "PROC-INT-009 §3.2"

FRAGMENTO = Fragmento(
    doc_id="API-570-2016",
    seccion="7.4",
    contenido="Un componente por debajo del mínimo requerido se retira de servicio.",
)


def caso(**campos: object) -> Caso:
    base: dict[str, object] = {
        "id": "c-1",
        "pregunta": "p",
        "respuesta_esperada": "r",
        "debe_rechazar": False,
    }
    base.update(campos)
    return Caso(**base)  # type: ignore[arg-type]


def respuesta(**campos: object) -> RespuestaDelSistema:
    return RespuestaDelSistema(**campos)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# Citas
# ─────────────────────────────────────────────────────────────────────────────


def test_citas_aprueba_una_cita_existente_y_respaldada() -> None:
    resultado = citas.evaluar(
        caso(fuentes=(VIGENTE,)),
        respuesta(citas=(VIGENTE,), fragmentos=(FRAGMENTO,)),
    )

    assert resultado is not None
    assert resultado.aprobado is True


def test_citas_detecta_una_seccion_inexistente() -> None:
    """La alucinación con formato de rigor: la que un revisor no verifica."""
    resultado = citas.evaluar(
        caso(fuentes=(VIGENTE,)),
        respuesta(citas=("API-570-2016 §99.9",), fragmentos=(FRAGMENTO,)),
    )

    assert resultado is not None
    assert resultado.aprobado is False
    assert "no existen" in resultado.detalle


def test_citas_detecta_una_seccion_real_que_no_se_recupero() -> None:
    """Una sección real que no estaba en el contexto no se leyó: se recordó."""
    resultado = citas.evaluar(
        caso(fuentes=(VIGENTE,)),
        respuesta(citas=("API-510-2022 §7.5",), fragmentos=(FRAGMENTO,)),
    )

    assert resultado is not None
    assert resultado.aprobado is False
    assert "no se recuperaron" in resultado.detalle


def test_citas_detecta_normativa_derogada() -> None:
    """No es un resultado de baja calidad: es un error normativo."""
    derogado = Fragmento(doc_id="PROC-INT-009", seccion="3.2", contenido="criterio viejo")
    resultado = citas.evaluar(
        caso(prohibidas=(DEROGADA,)),
        respuesta(citas=(DEROGADA,), fragmentos=(derogado,)),
    )

    assert resultado is not None
    assert resultado.aprobado is False
    assert "derogada" in resultado.detalle


def test_citas_da_puntaje_parcial_si_falta_una_fuente() -> None:
    """Citar la mitad no es lo mismo que no citar nada."""
    resultado = citas.evaluar(
        caso(fuentes=(VIGENTE, "API-510-2022 §7.5")),
        respuesta(citas=(VIGENTE,), fragmentos=(FRAGMENTO,)),
    )

    assert resultado is not None
    assert resultado.aprobado is False
    assert resultado.puntaje == pytest.approx(0.5)


def test_citas_no_aplica_cuando_no_hay_nada_que_medir() -> None:
    """Aprobar acá inflaría la métrica con casos que nunca se verificaron."""
    assert citas.evaluar(caso(), respuesta(texto="7,1 mm")) is None


# ─────────────────────────────────────────────────────────────────────────────
# Rechazo · la métrica que más importa
# ─────────────────────────────────────────────────────────────────────────────


def test_rechazo_aprueba_cuando_se_niega_y_correspondia() -> None:
    resultado = rechazo.evaluar(caso(debe_rechazar=True), respuesta(veredicto="sin_fundamento"))
    assert resultado.aprobado is True


def test_rechazo_penaliza_responder_sin_fundamento() -> None:
    """**El modo de falla peligroso.**"""
    resultado = rechazo.evaluar(
        caso(debe_rechazar=True),
        respuesta(texto="El ciclo se completa en catorce días.", veredicto="fundamentada"),
    )

    assert resultado.aprobado is False
    assert resultado.puntaje == 0.0
    assert "peligroso" in resultado.detalle


def test_rechazo_penaliza_menos_negarse_de_mas() -> None:
    """Negarse a algo que sí está es molesto, no peligroso.

    Distinguirlos permite ver en el reporte si el sistema se volvió peligroso o
    solamente tímido.
    """
    resultado = rechazo.evaluar(caso(debe_rechazar=False), respuesta(veredicto="sin_fundamento"))

    assert resultado.aprobado is False
    assert resultado.puntaje == 0.5
    assert "inútil" in resultado.detalle


def test_rechazo_reconoce_la_negativa_por_el_texto_si_no_hay_veredicto() -> None:
    """Para respuestas que no pasaron por el verificador."""
    resultado = rechazo.evaluar(
        caso(debe_rechazar=True), respuesta(texto="No encontré fundamento en la normativa.")
    )
    assert resultado.aprobado is True


def test_rechazo_siempre_dictamina() -> None:
    """Todo caso declara `debe_rechazar`, así que todo caso aporta a la métrica.

    Es la que sostiene el compromiso 4 y no puede tener casos que no la midan.
    """
    assert rechazo.evaluar(caso(), respuesta(texto="x")) is not None


def test_no_exposicion_detecta_un_legajo_en_la_respuesta() -> None:
    resultado = rechazo.evaluar_no_expone(
        caso(no_debe_contener=("LEG-",)), respuesta(texto="Lo firmó LEG-00042.")
    )

    assert resultado is not None
    assert resultado.aprobado is False


def test_no_exposicion_aprueba_una_respuesta_limpia() -> None:
    resultado = rechazo.evaluar_no_expone(
        caso(no_debe_contener=("LEG-",)), respuesta(texto="Lo firmó el inspector de guardia.")
    )

    assert resultado is not None
    assert resultado.aprobado is True


def test_no_exposicion_no_aplica_si_el_caso_no_lo_declara() -> None:
    assert rechazo.evaluar_no_expone(caso(), respuesta(texto="LEG-00042")) is None


# ─────────────────────────────────────────────────────────────────────────────
# Cálculos
# ─────────────────────────────────────────────────────────────────────────────


def test_calculos_aprueba_dentro_de_la_tolerancia() -> None:
    resultado = calculos.evaluar(
        caso(valor_esperado={"campo": "vida_remanente_anios", "valor": -1.43, "tolerancia": 0.05}),
        respuesta(calculos={"vida_remanente_anios": -1.45}),
    )

    assert resultado is not None
    assert resultado.aprobado is True


def test_calculos_falla_fuera_de_la_tolerancia() -> None:
    resultado = calculos.evaluar(
        caso(valor_esperado={"campo": "vida_remanente_anios", "valor": -1.43, "tolerancia": 0.05}),
        respuesta(calculos={"vida_remanente_anios": 2.0}),
    )

    assert resultado is not None
    assert resultado.aprobado is False


def test_calculos_mide_sobre_el_estado_y_no_sobre_el_texto() -> None:
    """El número que importa es el de la función determinística.

    Medir sobre el texto convertiría esta métrica en una de formato.
    """
    resultado = calculos.evaluar(
        caso(valor_esperado={"campo": "vida_remanente_anios", "valor": -1.43, "tolerancia": 0.05}),
        respuesta(texto="La vida remanente es de -1,43 años.", calculos={}),
    )

    assert resultado is not None
    assert resultado.aprobado is False
    assert "no produjo" in resultado.detalle


def test_calculos_espera_none_donde_no_se_puede_calcular() -> None:
    """Devolver cero significaría «este equipo no se corroe»."""
    esperado = {"campo": "vida_remanente_anios", "valor": None, "tolerancia": 0}

    aprueba = calculos.evaluar(
        caso(valor_esperado=esperado), respuesta(calculos={"vida_remanente_anios": None})
    )
    falla = calculos.evaluar(
        caso(valor_esperado=esperado), respuesta(calculos={"vida_remanente_anios": 0.0})
    )

    assert aprueba is not None and aprueba.aprobado is True
    assert falla is not None and falla.aprobado is False


def test_calculos_compara_booleanos_como_booleanos() -> None:
    """`bool` es subclase de `int`: sin el caso especial, `apto=False` se
    compararía numéricamente contra 0 y pasaría por casualidad."""
    resultado = calculos.evaluar(
        caso(valor_esperado={"campo": "apto", "valor": False, "tolerancia": 0}),
        respuesta(calculos={"apto": True}),
    )

    assert resultado is not None
    assert resultado.aprobado is False


def test_calculos_no_aplica_sin_valor_esperado() -> None:
    assert calculos.evaluar(caso(), respuesta(calculos={"x": 1})) is None


# ─────────────────────────────────────────────────────────────────────────────
# Fidelidad · el único con juez
# ─────────────────────────────────────────────────────────────────────────────


def verificador(*pares: tuple[str, bool]) -> VerificadorDeFundamento:
    falso = FakeChatModel(
        estructurados=[
            Dictamen(afirmaciones=[Afirmacion(texto=t, respaldada=ok) for t, ok in pares])
        ]
    )
    return VerificadorDeFundamento(
        Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)
    )


async def test_fidelidad_aprueba_una_respuesta_respaldada() -> None:
    resultado = await fidelidad.evaluar(
        caso(fuentes=(VIGENTE,)),
        respuesta(texto=f"Corresponde retirar [{VIGENTE}].", fragmentos=(FRAGMENTO,)),
        verificador=verificador(("Corresponde retirar", True)),
    )

    assert resultado is not None
    assert resultado.aprobado is True


async def test_fidelidad_penaliza_lo_que_las_fuentes_no_dicen() -> None:
    resultado = await fidelidad.evaluar(
        caso(fuentes=(VIGENTE,)),
        respuesta(texto=f"Hay que parar la planta entera [{VIGENTE}].", fragmentos=(FRAGMENTO,)),
        verificador=verificador(("Hay que parar la planta entera", False)),
    )

    assert resultado is not None
    assert resultado.aprobado is False


async def test_fidelidad_da_la_proporcion_respaldada_y_no_un_numero_inventado() -> None:
    """Pedirle a un LLM «puntuá del 1 al 10» produce ruido con forma de medición."""
    resultado = await fidelidad.evaluar(
        caso(fuentes=(VIGENTE,)),
        respuesta(texto=f"Dos cosas [{VIGENTE}].", fragmentos=(FRAGMENTO,)),
        verificador=verificador(("una", True), ("otra", False), ("tercera", True)),
    )

    assert resultado is not None
    assert resultado.puntaje == pytest.approx(2 / 3)


async def test_fidelidad_no_juzga_una_negativa_correcta() -> None:
    """Juzgar la fidelidad de un «no sé» no mide nada. Eso lo mide `rechazo`."""
    resultado = await fidelidad.evaluar(
        caso(debe_rechazar=True),
        respuesta(texto="No encontré fundamento.", veredicto="sin_fundamento"),
        verificador=verificador(),
    )

    assert resultado is None


async def test_fidelidad_no_juzga_una_respuesta_vacia() -> None:
    assert await fidelidad.evaluar(caso(), respuesta(texto="  "), verificador=verificador()) is None


async def test_fidelidad_juzga_contra_lo_que_el_sistema_recupero() -> None:
    """Si el retriever falló, la fidelidad tiene que reflejarlo, no compensarlo.

    Sin fragmentos, el verificador rechaza: no hay contra qué respaldar.
    """
    resultado = await fidelidad.evaluar(
        caso(fuentes=(VIGENTE,)),
        respuesta(texto=f"Corresponde retirar [{VIGENTE}].", fragmentos=()),
        verificador=verificador(("x", True)),
    )

    assert resultado is not None
    assert resultado.aprobado is False
