"""Generador de datos sintéticos del dominio.

    python -m scripts.generar_datos --salida data/generado --semilla 42

Produce un archivo JSON por colección de la ontología, listo para que `seed.py`
lo cargue a Firestore sin transformarlo.

## Por qué un generador y no un volcado fijo

Los datos tienen que cumplir propiedades que un archivo escrito a mano pierde en
cuanto alguien lo edita: que los espesores de un activo decrezcan en el tiempo,
que toda inspección apunte a un activo que existe, que las severidades sean
coherentes con la medición que las produjo. Acá esas propiedades están en el
código que genera, así que se sostienen por construcción.

## El caso P-2101-A

Es el activo que atraviesa toda la documentación del proyecto: mide 6,8 mm
contra un `t_min` de 7,1 mm, así que no está apto para continuar en servicio. Se
reserva y se construye a mano porque de él depende que el recorrido llegue al
gate de aprobación; dejarlo librado al azar significaría que una corrida sin
activos críticos vacíe de sentido a F5.

**Es una cañería de proceso, no una bomba.** El prefijo `P` corresponde acá a
línea de proceso. La ficha de la ontología lo ejemplifica como bomba centrífuga,
pero el criterio que le aplica la documentación —espesor bajo `t_min`, vida
remanente, API 570— es el de cañerías en servicio: un equipo rotativo se sigue
por vibraciones, no por espesor. Se toma la lectura que deja consistente al
resto del proyecto.

## Determinismo

Todo el azar entra por un único `random.Random(semilla)` que se pasa explícito.
No se usa el generador global del módulo `random`, porque cualquier otra
importación que lo toque rompería la reproducibilidad sin dejar rastro.

Ver docs/plan/fases/F0-datos.md § F0.1
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import random
import sys
from pathlib import Path
from typing import Any

from synapseflow.ontology import Ontology, PropertyType, get_ontology

RAIZ = Path(__file__).resolve().parent.parent

# Fecha de corte de la generación. Fija y no `date.today()`: con la fecha real,
# las edades de los activos y las vidas remanentes cambiarían solas cada día y
# los tests de F0.4 dejarían de ser reproducibles.
HOY = dt.date(2026, 8, 6)

SALIDA_POR_DEFECTO = "data/generado"
SEMILLA_POR_DEFECTO = 42

# ── El caso crítico del proyecto ─────────────────────────────────────────────
#
# Estos valores no son arbitrarios: reproducen la transcripción que encabeza el
# README. Con t_min 7,1 mm, última medición 6,8 mm y 0,21 mm/año, la vida
# remanente da −1,43 años, que es el −1,4 que la documentación publica.
#
# Fijarlos es lo que hace que el caso de referencia sea *reproducible* en lugar
# de ilustrativo: cuando F5.6 ejecute el recorrido completo, va a producir los
# números que el README ya afirma. Si se sortearan, el README quedaría
# publicando resultados que el sistema nunca produce.
TAG_CRITICO = "P-2101-A"
ESPESOR_MINIMO_CRITICO = 7.1
ESPESOR_MEDIDO_CRITICO = 6.8
VELOCIDAD_CRITICA = 0.21
# Cuatro campañas entre 2019 y 2026, como dice el historial de la transcripción.
FECHAS_CRITICAS = (
    dt.date(2019, 4, 15),
    dt.date(2021, 5, 20),
    dt.date(2023, 7, 11),
    dt.date(2026, 2, 18),
)


# ─────────────────────────────────────────────────────────────────────────────
# Catálogos del dominio
#
# Los valores de enum se repiten acá y en el YAML a propósito: el generador
# valida su salida contra la ontología antes de escribir, así que una divergencia
# falla ruidosamente en lugar de producir datos que la plataforma rechace más
# tarde, cuando ya nadie recuerde de dónde salieron.
# ─────────────────────────────────────────────────────────────────────────────

# Prefijo de TAG por clase de equipo. `P` es línea de proceso; las bombas van
# con `B`, que es la convención en castellano y evita la colisión con P-2101-A.
PREFIJO_POR_CLASE: dict[str, str] = {
    "bomba_centrifuga": "B",
    "compresor_reciprocante": "K",
    "intercambiador_calor": "E",
    "tanque_almacenamiento": "TK",
    "recipiente_presion": "V",
    "canieria_proceso": "P",
    "valvula_seguridad": "PSV",
    "separador_trifasico": "S",
}

# Solo los equipos estáticos se siguen por espesor de pared. Un rotativo se
# inspecciona por vibraciones y termografía, y su `espesor_medido_mm` queda
# ausente: es el caso que ejercita que el campo sea opcional de verdad.
CLASES_CON_ESPESOR = (
    "canieria_proceso",
    "recipiente_presion",
    "tanque_almacenamiento",
    "separador_trifasico",
    "intercambiador_calor",
)

# Espesor nominal de fábrica, en mm, por clase.
ESPESOR_NOMINAL_POR_CLASE: dict[str, tuple[float, float]] = {
    "canieria_proceso": (6.4, 12.7),
    "recipiente_presion": (12.0, 25.0),
    "tanque_almacenamiento": (6.0, 12.0),
    "separador_trifasico": (12.0, 20.0),
    "intercambiador_calor": (8.0, 16.0),
}

# Velocidad de corrosión en mm/año por fluido de proceso. El agua de producción
# es el fluido más agresivo del conjunto por salinidad; el aire de instrumentos,
# el más benigno. El orden relativo importa más que el valor absoluto: es lo que
# hace que la vida remanente calculada en F2.3 sea creíble.
CORROSION_POR_FLUIDO: dict[str, tuple[float, float]] = {
    "agua_produccion": (0.18, 0.34),
    "crudo": (0.09, 0.21),
    "condensado": (0.06, 0.14),
    "vapor": (0.05, 0.12),
    "gas_natural": (0.03, 0.09),
    "glicol": (0.02, 0.06),
    "aire_instrumentos": (0.01, 0.03),
}

# Fluidos plausibles por clase de equipo.
FLUIDOS_POR_CLASE: dict[str, tuple[str, ...]] = {
    "bomba_centrifuga": ("crudo", "agua_produccion", "condensado"),
    "compresor_reciprocante": ("gas_natural",),
    "intercambiador_calor": ("crudo", "gas_natural", "vapor"),
    "tanque_almacenamiento": ("crudo", "agua_produccion"),
    "recipiente_presion": ("gas_natural", "crudo", "vapor"),
    "canieria_proceso": ("crudo", "gas_natural", "agua_produccion", "condensado"),
    "valvula_seguridad": ("gas_natural", "vapor", "aire_instrumentos"),
    "separador_trifasico": ("crudo", "gas_natural", "agua_produccion"),
}

# Técnica de ensayo según lo que se inspecciona.
TECNICAS_ESTATICO = ("ultrasonido_espesores", "ultrasonido_espesores", "radiografia_industrial")
TECNICAS_ROTATIVO = ("analisis_vibraciones", "termografia", "inspeccion_visual")
TECNICAS_VALVULA = ("inspeccion_visual", "tintas_penetrantes", "particulas_magneticas")

# Clases de equipo que puede alojar cada tipo de instalación.
CLASES_POR_TIPO_INSTALACION: dict[str, tuple[str, ...]] = {
    "bateria": (
        "bomba_centrifuga",
        "separador_trifasico",
        "tanque_almacenamiento",
        "canieria_proceso",
        "valvula_seguridad",
    ),
    "planta_tratamiento_gas": (
        "compresor_reciprocante",
        "recipiente_presion",
        "intercambiador_calor",
        "canieria_proceso",
        "valvula_seguridad",
    ),
    "estacion_bombeo": (
        "bomba_centrifuga",
        "canieria_proceso",
        "recipiente_presion",
        "valvula_seguridad",
    ),
    "cabecera_ducto": ("canieria_proceso", "valvula_seguridad", "recipiente_presion"),
    "planta_deshidratacion": (
        "intercambiador_calor",
        "recipiente_presion",
        "canieria_proceso",
        "bomba_centrifuga",
    ),
}

PREFIJO_POR_TIPO_INSTALACION: dict[str, str] = {
    "bateria": "BAT",
    "planta_tratamiento_gas": "PTG",
    "estacion_bombeo": "EBO",
    "cabecera_ducto": "CDU",
    "planta_deshidratacion": "PDH",
}

# Yacimientos inventados, con sus iniciales para el código de instalación. No
# corresponden a ninguna operación real.
YACIMIENTOS: tuple[tuple[str, str, str], ...] = (
    ("Loma Chelena", "LC", "neuquina"),
    ("Cerro Vidal", "CV", "neuquina"),
    ("Puesto Aguará", "PA", "golfo_san_jorge"),
    ("Meseta Ventura", "MV", "golfo_san_jorge"),
    ("Bajo Ñireco", "BN", "austral"),
    ("Cañada Herrera", "CH", "cuyana"),
    ("Sierra Tolqui", "ST", "noroeste"),
    ("Punta Ledesma", "PL", "austral"),
)

DESCRIPCION_POR_CLASE: dict[str, tuple[str, ...]] = {
    "bomba_centrifuga": (
        "Bomba centrífuga de transferencia",
        "Bomba centrífuga de inyección",
        "Bomba centrífuga booster",
    ),
    "compresor_reciprocante": (
        "Compresor reciprocante de gas de baja",
        "Compresor reciprocante de gas de alta",
    ),
    "intercambiador_calor": (
        "Intercambiador de calor casco y tubos",
        "Enfriador de gas de descarga",
    ),
    "tanque_almacenamiento": (
        "Tanque de almacenamiento de techo fijo",
        "Tanque cortador de agua libre",
    ),
    "recipiente_presion": (
        "Recipiente a presión separador de gas",
        "Acumulador de gas combustible",
    ),
    "canieria_proceso": (
        "Cañería de transferencia de crudo",
        "Cañería de descarga de planta",
        "Colector de producción general",
    ),
    "valvula_seguridad": (
        "Válvula de seguridad de recipiente",
        "Válvula de alivio de línea",
    ),
    "separador_trifasico": (
        "Separador trifásico de producción",
        "Separador trifásico de prueba",
    ),
}

HALLAZGOS_SIN_DESVIO = (
    "Sin desvíos respecto de la campaña anterior.",
    "Medición dentro de los valores esperados para el plan de integridad.",
    "Estado general del equipo acorde a su antigüedad de servicio.",
)
HALLAZGOS_CON_DESVIO = (
    "Pérdida de espesor localizada en la zona de mayor turbulencia.",
    "Adelgazamiento uniforme por encima de la velocidad de corrosión prevista.",
    "Corrosión interna con picado disperso en la generatriz inferior.",
    "Reducción de espesor sostenida en las últimas tres campañas.",
)
HALLAZGOS_ROTATIVO = (
    "Nivel de vibración dentro de los límites de la norma aplicable.",
    "Incremento leve de vibración en el descanso lado acople.",
    "Temperatura de descanso estable respecto de la medición anterior.",
    "Sin evidencia de pérdida por sello mecánico.",
)


# ─────────────────────────────────────────────────────────────────────────────
# Generación
# ─────────────────────────────────────────────────────────────────────────────


def generar_instalaciones(n: int, rng: random.Random) -> list[dict[str, Any]]:
    """Instalaciones del dominio, una por yacimiento.

    La criticidad de la instalación se deriva después, en `generar_activos`, a
    partir de la de sus activos: la ontología la describe como agregada, y
    sortearla por separado produciría una instalación de criticidad baja llena de
    equipos críticos.
    """
    if n > len(YACIMIENTOS):
        raise ValueError(f"solo hay {len(YACIMIENTOS)} yacimientos definidos y se pidieron {n}")

    tipos = list(PREFIJO_POR_TIPO_INSTALACION)
    instalaciones: list[dict[str, Any]] = []

    for i, (nombre, iniciales, cuenca) in enumerate(YACIMIENTOS[:n]):
        tipo = tipos[i % len(tipos)]
        instalaciones.append(
            {
                "codigo": f"{PREFIJO_POR_TIPO_INSTALACION[tipo]}-{iniciales}-{rng.randint(10, 99):03d}",
                "nombre": f"{_nombre_de_instalacion(tipo)} {nombre}",
                "tipo": tipo,
                "cuenca": cuenca,
                "area_clasificada": rng.choice(["zona_0", "zona_1", "zona_1", "zona_2"]),
                # Provisorio: lo recalcula generar_activos.
                "criticidad_instalacion": "media",
            }
        )
    return instalaciones


def _nombre_de_instalacion(tipo: str) -> str:
    return {
        "bateria": "Batería",
        "planta_tratamiento_gas": "Planta de Tratamiento de Gas",
        "estacion_bombeo": "Estación de Bombeo",
        "cabecera_ducto": "Cabecera de Ducto",
        "planta_deshidratacion": "Planta de Deshidratación",
    }[tipo]


def generar_activos(
    instalaciones: list[dict[str, Any]], n: int, rng: random.Random
) -> list[dict[str, Any]]:
    """Activos repartidos entre las instalaciones.

    El primero es siempre `P-2101-A`, construido a mano: es el caso crítico del
    que depende que el recorrido del proyecto llegue al gate de aprobación.
    """
    if not instalaciones:
        raise ValueError("no se puede generar activos sin instalaciones")

    activos: list[dict[str, Any]] = [_activo_critico(instalaciones[0])]
    usados = {TAG_CRITICO}

    for i in range(n - 1):
        instalacion = instalaciones[i % len(instalaciones)]
        clase = rng.choice(CLASES_POR_TIPO_INSTALACION[instalacion["tipo"]])
        tag = _tag_libre(clase, rng, usados)
        usados.add(tag)
        activos.append(_activo(tag, clase, instalacion, rng))

    _asignar_criticidad_de_instalacion(instalaciones, activos)
    return activos


def _activo_critico(instalacion: dict[str, Any]) -> dict[str, Any]:
    """El activo que sostiene el recorrido completo del proyecto.

    Todos sus valores son fijos. Si dependieran de la semilla, cambiar la semilla
    cambiaría el caso de referencia de la documentación.
    """
    return {
        "tag": TAG_CRITICO,
        "descripcion": "Cañería de transferencia de crudo, tren A",
        "clase": "canieria_proceso",
        "instalacion": instalacion["codigo"],
        "criticidad": "A",
        "fluido": "crudo",
        "presion_diseno_kpa": 4800.0,
        "temperatura_diseno_c": 90.0,
        "espesor_nominal_mm": 11.0,
        "espesor_minimo_requerido_mm": ESPESOR_MINIMO_CRITICO,
        "fecha_puesta_servicio": dt.date(2009, 4, 17).isoformat(),
        "estado": "en_servicio",
    }


def _tag_libre(clase: str, rng: random.Random, usados: set[str]) -> str:
    prefijo = PREFIJO_POR_CLASE[clase]
    for _ in range(500):
        tag = f"{prefijo}-{rng.randint(1000, 8999)}-{rng.choice('ABCD')}"
        if tag not in usados:
            return tag
    raise RuntimeError(f"no se consiguió un TAG libre para la clase '{clase}'")


def _activo(
    tag: str, clase: str, instalacion: dict[str, Any], rng: random.Random
) -> dict[str, Any]:
    fluido = rng.choice(FLUIDOS_POR_CLASE[clase])
    puesta = HOY - dt.timedelta(days=rng.randint(6 * 365, 26 * 365))

    activo: dict[str, Any] = {
        "tag": tag,
        "descripcion": f"{rng.choice(DESCRIPCION_POR_CLASE[clase])}, tren {tag[-1]}",
        "clase": clase,
        "instalacion": instalacion["codigo"],
        # Criticidad RBI: la mayoría de los equipos de una operación es B o C.
        # Un dominio donde todo es crítico no permite mostrar el filtrado.
        "criticidad": rng.choices(["A", "B", "C"], weights=[15, 45, 40])[0],
        "fluido": fluido,
        "fecha_puesta_servicio": puesta.isoformat(),
        "estado": rng.choices(
            ["en_servicio", "en_mantenimiento", "fuera_servicio", "baja_definitiva"],
            weights=[82, 9, 6, 3],
        )[0],
    }

    if clase in CLASES_CON_ESPESOR:
        minimo, maximo = ESPESOR_NOMINAL_POR_CLASE[clase]
        nominal = round(rng.uniform(minimo, maximo), 1)
        activo["espesor_nominal_mm"] = nominal
        activo["espesor_minimo_requerido_mm"] = round(nominal * rng.uniform(0.55, 0.70), 1)

    if clase != "valvula_seguridad":
        activo["presion_diseno_kpa"] = round(rng.uniform(600, 9500), -1)
        activo["temperatura_diseno_c"] = round(rng.uniform(45, 180), 0)

    return activo


def _asignar_criticidad_de_instalacion(
    instalaciones: list[dict[str, Any]], activos: list[dict[str, Any]]
) -> None:
    """La criticidad de una instalación es la del peor de sus activos.

    La ontología la declara agregada. Derivarla en lugar de sortearla es lo que
    evita que una instalación 'baja' contenga un equipo de criticidad A, que es
    justo la incoherencia que un revisor del dominio detecta a la primera.
    """
    for instalacion in instalaciones:
        criticidades = [
            a["criticidad"] for a in activos if a["instalacion"] == instalacion["codigo"]
        ]
        if "A" in criticidades:
            instalacion["criticidad_instalacion"] = "alta"
        elif "B" in criticidades:
            instalacion["criticidad_instalacion"] = "media"
        else:
            instalacion["criticidad_instalacion"] = "baja"


def generar_inspecciones(activos: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    """Historial de inspecciones por activo, del más antiguo al más reciente.

    Las mediciones de espesor se construyen **hacia atrás** desde la última: se
    fija el valor final y cada medición anterior se obtiene sumando el desgaste
    del período. Así la serie es monótonamente decreciente por construcción y el
    valor final es exacto, que es lo que necesita el caso P-2101-A. Generarla
    hacia adelante obligaría a corregir el último punto y rompería la monotonía.
    """
    inspecciones: list[dict[str, Any]] = []
    contador = 0

    # Un activo con una sola medición: sin dos puntos no hay velocidad de
    # corrosión, y F2.3 tiene que responder que no puede calcularla en lugar de
    # dividir por cero.
    tag_medicion_unica = _elegir_tag_de_medicion_unica(activos)

    # Unos pocos activos cerca del límite, para que el dominio tenga casos
    # intermedios y no solamente sanos o críticos.
    tags_al_limite = _elegir_tags_al_limite(activos, tag_medicion_unica, rng)

    for activo in activos:
        tag = activo["tag"]
        con_espesor = "espesor_minimo_requerido_mm" in activo

        cantidad = 1 if tag == tag_medicion_unica else rng.randint(4, 8)

        fechas = _fechas_de_campana(activo, cantidad, rng)
        espesores = (
            _serie_de_espesores(activo, fechas, tag in tags_al_limite, rng) if con_espesor else None
        )

        for i, fecha in enumerate(fechas):
            contador += 1
            medido = espesores[i] if espesores is not None else None
            inspecciones.append(
                {
                    "id_inspeccion": f"INS-{fecha.year}-{contador:05d}",
                    "activo": tag,
                    "fecha": fecha.isoformat(),
                    "tecnica": _tecnica(activo, rng),
                    "hallazgo": _hallazgo(activo, medido, rng),
                    "severidad": _severidad(activo, medido, rng),
                    "inspector_legajo": f"LEG-{rng.randint(10000, 99999)}",
                    **({"espesor_medido_mm": medido} if medido is not None else {}),
                }
            )

    return inspecciones


def _elegir_tag_de_medicion_unica(activos: list[dict[str, Any]]) -> str:
    """Primer activo con espesor que no sea el crítico.

    Se elige por posición y no al azar para que no dependa de la semilla: el caso
    de borde tiene que existir siempre.
    """
    for activo in activos:
        if activo["tag"] != TAG_CRITICO and "espesor_minimo_requerido_mm" in activo:
            return str(activo["tag"])
    raise RuntimeError("no hay ningún activo con espesor además del crítico")


def _elegir_tags_al_limite(
    activos: list[dict[str, Any]], excluido: str, rng: random.Random
) -> set[str]:
    candidatos = [
        a["tag"]
        for a in activos
        if a["tag"] not in (TAG_CRITICO, excluido) and "espesor_minimo_requerido_mm" in a
    ]
    return set(rng.sample(candidatos, min(4, len(candidatos))))


def _fechas_de_campana(activo: dict[str, Any], cantidad: int, rng: random.Random) -> list[dt.date]:
    """Fechas de inspección, de la más antigua a la más reciente.

    El intervalo entre campañas sale de la criticidad RBI: API 580 exige mayor
    frecuencia cuanto más crítico es el equipo, y datos que ignoren eso hacen que
    cualquier pregunta sobre frecuencias de inspección conteste cualquier cosa.
    """
    if activo["tag"] == TAG_CRITICO:
        return list(FECHAS_CRITICAS)

    intervalo_meses = {"A": (12, 24), "B": (24, 36), "C": (36, 54)}[activo["criticidad"]]
    ultima = HOY - dt.timedelta(days=rng.randint(30, 400))
    puesta = dt.date.fromisoformat(activo["fecha_puesta_servicio"])

    fechas = [ultima]
    for _ in range(cantidad - 1):
        anterior = fechas[-1] - dt.timedelta(days=rng.randint(*intervalo_meses) * 30)
        if anterior <= puesta:
            break
        fechas.append(anterior)

    return sorted(fechas)


def _serie_de_espesores(
    activo: dict[str, Any], fechas: list[dt.date], al_limite: bool, rng: random.Random
) -> list[float]:
    """Serie decreciente de espesores, construida desde la última medición."""
    t_min = float(activo["espesor_minimo_requerido_mm"])
    nominal = float(activo["espesor_nominal_mm"])

    if activo["tag"] == TAG_CRITICO:
        final = ESPESOR_MEDIDO_CRITICO
    elif al_limite:
        final = round(t_min * rng.uniform(1.02, 1.10), 2)
    else:
        final = round(t_min * rng.uniform(1.25, 1.75), 2)

    # Tiene que quedar margen de corrosión entre el nominal de fábrica y la
    # última medición: sin margen la serie no puede decrecer, y un activo cuyo
    # espesor nunca bajó del nominal no tiene historia de integridad que contar.
    final = min(final, round(nominal * 0.95, 2))

    if activo["tag"] == TAG_CRITICO:
        velocidad = VELOCIDAD_CRITICA
    else:
        minimo, maximo = CORROSION_POR_FLUIDO[activo["fluido"]]
        velocidad = rng.uniform(minimo, maximo)

    # El desgaste acumulado no puede exceder ese margen, o la primera medición
    # daría por encima del espesor de fábrica. Se acota la velocidad en lugar de
    # recortar cada punto contra el nominal: recortar aplana los puntos más
    # antiguos entre sí y rompe la monotonía que F2.3 necesita.
    span_anios = (fechas[-1] - fechas[0]).days / 365.25
    if span_anios > 0:
        velocidad = min(velocidad, (nominal - final) / span_anios)

    espesores = [final]
    for posterior, anterior in zip(fechas[:0:-1], fechas[-2::-1], strict=True):
        anios = (posterior - anterior).days / 365.25
        espesores.append(round(espesores[-1] + velocidad * anios, 2))

    espesores.reverse()

    # Con velocidades bajas y campañas seguidas, dos puntos pueden redondear al
    # mismo valor. Se recorre de la medición más nueva a la más vieja: cada punto
    # se separa de su sucesor, que ya quedó fijo. Hacerlo al revés reintroduce el
    # empate en el punto anterior, que ya había sido revisado.
    for i in range(len(espesores) - 2, -1, -1):
        if espesores[i] <= espesores[i + 1]:
            espesores[i] = round(espesores[i + 1] + 0.01, 2)

    return espesores


def _tecnica(activo: dict[str, Any], rng: random.Random) -> str:
    if activo["clase"] == "valvula_seguridad":
        return rng.choice(TECNICAS_VALVULA)
    if activo["clase"] in CLASES_CON_ESPESOR:
        return rng.choice(TECNICAS_ESTATICO)
    return rng.choice(TECNICAS_ROTATIVO)


def _severidad(activo: dict[str, Any], medido: float | None, rng: random.Random) -> str:
    """Severidad derivada de la medición, no sorteada.

    Un hallazgo `sin_desvio` sobre un espesor por debajo de `t_min` sería un dato
    contradictorio, y el sistema no tendría forma de dar una respuesta correcta a
    partir de él.
    """
    if medido is None:
        return rng.choices(["sin_desvio", "observacion", "desvio_menor"], weights=[60, 30, 10])[0]

    t_min = float(activo["espesor_minimo_requerido_mm"])
    if medido < t_min:
        return "critico"
    if medido < t_min * 1.05:
        return "desvio_mayor"
    if medido < t_min * 1.15:
        return "desvio_menor"
    if medido < t_min * 1.30:
        return "observacion"
    return "sin_desvio"


def _hallazgo(activo: dict[str, Any], medido: float | None, rng: random.Random) -> str:
    if medido is None:
        return rng.choice(HALLAZGOS_ROTATIVO)
    t_min = float(activo["espesor_minimo_requerido_mm"])
    if medido < t_min:
        return (
            f"Espesor medido {medido:.2f} mm por debajo del mínimo requerido "
            f"{t_min:.2f} mm. El componente no está apto para continuar en servicio."
        )
    if medido < t_min * 1.15:
        return rng.choice(HALLAZGOS_CON_DESVIO)
    return rng.choice(HALLAZGOS_SIN_DESVIO)


def generar_ordenes(
    activos: list[dict[str, Any]],
    inspecciones: list[dict[str, Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Órdenes de trabajo, priorizando las que nacen de un hallazgo.

    Toda inspección con desvío mayor o crítico origina una orden: es el vínculo
    hallazgo → acción correctiva que, según el ADR-0003, es lo primero que pide
    un auditor externo. El resto completa hasta el total con trabajo programado.
    """
    por_tag = {a["tag"]: a for a in activos}
    contador = 0
    ordenes: list[dict[str, Any]] = []

    con_desvio = [i for i in inspecciones if i["severidad"] in ("desvio_mayor", "critico")]
    for inspeccion in con_desvio:
        contador += 1
        ordenes.append(
            _orden(
                contador,
                por_tag[inspeccion["activo"]],
                inspeccion,
                "correctivo",
                rng,
            )
        )

    programables = [a for a in activos if a["estado"] != "baja_definitiva"]
    while len(ordenes) < 40 and programables:
        contador += 1
        ordenes.append(
            _orden(
                contador,
                rng.choice(programables),
                None,
                rng.choice(["preventivo", "predictivo", "mejora"]),
                rng,
            )
        )

    return ordenes


