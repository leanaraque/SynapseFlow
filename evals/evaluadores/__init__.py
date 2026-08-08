"""Los cuatro evaluadores de la suite.

**Tres son determinísticos y uno usa un juez.** No es una casualidad: un juez que
es otro modelo introduce su propia varianza, y entonces una eval que empeora
tiene dos explicaciones posibles —el sistema empeoró o el juez tuvo un mal día—.
Con dos explicaciones, la métrica deja de servir para decidir.

Para citas, rechazo y números no hace falta juzgar: se comprueban. La fidelidad
es el único que no tiene alternativa, porque «esta afirmación está respaldada por
este fragmento» es un juicio sobre lenguaje natural.

Ver docs/plan/fases/F8-evals.md § F8.2
"""

from evals.evaluadores.base import Caso, Fragmento, RespuestaDelSistema, Resultado

__all__ = ["Caso", "Fragmento", "RespuestaDelSistema", "Resultado"]
