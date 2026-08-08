"""Los datos generados tienen que servir para lo que el proyecto necesita.

Estos tests no verifican el generador: verifican **propiedades de los datos**.
La distinción importa porque es lo que decide qué falla cuando algo se rompe. Si
mañana el generador se reescribe entero, estos tests deben seguir pasando; si
deja de existir un activo por debajo de su `t_min`, deben fallar aunque el
generador funcione perfectamente.

Cada uno protege algo concreto de una fase posterior:

- sin un activo bajo `t_min`, el recorrido de F5 nunca llega al gate de
  aprobación y el compromiso 2 no se puede ejercitar;
- sin espesores monótonamente decrecientes, el cálculo de velocidad de corrosión
  de F2.3 produce números sin sentido físico;
- sin un activo de una sola medición, el caso de borde de F2.3 —no se puede
  calcular velocidad con un solo punto— no tiene con qué probarse;
- sin integridad referencial, cualquier herramienta que expanda el contexto de
  una consulta rompe en runtime.

Ver docs/plan/fases/F0-datos.md § F0.4
"""

from __future__ import annotations

import datetime as dt
import itertools
import random
from typing import Any, NamedTuple

import pytest

from scripts.generar_datos import (
    ESPESOR_MEDIDO_CRITICO,
    ESPESOR_MINIMO_CRITICO,
    TAG_CRITICO,
    VELOCIDAD_CRITICA,
    generar_activos,
    generar_inspecciones,
    generar_instalaciones,
    generar_ordenes,
)

# ─────────────────────────────────────────────────────────────────────────────
# Los datos se generan en proceso
#
# `data/generado/` está en .gitignore —es derivado, se reconstruye con la
# semilla— así que en un clon limpio y en CI no existe. Un test que lo leyera se
# saltearía solo, que es la peor forma de fallar: verde sin haber probado nada.
#
# Las fixtures viven en este módulo y no en un conftest para que el tipo
# `Dominio` se pueda importar sin convertir el directorio en un paquete: ningún
# otro directorio de tests del repositorio tiene `__init__.py`.
# ─────────────────────────────────────────────────────────────────────────────

# La 42 es la semilla por defecto y la que produce el caso de referencia del
# README. Las otras están para que ninguna invariante dependa de ella: una que
# solo se sostiene con la semilla por defecto no es una invariante, es una
# coincidencia que se rompe la primera vez que alguien regenere con otra.
SEMILLAS = (42, 7, 2026)


class Dominio(NamedTuple):
    semilla: int
    instalaciones: list[dict[str, Any]]
    activos: list[dict[str, Any]]
    inspecciones: list[dict[str, Any]]
    ordenes: list[dict[str, Any]]

    def inspecciones_de(self, tag: str) -> list[dict[str, Any]]:
        """Historial de un activo, de la medición más antigua a la más reciente."""
        return sorted(
            (i for i in self.inspecciones if i["activo"] == tag),
            key=lambda i: str(i["fecha"]),
        )


def construir(semilla: int) -> Dominio:
    rng = random.Random(semilla)
    instalaciones = generar_instalaciones(6, rng)
    activos = generar_activos(instalaciones, 60, rng)
    inspecciones = generar_inspecciones(activos, rng)
    ordenes = generar_ordenes(activos, inspecciones, rng)
    return Dominio(semilla, instalaciones, activos, inspecciones, ordenes)


@pytest.fixture(scope="session", params=SEMILLAS, ids=lambda s: f"semilla-{s}")
def dominio(request: pytest.FixtureRequest) -> Dominio:
    return construir(int(request.param))


@pytest.fixture(scope="session")
def dominio_de_referencia() -> Dominio:
    """El dominio de la semilla por defecto, que es el del README."""
    return construir(42)


def _con_espesor(activo: dict[str, Any]) -> bool:
    return "espesor_minimo_requerido_mm" in activo


# ─────────────────────────────────────────────────────────────────────────────
# Las cuatro invariantes del plan
# ─────────────────────────────────────────────────────────────────────────────