def _orden(
    numero: int,
    activo: dict[str, Any],
    inspeccion: dict[str, Any] | None,
    tipo: str,
    rng: random.Random,
) -> dict[str, Any]:
    critica = inspeccion is not None and inspeccion["severidad"] == "critico"

    orden: dict[str, Any] = {
        "id_ot": f"OT-2026-{100000 + numero * 37:06d}",
        "activo": activo["tag"],
        "tipo": tipo,
        "prioridad": (
            "emergencia"
            if critica
            else rng.choices(["alta", "media", "baja"], weights=[25, 45, 30])[0]
        ),
        "descripcion_trabajo": _descripcion_de_trabajo(activo, inspeccion, tipo),
        "estado": (
            "pendiente_aprobacion"
            if critica
            else rng.choices(
                ["borrador", "pendiente_aprobacion", "aprobada", "en_ejecucion", "cerrada"],
                weights=[20, 15, 20, 15, 30],
            )[0]
        ),
        "solicitante": f"LEG-{rng.randint(10000, 99999)}",
    }

    permisos = _permisos_requeridos(activo, tipo)
    if permisos:
        orden["permisos_requeridos"] = permisos
    if inspeccion is not None:
        orden["inspeccion_origen"] = inspeccion["id_inspeccion"]

    return orden


