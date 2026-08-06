"""Las acciones del dominio: lo que el agente puede hacer de verdad.

La ontología **declara** nueve acciones; este paquete las **implementa**. El
vínculo entre una y otra es el decorador `@implements("id_de_la_accion")`, y el
compilador se niega a producir el catálogo si alguna quedó sin implementación:
un YAML que promete una herramienta inexistente es un error de arranque, no un
fallo en runtime.

Ver docs/plan/fases/F2-dominio.md
"""

from synapseflow.domain.contexto import (
    ContextoRequeridoError,
    ExecutionContext,
    exigir_contexto,
    exigir_rol_autorizado,
)
from synapseflow.domain.repository import RepositorioDominio

__all__ = [
    "ContextoRequeridoError",
    "ExecutionContext",
    "RepositorioDominio",
    "exigir_contexto",
    "exigir_rol_autorizado",
]
