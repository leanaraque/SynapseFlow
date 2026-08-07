"""Velocidad de corrosión y vida remanente, en Python determinístico.

**Este módulo es el compromiso 3 del proyecto.** El modelo no estima estas
magnitudes: las recibe ya calculadas, como hecho, junto con las mediciones que
las sustentan. Un LLM que redacta «la vida remanente es de unos dos años» a
partir de cuatro números en su contexto produce texto convincente y a veces
equivocado, y acá el número termina en un informe que firma un ingeniero.

El método es el de la sección 7 de API 570:

    velocidad = (espesor_anterior − espesor_actual) / años transcurridos
    vida      = (espesor_actual − t_min) / velocidad

## Dos velocidades, y se toma la peor

API 570 calcula la corrosión de **largo plazo** —primera medición contra la
última— y la de **corto plazo** —anteúltima contra la última— y gobierna la más
alta. No es una sutileza: un activo que se corroyó despacio durante quince años
y se aceleró en la última campaña tiene un problema nuevo, y promediarlo contra
la historia entera lo esconde justo cuando importa.

## Por qué tantas cosas devuelven None

Un cálculo que no se puede hacer tiene que decirlo. La alternativa —devolver
cero— es peor de lo que parece: una velocidad de corrosión de cero significa
«este equipo no se corroe», que es una afirmación fuerte y falsa, y produce una
vida remanente infinita. El sistema prefiere responder que no sabe.

Ver docs/plan/fases/F2-dominio.md § F2.3
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from synapseflow.domain.repository import RepositorioDominio
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.ontology import ToolResult, implements

# Días por año. Es la misma constante que usa `scripts/generar_datos.py` para
# construir las series: con 365 en un lado y 365,25 en el otro, la velocidad
# recuperada no coincidiría con la que se generó y los tests de coherencia del
# caso P-2101-A fallarían por un motivo que no tiene nada que ver con el método.
DIAS_POR_ANIO = 365.25

# Por debajo de esto la velocidad se considera indistinguible de cero. Con
# 0,001 mm/año, la vida remanente de un milímetro de margen daría mil años: un
# número que no informa nada y que en un informe se lee como error.
VELOCIDAD_DESPRECIABLE = 0.001


class Diagnostico(StrEnum):
    """Por qué un cálculo no se pudo completar, o cómo salió."""

    OK = "ok"
    SIN_MEDICIONES = "sin_mediciones"
    UNA_SOLA_MEDICION = "una_sola_medicion"
    MEDICIONES_SIMULTANEAS = "mediciones_simultaneas"
    ESPESOR_CRECIENTE = "espesor_creciente"
    SIN_CORROSION_APRECIABLE = "sin_corrosion_apreciable"


class Medicion(BaseModel):
    """Un punto de la serie de espesores."""

    model_config = ConfigDict(frozen=True)

    fecha: dt.date
    espesor_mm: float


class Analisis(BaseModel):
    """Resultado completo, con el porqué cuando no hay número.

    Lleva los pasos intermedios porque un ingeniero que firma tiene derecho a
    auditar el cálculo, no solo a recibir la conclusión.
    """

    model_config = ConfigDict(frozen=True)

    diagnostico: Diagnostico
    explicacion: str
    velocidad_mm_anio: float | None = None
    vida_remanente_anios: float | None = None
    espesor_actual_mm: float | None = None
    espesor_minimo_mm: float | None = None
    apto: bool | None = None
    velocidad_largo_plazo: float | None = None
    velocidad_corto_plazo: float | None = None
    mediciones_usadas: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Núcleo determinístico
# ─────────────────────────────────────────────────────────────────────────────


def analizar(mediciones: list[Medicion], espesor_minimo_mm: float) -> Analisis:
    """Analiza una serie de espesores contra el espesor mínimo requerido."""
    ordenadas = sorted(mediciones, key=lambda m: m.fecha)

    if not ordenadas:
        return Analisis(
            diagnostico=Diagnostico.SIN_MEDICIONES,
            explicacion="No hay mediciones de espesor registradas para este activo.",
            espesor_minimo_mm=espesor_minimo_mm,
        )

    actual = ordenadas[-1]
    apto = actual.espesor_mm >= espesor_minimo_mm

    if len(ordenadas) == 1:
        return Analisis(
            diagnostico=Diagnostico.UNA_SOLA_MEDICION,
            explicacion=(
                "Hay una sola medición de espesor. La velocidad de corrosión "
                "necesita al menos dos campañas en fechas distintas, así que no "
                "se puede estimar la vida remanente."
            ),
            espesor_actual_mm=actual.espesor_mm,
            espesor_minimo_mm=espesor_minimo_mm,
            apto=apto,
            mediciones_usadas=1,
        )

    primera = ordenadas[0]
    anterior = ordenadas[-2]

    anios_largo = _anios_entre(primera.fecha, actual.fecha)
    anios_corto = _anios_entre(anterior.fecha, actual.fecha)

    if anios_largo <= 0 or anios_corto <= 0:
        return Analisis(
            diagnostico=Diagnostico.MEDICIONES_SIMULTANEAS,
            explicacion=(
                "Hay dos mediciones con la misma fecha. Sin tiempo transcurrido "
                "entre ellas no se puede calcular una velocidad de corrosión."
            ),
            espesor_actual_mm=actual.espesor_mm,
            espesor_minimo_mm=espesor_minimo_mm,
            apto=apto,
            mediciones_usadas=len(ordenadas),
        )

    largo = (primera.espesor_mm - actual.espesor_mm) / anios_largo
    corto = (anterior.espesor_mm - actual.espesor_mm) / anios_corto

    if largo < 0 or corto < 0:
        # Un espesor que crece es físicamente imposible por corrosión. Reportarlo
        # es lo correcto: devolver la velocidad negativa produciría una vida
        # remanente positiva enorme sobre un activo cuyos datos no son de fiar.
        return Analisis(
            diagnostico=Diagnostico.ESPESOR_CRECIENTE,
            explicacion=(
                "La serie de espesores crece con el tiempo, lo que no es posible "
                "por corrosión. Puede tratarse de un cambio de punto de medición, "
                "de un error de carga o de una reparación no registrada. "
                "No se calcula vida remanente sobre datos inconsistentes."
            ),
            espesor_actual_mm=actual.espesor_mm,
            espesor_minimo_mm=espesor_minimo_mm,
            apto=apto,
            velocidad_largo_plazo=round(largo, 4),
            velocidad_corto_plazo=round(corto, 4),
            mediciones_usadas=len(ordenadas),
        )

    # API 570 gobierna por la velocidad más alta: la que da la vida más corta.
    velocidad = max(largo, corto)

    if velocidad < VELOCIDAD_DESPRECIABLE:
        return Analisis(
            diagnostico=Diagnostico.SIN_CORROSION_APRECIABLE,
            explicacion=(
                f"La velocidad de corrosión medida es despreciable "
                f"({velocidad:.4f} mm/año). La vida remanente resultante no sería "
                "informativa; corresponde revisarla en la próxima campaña."
            ),
            espesor_actual_mm=actual.espesor_mm,
            espesor_minimo_mm=espesor_minimo_mm,
            apto=apto,
            velocidad_largo_plazo=round(largo, 4),
            velocidad_corto_plazo=round(corto, 4),
            mediciones_usadas=len(ordenadas),
        )

    vida = (actual.espesor_mm - espesor_minimo_mm) / velocidad

    if apto:
        explicacion = (
            f"Espesor actual {actual.espesor_mm} mm sobre un mínimo requerido de "
            f"{espesor_minimo_mm} mm. A {velocidad:.2f} mm/año, la vida remanente "
            f"es de {vida:.2f} años."
        )
    else:
        explicacion = (
            f"Espesor actual {actual.espesor_mm} mm POR DEBAJO del mínimo requerido "
            f"({espesor_minimo_mm} mm). La vida remanente es negativa: "
            f"{vida:.2f} años. El activo no está apto para continuar en servicio."
        )

    return Analisis(
        diagnostico=Diagnostico.OK,
        explicacion=explicacion,
        velocidad_mm_anio=round(velocidad, 4),
        vida_remanente_anios=round(vida, 2),
        espesor_actual_mm=actual.espesor_mm,
        espesor_minimo_mm=espesor_minimo_mm,
        apto=apto,
        velocidad_largo_plazo=round(largo, 4),
        velocidad_corto_plazo=round(corto, 4),
        mediciones_usadas=len(ordenadas),
    )


def velocidad_de_corrosion(mediciones: list[Medicion]) -> float | None:
    """Milímetros por año, según el método de la sección 7 de API 570.

    Devuelve `None` cuando no se puede calcular: hace falta al menos dos
    mediciones en fechas distintas y una serie que no crezca.
    """
    # El espesor mínimo no interviene en la velocidad; se pasa cero para poder
    # reutilizar el análisis sin duplicar las reglas de borde.
    return analizar(mediciones, 0.0).velocidad_mm_anio


def vida_remanente(espesor_actual: float, t_min: float, velocidad: float) -> float | None:
    """Años hasta alcanzar el espesor mínimo. Negativo si ya se pasó.

    Devuelve `None` si la velocidad es despreciable: la vida sería tan grande
    que el número deja de informar.
    """
    if velocidad < VELOCIDAD_DESPRECIABLE:
        return None
    return round((espesor_actual - t_min) / velocidad, 2)


def _anios_entre(desde: dt.date, hasta: dt.date) -> float:
    return (hasta - desde).days / DIAS_POR_ANIO


# ─────────────────────────────────────────────────────────────────────────────
# La acción del dominio
# ─────────────────────────────────────────────────────────────────────────────


@implements("calcular_vida_remanente")
async def calcular_vida_remanente(tag: str, *, ctx: ExecutionContext | None = None) -> ToolResult:
    """Vida remanente de un activo, calculada sobre su historial de inspecciones."""
    repo = RepositorioDominio()
    activo = await repo.activo_por_tag(tag)

    if activo is None:
        return ToolResult(
            content=f"No existe ningún activo con el TAG '{tag}'.",
            artifact={"tag": tag, "encontrado": False},
        )

    t_min = activo.get("espesor_minimo_requerido_mm")
    if t_min is None:
        return ToolResult(
            content=(
                f"El activo '{tag}' no tiene declarado un espesor mínimo requerido, "
                "así que no hay contra qué comparar el espesor medido."
            ),
            artifact={"tag": tag, "encontrado": True, "espesor_minimo_requerido_mm": None},
        )

    inspecciones = await repo.inspecciones_de(tag, limite=50)
    mediciones = [
        Medicion(fecha=_a_fecha(i["fecha"]), espesor_mm=float(i["espesor_medido_mm"]))
        for i in inspecciones
        if i.get("fecha") and i.get("espesor_medido_mm") is not None
    ]

    analisis = analizar(mediciones, float(t_min))

    return ToolResult(
        content=analisis.explicacion,
        artifact={
            "tag": tag,
            "encontrado": True,
            "analisis": analisis.model_dump(mode="json"),
            # Las mediciones que sustentan el número, para que el cálculo se
            # pueda auditar sin volver a la base.
            "mediciones": [
                m.model_dump(mode="json") for m in sorted(mediciones, key=lambda x: x.fecha)
            ],
        },
    )


def _a_fecha(valor: Any) -> dt.date:
    """Fecha de una inspección, venga como `date`, `datetime` o cadena ISO.

    Firestore devuelve `DatetimeWithNanoseconds` para un campo de fecha y una
    cadena si se guardó como texto, que es como lo escribe `scripts/seed.py`.
    """
    if isinstance(valor, dt.datetime):
        return valor.date()
    if isinstance(valor, dt.date):
        return valor
    return dt.date.fromisoformat(str(valor)[:10])