def test_hay_un_activo_bajo_t_min(dominio: Dominio) -> None:
    """Sin esto no se puede ejercitar el recorrido hasta el gate de aprobación."""
    criticos = [
        a["tag"]
        for a in dominio.activos
        if _con_espesor(a)
        and any(
            i.get("espesor_medido_mm") is not None
            and i["espesor_medido_mm"] < a["espesor_minimo_requerido_mm"]
            for i in dominio.inspecciones_de(a["tag"])
        )
    ]
    assert criticos, (
        "ningún activo quedó por debajo de su t_min: el recorrido del proyecto "
        "nunca llegaría a proponer una parada de equipo"
    )
    assert TAG_CRITICO in criticos, (
        f"{TAG_CRITICO} es el caso de referencia de toda la documentación y "
        f"tiene que estar entre los críticos. Críticos hallados: {criticos}"
    )


def test_corrosion_es_monotona(dominio: Dominio) -> None:
    """Los espesores de un activo deben decrecer en el tiempo.

    Un espesor creciente indicaría una medición mal tomada. Puede existir como
    caso de borde deliberado, pero no como regla, y hoy no existe ninguno: el
    generador construye la serie hacia atrás desde la última medición, así que
    la monotonía es una propiedad de construcción y este test la custodia.
    """
    fallas: list[str] = []

    for activo in dominio.activos:
        medidos = [
            (i["fecha"], i["espesor_medido_mm"])
            for i in dominio.inspecciones_de(activo["tag"])
            if i.get("espesor_medido_mm") is not None
        ]
        for (fecha_a, esp_a), (fecha_b, esp_b) in itertools.pairwise(medidos):
            if esp_b >= esp_a:
                fallas.append(f"{activo['tag']}: {esp_a} mm ({fecha_a}) → {esp_b} mm ({fecha_b})")

    assert not fallas, "hay activos cuyo espesor no decrece:\n  " + "\n  ".join(fallas)


def test_hay_un_activo_con_una_sola_medicion(dominio: Dominio) -> None:
    """Caso de borde: con un solo punto no se puede calcular velocidad de corrosión."""
    de_una = [
        a["tag"]
        for a in dominio.activos
        if _con_espesor(a) and len(dominio.inspecciones_de(a["tag"])) == 1
    ]
    assert de_una, (
        "ningún activo con espesor tiene una sola medición: F2.3 no tendría con "
        "qué probar que se niega a calcular una velocidad de corrosión"
    )


