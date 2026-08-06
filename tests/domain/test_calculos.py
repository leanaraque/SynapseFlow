"""Contrato del cálculo determinístico. Cierra el compromiso 3.

Este módulo no puede tener un error silencioso: un número mal firma un informe.
Por eso los casos son de tabla, con valores calculados a mano, y las fechas están
separadas por múltiplos exactos de 1461 días —cuatro años de 365,25— para que la
aritmética dé redonda y el valor esperado sea verificable a ojo en lugar de
copiado de una corrida.

Los casos de borde no son decoración. Cada uno corresponde a una forma concreta
en que este cálculo podría mentir:

| Caso | Qué pasaría si devolviera un número |
|---|---|
| Una sola medición | Una velocidad inventada sobre un solo punto |
| Fechas iguales | División por cero, o una velocidad infinita |
| Espesor creciente | Vida remanente enorme sobre datos que no son de fiar |
| Velocidad cero | Vida infinita, que en un informe se lee como error |
| Espesor bajo t_min | **Nada: es el caso crítico y el número es válido** |

Ver docs/plan/fases/F2-dominio.md § F2.3
"""

from __future__ import annotations

import datetime as dt

import pytest

from synapseflow.domain.calculos import (
    Diagnostico,
    Medicion,
    analizar,
    velocidad_de_corrosion,
    vida_remanente,
)

# 1461 días son exactamente cuatro años de 365,25.
INICIO = dt.date(2017, 1, 1)
MAS_4 = dt.date(2021, 1, 1)
MAS_8 = dt.date(2025, 1, 1)


def serie(*puntos: tuple[dt.date, float]) -> list[Medicion]:
    return [Medicion(fecha=f, espesor_mm=e) for f, e in puntos]


# ─────────────────────────────────────────────────────────────────────────────
# El cálculo, cuando se puede hacer
# ─────────────────────────────────────────────────────────────────────────────


def test_las_fechas_elegidas_dan_anios_exactos() -> None:
    """Si esto falla, todos los valores esperados de abajo dejan de ser exactos."""
    assert (MAS_4 - INICIO).days == 1461
    assert (MAS_8 - INICIO).days == 2922


def test_velocidad_y_vida_de_un_caso_simple() -> None:
    """10 mm a 6 mm en 4 años son 1 mm/año. Con t_min 5, queda 1 año."""
    analisis = analizar(serie((MAS_4, 10.0), (MAS_8, 6.0)), 5.0)

    assert analisis.diagnostico is Diagnostico.OK
    assert analisis.velocidad_mm_anio == pytest.approx(1.0)
    assert analisis.vida_remanente_anios == pytest.approx(1.0)
    assert analisis.apto is True


def test_vida_remanente_negativa() -> None:
    """**Es el caso crítico del proyecto**, y el número es válido.

    Tratarlo como error dejaría al agente sin nada que decir justo en el caso
    que tiene que escalar a un humano.
    """
    analisis = analizar(serie((MAS_4, 10.0), (MAS_8, 6.0)), 7.0)

    assert analisis.diagnostico is Diagnostico.OK
    assert analisis.vida_remanente_anios == pytest.approx(-1.0)
    assert analisis.apto is False
    assert "no está apto" in analisis.explicacion


def test_gobierna_la_velocidad_de_corto_plazo_cuando_es_mayor() -> None:
    """API 570 toma la peor de las dos, y por una razón concreta.

    Un activo que se corroyó despacio durante ocho años y se aceleró en la
    última campaña tiene un problema nuevo. Promediarlo contra la historia
    entera lo esconde justo cuando importa: acá la vida daría 1,33 años en lugar
    de 1.
    """
    analisis = analizar(serie((INICIO, 12.0), (MAS_4, 10.0), (MAS_8, 6.0)), 5.0)

    assert analisis.velocidad_largo_plazo == pytest.approx(0.75)
    assert analisis.velocidad_corto_plazo == pytest.approx(1.0)
    assert analisis.velocidad_mm_anio == pytest.approx(1.0), "no gobernó la peor velocidad"
    assert analisis.vida_remanente_anios == pytest.approx(1.0)


def test_gobierna_la_de_largo_plazo_cuando_la_reciente_se_desaceleró() -> None:
    """La regla es «la mayor», no «la más reciente»."""
    analisis = analizar(serie((INICIO, 12.0), (MAS_4, 7.0), (MAS_8, 6.0)), 5.0)

    assert analisis.velocidad_largo_plazo == pytest.approx(0.75)
    assert analisis.velocidad_corto_plazo == pytest.approx(0.25)
    assert analisis.velocidad_mm_anio == pytest.approx(0.75)


