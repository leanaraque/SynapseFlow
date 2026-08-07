"""Gobernanza: las garantías que atraviesan a todos los agentes.

Identidad y permisos, redacción de datos personales, log de auditoría y política
de proveedores. Es una capa y no código repetido en cada agente, porque una
garantía que hay que acordarse de aplicar no es una garantía.

Ver docs/plan/fases/F4-gobernanza.md
"""

from synapseflow.governance.auditoria import (
    EventoAuditoria,
    TipoEvento,
    evento_de_acceso_denegado,
    evento_de_accion,
    evento_de_aprobacion,
    evento_de_respuesta,
    registrar,
    registrar_lote,
)
from synapseflow.governance.pii import (
    Tokenizador,
    campos_pii,
    contiene_pii,
    detectar_legajos,
)
from synapseflow.governance.politica import (
    PoliticaVioladaError,
    clasificaciones_que_nunca_salen,
    exigir_zero_training,
    puede_salir,
)
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
    "EventoAuditoria",
    "ExecutionContext",
    "PoliticaVioladaError",
    "TipoEvento",
    "Tokenizador",
    "aprobadores_de",
    "campos_pii",
    "clasificaciones_que_nunca_salen",
    "contiene_pii",
    "detectar_legajos",
    "evento_de_acceso_denegado",
    "evento_de_accion",
    "evento_de_aprobacion",
    "evento_de_respuesta",
    "exigir_autoridad_de_aprobacion",
    "exigir_contexto",
    "exigir_rol_autorizado",
    "exigir_zero_training",
    "puede_aprobar",
    "puede_salir",
    "registrar",
    "registrar_lote",
]
