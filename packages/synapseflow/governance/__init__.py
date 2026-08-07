"""Gobernanza: las garantías que atraviesan a todos los agentes.

Identidad y permisos, redacción de datos personales, log de auditoría y política
de proveedores. Es una capa y no código repetido en cada agente, porque una
garantía que hay que acordarse de aplicar no es una garantía.

Ver docs/plan/fases/F4-gobernanza.md
"""

from synapseflow.governance.rbac import (
    AutoridadInsuficienteError,
    ContextoRequeridoError,
    ExecutionContext,
    aprobadores_de,
    exigir_autoridad_de_aprobacion,
    exigir_contexto,
    exigir_rol_autorizado,
    puede_aprobar,
)

__all__ = [
    "AutoridadInsuficienteError",
    "ContextoRequeridoError",
    "ExecutionContext",
    "aprobadores_de",
    "exigir_autoridad_de_aprobacion",
    "exigir_contexto",
    "exigir_rol_autorizado",
    "puede_aprobar",
]
