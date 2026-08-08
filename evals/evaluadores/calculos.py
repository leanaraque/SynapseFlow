"""Exactitud de los números. **Determinístico y con tolerancia declarada.**

El caso trae el valor calculado a mano y el sistema trae el que produjo la
función de Python. Se comparan.

## Por qué se mide sobre `calculos` y no sobre el texto

El número que importa es el que salió de la función determinística, no el que el
modelo transcribió al redactar. Medir sobre el texto convertiría esta métrica en
una de formato: bajaría si el modelo escribe «−1,43» en lugar de «-1.43», y no
subiría si el cálculo empezara a dar mal pero el modelo lo escribiera prolijo.

Es la misma razón por la que el estado del grafo lleva `calculos` aparte del
historial de mensajes.

Ver docs/plan/fases/F8-evals.md § F8.2
"""

from __future__ import annotations

from typing import Any

from evals.evaluadores.base import Caso, RespuestaDelSistema, Resultado

METRICA = "exactitud_del_calculo"


def evaluar(caso: Caso, respuesta: RespuestaDelSistema) -> Resultado | None:
    """Compara el número producido contra el esperado, con su tolerancia.

    Devuelve `None` si el caso no declara un valor esperado: la mayoría no lo
    hace, y aprobarlos inflaría la métrica con casos que no miden un cálculo.
    """
    esperado = caso.valor_esperado
    if not esperado:
        return None

    campo = str(esperado["campo"])
    valor_esperado = esperado["valor"]
    tolerancia = float(esperado.get("tolerancia") or 0)

    if campo not in respuesta.calculos:
        return Resultado.falla(
            caso.id,
            METRICA,
            (
                f"el sistema no produjo '{campo}'. El número tiene que salir de la "
                "función determinística, no del texto del modelo."
            ),
        )

    obtenido = respuesta.calculos[campo]

    if valor_esperado is None:
        # El caso espera que NO se pueda calcular. Es tan importante como los
        # que esperan un número: devolver cero donde no se puede calcular
        # significaría «este equipo no se corroe».
        if obtenido is None:
            return Resultado.aprueba(caso.id, METRICA, f"'{campo}' es None, como correspondía")
        return Resultado.falla(
            caso.id,
            METRICA,
            f"'{campo}' dio {obtenido!r} donde el cálculo no se podía hacer",
        )

    if obtenido is None:
        return Resultado.falla(
            caso.id, METRICA, f"'{campo}' dio None y se esperaba {valor_esperado!r}"
        )

    if isinstance(valor_esperado, bool) or isinstance(obtenido, bool):
        # `bool` es subclase de `int`: sin este caso, `apto=False` se compararía
        # numéricamente contra 0 y pasaría por casualidad.
        if bool(obtenido) == bool(valor_esperado):
            return Resultado.aprueba(caso.id, METRICA, f"'{campo}' = {obtenido}")
        return Resultado.falla(
            caso.id, METRICA, f"'{campo}' dio {obtenido} y se esperaba {valor_esperado}"
        )

    diferencia = abs(float(obtenido) - float(valor_esperado))
    if diferencia <= tolerancia:
        return Resultado.aprueba(
            caso.id, METRICA, f"'{campo}' = {obtenido} (esperado {valor_esperado} ± {tolerancia})"
        )

    return Resultado.falla(
        caso.id,
        METRICA,
        (
            f"'{campo}' dio {obtenido} y se esperaba {valor_esperado} ± {tolerancia} "
            f"(diferencia {diferencia:.4f})"
        ),
    )


def valores_medidos(caso: Caso) -> dict[str, Any]:
    """Qué campo mide un caso, para el reporte. Vacío si no mide ninguno."""
    return dict(caso.valor_esperado or {})