def test_las_mediciones_desordenadas_se_ordenan_antes_de_calcular() -> None:
    """El repositorio las devuelve de la más reciente a la más antigua.

    Sin ordenar, la velocidad saldría con el signo invertido y el activo crítico
    daría vida remanente positiva. Nada fallaría.
    """
    desordenada = serie((MAS_8, 6.0), (INICIO, 12.0), (MAS_4, 10.0))
    assert analizar(desordenada, 5.0).velocidad_mm_anio == pytest.approx(1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Los casos en que no se puede calcular
# ─────────────────────────────────────────────────────────────────────────────


def test_sin_mediciones_lo_dice() -> None:
    analisis = analizar([], 7.0)
    assert analisis.diagnostico is Diagnostico.SIN_MEDICIONES
    assert analisis.velocidad_mm_anio is None


def test_una_sola_medicion_no_permite_calcular() -> None:
    """Cero significaría «este equipo no se corroe», que es falso y es fuerte."""
    analisis = analizar(serie((MAS_8, 6.0)), 7.0)

    assert analisis.diagnostico is Diagnostico.UNA_SOLA_MEDICION
    assert analisis.velocidad_mm_anio is None
    assert analisis.vida_remanente_anios is None
    # Lo que sí se puede afirmar con un solo punto, se afirma.
    assert analisis.apto is False, "con una sola medición igual se sabe si está bajo t_min"


def test_dos_mediciones_el_mismo_dia_no_dividen_por_cero() -> None:
    analisis = analizar(serie((MAS_8, 8.0), (MAS_8, 6.0)), 5.0)

    assert analisis.diagnostico is Diagnostico.MEDICIONES_SIMULTANEAS
    assert analisis.velocidad_mm_anio is None


def test_espesor_creciente_es_sospechoso() -> None:
    """Un espesor que crece no es corrosión: es un dato que no es de fiar.

    Devolver la velocidad negativa produciría una vida remanente positiva
    enorme, que es la respuesta más peligrosa posible: tranquiliza.
    """
    analisis = analizar(serie((MAS_4, 6.0), (MAS_8, 10.0)), 5.0)

    assert analisis.diagnostico is Diagnostico.ESPESOR_CRECIENTE
    assert analisis.vida_remanente_anios is None
    assert "no es posible" in analisis.explicacion
    assert "reparación no registrada" in analisis.explicacion


def test_una_velocidad_despreciable_no_produce_vida_infinita() -> None:
    """Con 0,0007 mm/año la vida daría miles de años: un número que no informa."""
    analisis = analizar(serie((INICIO, 6.0005), (MAS_8, 6.0)), 5.0)

    assert analisis.diagnostico is Diagnostico.SIN_CORROSION_APRECIABLE
    assert analisis.vida_remanente_anios is None
    assert "despreciable" in analisis.explicacion


# ─────────────────────────────────────────────────────────────────────────────
# Los auxiliares que documenta el plan
# ─────────────────────────────────────────────────────────────────────────────


def test_velocidad_de_corrosion_devuelve_none_cuando_no_se_puede() -> None:
    assert velocidad_de_corrosion(serie((MAS_8, 6.0))) is None
    assert velocidad_de_corrosion([]) is None


def test_velocidad_de_corrosion_no_depende_del_espesor_minimo() -> None:
    """El t_min interviene en la vida remanente, no en la velocidad."""
    assert velocidad_de_corrosion(serie((MAS_4, 10.0), (MAS_8, 6.0))) == pytest.approx(1.0)


def test_vida_remanente_con_velocidad_cero_devuelve_none() -> None:
    assert vida_remanente(9.0, 7.0, 0.0) is None


def test_vida_remanente_admite_resultado_negativo() -> None:
    assert vida_remanente(6.8, 7.1, 0.21) == pytest.approx(-1.43, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# El caso que publica el README
# ─────────────────────────────────────────────────────────────────────────────


def test_el_caso_p_2101_a_reproduce_los_numeros_publicados() -> None:
    """La transcripción del README afirma 0,21 mm/año y −1,43 años.

    Los datos se construyen con las mismas constantes que usa el generador, así
    que si alguien cambia allá el caso crítico, este test lo avisa: el README
    quedaría publicando resultados que el sistema no produce.
    """
    from scripts.generar_datos import (
        ESPESOR_MEDIDO_CRITICO,
        ESPESOR_MINIMO_CRITICO,
        FECHAS_CRITICAS,
        VELOCIDAD_CRITICA,
    )

    # La serie se construye hacia atrás desde la última medición, igual que el
    # generador: se fija el valor final y cada punto anterior suma el desgaste
    # del período.
    espesores = [ESPESOR_MEDIDO_CRITICO]
    for posterior, anterior in zip(FECHAS_CRITICAS[:0:-1], FECHAS_CRITICAS[-2::-1], strict=True):
        anios = (posterior - anterior).days / 365.25
        espesores.append(round(espesores[-1] + VELOCIDAD_CRITICA * anios, 2))
    espesores.reverse()

    analisis = analizar(
        serie(*zip(FECHAS_CRITICAS, espesores, strict=True)), ESPESOR_MINIMO_CRITICO
    )

    assert analisis.diagnostico is Diagnostico.OK
    assert analisis.velocidad_mm_anio == pytest.approx(VELOCIDAD_CRITICA, abs=0.01)
    assert analisis.vida_remanente_anios == pytest.approx(-1.43, abs=0.05)
    assert analisis.apto is False