def _descripcion_de_trabajo(
    activo: dict[str, Any], inspeccion: dict[str, Any] | None, tipo: str
) -> str:
    if inspeccion is not None:
        return (
            f"Intervención correctiva sobre {activo['tag']} originada en la "
            f"inspección {inspeccion['id_inspeccion']}: {inspeccion['hallazgo']}"
        )
    return {
        "preventivo": f"Mantenimiento preventivo programado de {activo['tag']} según plan anual.",
        "predictivo": f"Campaña predictiva sobre {activo['tag']} para seguimiento de tendencia.",
        "mejora": f"Mejora de accesibilidad para inspección de {activo['tag']}.",
    }[tipo]


def _permisos_requeridos(activo: dict[str, Any], tipo: str) -> list[str]:
    """Permisos derivados del activo y del trabajo, no sorteados.

    La ontología dice que se derivan del área clasificada y de la clase del
    activo. Sortearlos produciría una orden de apertura de línea sin bloqueo y
    etiquetado, que es exactamente el error que un permiso de trabajo existe para
    impedir.
    """
    permisos: list[str] = []

    if tipo in ("correctivo", "mejora"):
        permisos.append("bloqueo_etiquetado")
    if activo["clase"] in ("canieria_proceso", "intercambiador_calor"):
        permisos.append("apertura_linea_proceso")
    if activo["clase"] in ("tanque_almacenamiento", "separador_trifasico", "recipiente_presion"):
        permisos.append("espacio_confinado")
    if activo["clase"] == "tanque_almacenamiento":
        permisos.append("trabajo_en_altura")

    return sorted(set(permisos))


