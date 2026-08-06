"""Las acciones del dominio: lo que el agente puede hacer de verdad.

La ontología **declara** nueve acciones; este paquete las **implementa**. El
vínculo entre una y otra es el decorador `@implements("id_de_la_accion")`, y el
compilador se niega a producir el catálogo si alguna quedó sin implementación:
un YAML que promete una herramienta inexistente es un error de arranque, no un
fallo en runtime.

## Por qué este módulo importa submódulos que no reexporta

`@implements` registra al importarse. Si nadie importa `lecturas`, el registro
queda vacío y `compile_tools` falla diciendo que faltan implementaciones — un
error desconcertante, porque el archivo está ahí y la función existe.

Importarlos acá hace que `import synapseflow.domain` alcance para tener el
catálogo completo. El `noqa: F401` es deliberado: son imports por efecto
secundario.

Ver docs/plan/fases/F2-dominio.md
"""

from synapseflow.domain import calculos, lecturas  # noqa: F401
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
