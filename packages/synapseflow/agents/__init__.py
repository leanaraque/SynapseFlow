"""El grafo de agentes: supervisor, especialistas, verificador y gates.

Es la fase que junta todo lo anterior. Acá el sistema deja de ser piezas sueltas
y hace el recorrido completo: pregunta sobre un activo → cálculo determinístico →
fundamento normativo con citas → propuesta de acción irreversible → freno
esperando a un humano.

Ver docs/plan/fases/F5-grafo.md
"""

from synapseflow.agents.state import (
    MAX_REINTENTOS_DE_FUNDAMENTO,
    AgentState,
    estado_inicial,
    puede_reintentar,
)

__all__ = [
    "MAX_REINTENTOS_DE_FUNDAMENTO",
    "AgentState",
    "estado_inicial",
    "puede_reintentar",
]
