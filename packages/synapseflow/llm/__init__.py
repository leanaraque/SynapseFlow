"""Gateway de modelos.

En construcción. Hoy resuelve *perfil de tarea + proveedor → modelo concreto* y
calcula el costo de una llamada; falta el gateway que instancia los modelos
(F1.3) y la contabilidad que persiste el consumo (F1.4).

La decisión de diseño —por qué el código pide un perfil y nunca un nombre de
modelo, y por qué el gateway es el único punto donde el texto cruza el perímetro
de datos— está en docs/adr/0004-gateway-provider-agnostic.md
"""

from synapseflow.llm.registry import (
    PERFILES,
    ModelSpec,
    RegistryError,
    costo_de,
    declara_zero_training,
    dimensiones_del_indice,
    resolver,
)

__all__ = [
    "PERFILES",
    "ModelSpec",
    "RegistryError",
    "costo_de",
    "declara_zero_training",
    "dimensiones_del_indice",
    "resolver",
]