def test_toda_inspeccion_apunta_a_un_activo_existente(dominio: Dominio) -> None:
    """Integridad referencial de los datos generados."""
    tags = {a["tag"] for a in dominio.activos}
    instalaciones = {i["codigo"] for i in dominio.instalaciones}
    inspecciones = {i["id_inspeccion"] for i in dominio.inspecciones}

    huerfanas = [i["id_inspeccion"] for i in dominio.inspecciones if i["activo"] not in tags]
    assert not huerfanas, f"inspecciones que apuntan a un activo inexistente: {huerfanas[:5]}"

    sin_instalacion = [a["tag"] for a in dominio.activos if a["instalacion"] not in instalaciones]
    assert not sin_instalacion, f"activos en una instalación inexistente: {sin_instalacion[:5]}"

    ot_huerfanas = [o["id_ot"] for o in dominio.ordenes if o["activo"] not in tags]
    assert not ot_huerfanas, f"órdenes sobre un activo inexistente: {ot_huerfanas[:5]}"

    ot_sin_origen = [
        o["id_ot"]
        for o in dominio.ordenes
        if o.get("inspeccion_origen") and o["inspeccion_origen"] not in inspecciones
    ]
    assert not ot_sin_origen, (
        "órdenes que dicen originarse en una inspección inexistente: "
        f"{ot_sin_origen[:5]}. Es el vínculo hallazgo → acción correctiva que "
        "un auditor externo pide primero."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Coherencia del dominio
# ─────────────────────────────────────────────────────────────────────────────


def test_la_severidad_concuerda_con_la_medicion(dominio: Dominio) -> None:
    """Un hallazgo `sin_desvio` sobre un espesor bajo t_min sería contradictorio.

    Si los datos se contradicen, ninguna respuesta del sistema puede ser
    correcta: el agente tendría que elegir a cuál de los dos campos creerle.
    """
    incoherentes = []
    for activo in dominio.activos:
        if not _con_espesor(activo):
            continue
        t_min = activo["espesor_minimo_requerido_mm"]
        for i in dominio.inspecciones_de(activo["tag"]):
            medido = i.get("espesor_medido_mm")
            if medido is None:
                continue
            if medido < t_min and i["severidad"] != "critico":
                incoherentes.append(
                    f"{i['id_inspeccion']}: {medido} mm bajo t_min {t_min} mm "
                    f"pero severidad '{i['severidad']}'"
                )
    assert not incoherentes, "\n  ".join(["severidades incoherentes:", *incoherentes[:5]])


def test_los_equipos_rotativos_no_reportan_espesor(dominio: Dominio) -> None:
    """Una bomba se sigue por vibraciones, no por espesor de pared.

    Es lo que ejercita que `espesor_medido_mm` sea opcional de verdad y no un
    campo que siempre viene.
    """
    rotativos = {
        a["tag"]
        for a in dominio.activos
        if a["clase"] in ("bomba_centrifuga", "compresor_reciprocante")
    }
    assert rotativos, "los datos no tienen equipos rotativos"

    con_espesor = [
        i["id_inspeccion"]
        for i in dominio.inspecciones
        if i["activo"] in rotativos and i.get("espesor_medido_mm") is not None
    ]
    assert not con_espesor, f"equipos rotativos con medición de espesor: {con_espesor[:5]}"


def test_la_criticidad_de_la_instalacion_es_la_del_peor_activo(dominio: Dominio) -> None:
    """La ontología la declara agregada, así que no puede contradecir a sus activos."""
    esperado = {"A": "alta", "B": "media", "C": "baja"}
    for instalacion in dominio.instalaciones:
        criticidades = [
            a["criticidad"] for a in dominio.activos if a["instalacion"] == instalacion["codigo"]
        ]
        if not criticidades:
            continue
        peor = min(criticidades)  # "A" < "B" < "C"
        assert instalacion["criticidad_instalacion"] == esperado[peor], (
            f"{instalacion['codigo']} declara criticidad "
            f"'{instalacion['criticidad_instalacion']}' y su peor activo es {peor}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# El caso de referencia y la reproducibilidad
# ─────────────────────────────────────────────────────────────────────────────


def test_el_caso_de_referencia_reproduce_los_numeros_del_readme(
    dominio_de_referencia: Dominio,
) -> None:
    """P-2101-A tiene que dar exactamente lo que la documentación publica.

    El README abre con una transcripción que afirma cuatro mediciones entre 2019
    y 2026, una velocidad de corrosión de 0,21 mm/año y una vida remanente de
    −1,4 años. Si los datos dejan de producir esos números, el proyecto publica
    resultados que su propio sistema no genera.
    """
    activo = next(a for a in dominio_de_referencia.activos if a["tag"] == TAG_CRITICO)
    assert activo["espesor_minimo_requerido_mm"] == ESPESOR_MINIMO_CRITICO
    assert activo["clase"] == "canieria_proceso", (
        "el criterio que le aplica la documentación —t_min, vida remanente, "
        "API 570— es el de cañerías en servicio"
    )

    historial = dominio_de_referencia.inspecciones_de(TAG_CRITICO)
    assert len(historial) == 4, f"el README dice 4 mediciones y hay {len(historial)}"
    assert historial[0]["fecha"].startswith("2019")
    assert historial[-1]["fecha"].startswith("2026")
    assert historial[-1]["espesor_medido_mm"] == ESPESOR_MEDIDO_CRITICO
    assert historial[-1]["severidad"] == "critico"

    anios = (
        dt.date.fromisoformat(historial[-1]["fecha"]) - dt.date.fromisoformat(historial[0]["fecha"])
    ).days / 365.25
    velocidad = (historial[0]["espesor_medido_mm"] - historial[-1]["espesor_medido_mm"]) / anios
    assert round(velocidad, 2) == VELOCIDAD_CRITICA, (
        f"el README publica {VELOCIDAD_CRITICA} mm/año y los datos dan {velocidad:.3f}"
    )

    vida_remanente = (ESPESOR_MEDIDO_CRITICO - ESPESOR_MINIMO_CRITICO) / velocidad
    assert vida_remanente < 0, "la vida remanente del caso crítico tiene que ser negativa"
    assert round(vida_remanente, 1) == -1.4, (
        f"el README publica −1,4 años y los datos dan {vida_remanente:.2f}"
    )


def test_la_misma_semilla_produce_los_mismos_datos() -> None:
    """Sin esto, ningún test sobre estos datos sería reproducible.

    Es la garantía de la que dependen todos los demás: si dos corridas con la
    misma semilla difirieran, un test podría pasar hoy y fallar mañana sin que
    haya cambiado una línea de código.
    """
    assert construir(42) == construir(42)
    assert construir(42) != construir(7), (
        "dos semillas distintas produjeron los mismos datos: la semilla no se está usando"
    )
