"""Quién ejecuta una acción, y con qué autoridad.

Toda implementación de acción recibe un `ExecutionContext` como argumento
keyword-only. No es un detalle de plomería: es la respuesta a la primera
pregunta que hace un auditor —«¿bajo la autoridad de quién se ejecutó esto?»— y
la razón por la que el agente **hereda los permisos del usuario y no los de la
cuenta de servicio**.

El contexto es un dato, no un cliente: entra al `artifact` de cada acción y de
ahí al log de auditoría. Por eso es inmutable y serializable, y por eso no lleva
adentro ni el repositorio ni una conexión.

F4 agrega sobre esto la redacción de PII y el log inmutable
(`governance/rbac.py`). Acá vive lo mínimo que F2 necesita para que una escritura
pueda decir quién la pidió.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContextoRequeridoError(RuntimeError):
    """Una acción se invocó sin contexto de ejecución.

    Tipo propio porque no es un error del usuario ni del modelo: es un error de
    cableado de la plataforma. Una escritura sin identidad no se puede auditar,
    así que la acción se niega a correr en lugar de registrar «anónimo».
    """


class ExecutionContext(BaseModel):
    """Identidad y alcance de quien invoca una acción."""

    model_config = ConfigDict(frozen=True)

    usuario: str = Field(min_length=1, description="uid de Firebase Auth")
    rol: str = Field(min_length=1, description="rol declarado en la ontología")
    # Hilo de conversación. Es lo que correlaciona una acción con el
    # razonamiento que la produjo: sin esto, el log dice qué pasó pero no por qué.
    thread_id: str | None = None
    nombre: str | None = None

    def registro(self) -> dict[str, Any]:
        """Lo que se guarda en el artifact de cada acción que ejecuta."""
        return {
            "usuario": self.usuario,
            "rol": self.rol,
            "thread_id": self.thread_id,
            "momento": dt.datetime.now(dt.UTC).isoformat(),
        }


def exigir_contexto(ctx: ExecutionContext | None, accion: str) -> ExecutionContext:
    """Devuelve el contexto o falla nombrando la acción que lo necesitaba.

    Se llama al principio de cada implementación que escribe. Devolver el
    contexto —en lugar de solo validarlo— permite escribir
    `ctx = exigir_contexto(ctx, "emitir_orden_trabajo")` y que el tipo quede
    estrechado a no-opcional para el resto de la función.
    """
    if ctx is None:
        raise ContextoRequeridoError(
            f"'{accion}' se invocó sin contexto de ejecución.\n"
            "  Una acción que escribe tiene que poder decir quién la pidió: sin "
            "eso no hay auditoría posible.\n"
            "  El contexto lo inyecta compile_tools(ontology, rol, context=...)."
        )
    return ctx


def exigir_rol_autorizado(ctx: ExecutionContext, accion: str, roles: list[str]) -> None:
    """Falla si el rol del contexto no está entre los autorizados.

    **Esta no es la barrera principal.** La barrera es que el catálogo se filtra
    por rol antes de dárselo al modelo, así que una herramienta que el rol no
    puede ejecutar no llega a existir para él. Esto es defensa en profundidad,
    para el caso de que alguien invoque la implementación por fuera del catálogo
    —un script, un test, una futura API interna—.

    Que sea redundante es el punto. Si algún día el filtrado del catálogo se
    rompe, esto sigue en pie.
    """
    if ctx.rol not in roles:
        raise PermissionError(
            f"el rol '{ctx.rol}' no está autorizado a ejecutar '{accion}'. "
            f"Autorizados: {sorted(roles)}."
        )