# ─────────────────────────────────────────────────────────────────────────────
# Validación contra la ontología
#
# El generador comprueba su propia salida antes de escribirla. Es la única
# defensa contra la deriva silenciosa: si mañana alguien agrega un valor de enum
# al YAML y no acá, o quita un campo requerido, el error aparece en esta corrida
# y no cuando el agente consulte un activo y reciba un registro inválido.
# ─────────────────────────────────────────────────────────────────────────────


def validar_contra_ontologia(
    onto: Ontology, registros_por_entidad: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """Devuelve la lista de incoherencias. Vacía significa que los datos sirven."""
    errores: list[str] = []
    claves_por_entidad = {
        eid: {r[onto.entity(eid).key] for r in registros}
        for eid, registros in registros_por_entidad.items()
    }

    for entidad_id, registros in registros_por_entidad.items():
        entidad = onto.entity(entidad_id)
        declaradas = {p.name for p in entidad.properties}

        for registro in registros:
            etiqueta = f"{entidad_id}[{registro.get(entidad.key, '?')}]"

            for sobrante in sorted(set(registro) - declaradas):
                errores.append(f"{etiqueta}: campo '{sobrante}' no está en la ontología")

            for prop in entidad.properties:
                valor = registro.get(prop.name)

                if valor is None:
                    if prop.required:
                        errores.append(f"{etiqueta}: falta el campo requerido '{prop.name}'")
                    continue

                errores.extend(_validar_valor(etiqueta, prop, valor, claves_por_entidad))

    return errores


def _validar_valor(
    etiqueta: str, prop: Any, valor: Any, claves_por_entidad: dict[str, set[Any]]
) -> list[str]:
    errores: list[str] = []
    campo = f"{etiqueta}.{prop.name}"

    match prop.type:
        case PropertyType.ENUM:
            if valor not in (prop.values or []):
                errores.append(f"{campo}: '{valor}' no está entre los valores admitidos")
        case PropertyType.ARRAY:
            if not isinstance(valor, list):
                errores.append(f"{campo}: se esperaba una lista y llegó {type(valor).__name__}")
            elif prop.items is PropertyType.ENUM:
                for item in valor:
                    if item not in (prop.values or []):
                        errores.append(f"{campo}: '{item}' no está entre los valores admitidos")
        case PropertyType.REFERENCE:
            claves = claves_por_entidad.get(prop.target or "", set())
            if valor not in claves:
                errores.append(f"{campo}: apunta a '{valor}', que no existe en '{prop.target}'")
        case PropertyType.DATE:
            try:
                dt.date.fromisoformat(str(valor))
            except ValueError:
                errores.append(f"{campo}: '{valor}' no es una fecha ISO-8601")
        case PropertyType.NUMBER:
            if not isinstance(valor, int | float) or isinstance(valor, bool):
                errores.append(f"{campo}: se esperaba un número y llegó {type(valor).__name__}")
        case PropertyType.INTEGER:
            if not isinstance(valor, int) or isinstance(valor, bool):
                errores.append(f"{campo}: se esperaba un entero y llegó {type(valor).__name__}")
        case PropertyType.BOOLEAN:
            if not isinstance(valor, bool):
                errores.append(f"{campo}: se esperaba un booleano y llegó {type(valor).__name__}")
        case PropertyType.STRING:
            if not isinstance(valor, str):
                errores.append(f"{campo}: se esperaba texto y llegó {type(valor).__name__}")

    return errores


def verificar_invariantes(
    activos: list[dict[str, Any]], inspecciones: list[dict[str, Any]]
) -> list[str]:
    """Las propiedades sin las cuales estos datos no sirven para el proyecto.

    No son sobre el generador: son sobre lo que el resto de las fases necesita
    encontrar en los datos. F0.4 las convierte en tests; acá se comprueban en
    cada corrida para que una semilla distinta no las rompa en silencio.
    """
    problemas: list[str] = []
    por_activo: dict[str, list[dict[str, Any]]] = {}
    for inspeccion in inspecciones:
        por_activo.setdefault(inspeccion["activo"], []).append(inspeccion)

    criticos = [
        a
        for a in activos
        if "espesor_minimo_requerido_mm" in a
        and any(
            i.get("espesor_medido_mm") is not None
            and i["espesor_medido_mm"] < a["espesor_minimo_requerido_mm"]
            for i in por_activo.get(a["tag"], [])
        )
    ]
    if not criticos:
        problemas.append("ningún activo quedó por debajo de su t_min: no se llega al gate")

    if not any(len(v) == 1 for v in por_activo.values()):
        problemas.append("ningún activo tiene una sola medición: falta el caso de borde de F2.3")

    for tag, historial in sorted(por_activo.items()):
        medidos = [
            i["espesor_medido_mm"]
            for i in sorted(historial, key=lambda x: str(x["fecha"]))
            if i.get("espesor_medido_mm") is not None
        ]
        for anterior, posterior in itertools.pairwise(medidos):
            if posterior >= anterior:
                problemas.append(f"{tag}: el espesor no decrece ({anterior} -> {posterior})")
                break

    return problemas


# ─────────────────────────────────────────────────────────────────────────────
# Escritura y línea de comandos
# ─────────────────────────────────────────────────────────────────────────────


def escribir(salida: Path, registros_por_coleccion: dict[str, list[dict[str, Any]]]) -> None:
    """Un JSON por colección, con las claves ordenadas.

    `sort_keys` no es cosmético: sin él, dos corridas con la misma semilla pueden
    diferir en el orden de las claves y el diff deja de servir para comprobar la
    reproducibilidad.
    """
    salida.mkdir(parents=True, exist_ok=True)
    for coleccion, registros in sorted(registros_por_coleccion.items()):
        destino = salida / f"{coleccion}.json"
        destino.write_text(
            json.dumps(registros, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.generar_datos",
        description="Genera los datos sintéticos del dominio de integridad de activos.",
        epilog="Los datos son inventados y no corresponden a ninguna operación real.",
    )
    parser.add_argument("--salida", default=SALIDA_POR_DEFECTO, help="directorio de destino")
    parser.add_argument(
        "--semilla",
        type=int,
        default=SEMILLA_POR_DEFECTO,
        help="semilla del generador; la misma semilla produce exactamente los mismos datos",
    )
    parser.add_argument("--instalaciones", type=int, default=6)
    parser.add_argument("--activos", type=int, default=60)
    parser.add_argument(
        "--solo-validar",
        action="store_true",
        help="genera y valida sin escribir nada a disco",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rng = random.Random(args.semilla)
    onto = get_ontology()

    instalaciones = generar_instalaciones(args.instalaciones, rng)
    activos = generar_activos(instalaciones, args.activos, rng)
    inspecciones = generar_inspecciones(activos, rng)
    ordenes = generar_ordenes(activos, inspecciones, rng)

    por_entidad = {
        "installation": instalaciones,
        "asset": activos,
        "inspection": inspecciones,
        "work_order": ordenes,
    }

    errores = validar_contra_ontologia(onto, por_entidad)
    problemas = verificar_invariantes(activos, inspecciones)

    print(f"Semilla    {args.semilla}")
    print(f"Generado   {len(instalaciones)} instalaciones, {len(activos)} activos,")
    print(f"           {len(inspecciones)} inspecciones, {len(ordenes)} ordenes de trabajo")
    print()

    if errores:
        print(f"La salida NO cumple la ontologia: {len(errores)} problemas", file=sys.stderr)
        for error in errores[:20]:
            print(f"  - {error}", file=sys.stderr)
        if len(errores) > 20:
            print(f"  ... y {len(errores) - 20} mas", file=sys.stderr)
        return 1

    if problemas:
        print(f"Los datos no sirven para el proyecto: {len(problemas)} problemas", file=sys.stderr)
        for problema in problemas:
            print(f"  - {problema}", file=sys.stderr)
        return 1

    print("Validacion contra la ontologia         OK")
    print("Invariantes del dominio                OK")

    critico = next(a for a in activos if a["tag"] == TAG_CRITICO)
    ultima = max(
        (i for i in inspecciones if i["activo"] == TAG_CRITICO),
        key=lambda i: str(i["fecha"]),
    )
    print()
    print(f"Caso critico  {TAG_CRITICO}  t_min {critico['espesor_minimo_requerido_mm']} mm")
    print(
        f"              ultima medicion {ultima['espesor_medido_mm']} mm "
        f"({ultima['fecha']}) severidad {ultima['severidad']}"
    )

    if args.solo_validar:
        print()
        print("--solo-validar: no se escribio nada")
        return 0

    salida = Path(args.salida)
    if not salida.is_absolute():
        salida = RAIZ / salida
    escribir(
        salida,
        {
            "installations": instalaciones,
            "assets": activos,
            "inspections": inspecciones,
            "work_orders": ordenes,
        },
    )
    print()
    print(f"Escrito en {salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
