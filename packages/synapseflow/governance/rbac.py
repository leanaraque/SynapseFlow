"""Quién ejecuta, quién aprueba, y con qué autoridad.

**El agente hereda los permisos del usuario, nunca los de la cuenta de
servicio.** Es la diferencia entre un asistente y un backdoor: un agente que
corre con la identidad del servicio puede hacer todo lo que el servicio puede
hacer, y el usuario que lo invocó se convierte en un canal para ejercer permisos
que no tiene.

El contexto es un **dato**, no un cliente: entra al `artifact` de cada acción y
de ahí al log de auditoría. Por eso es inmutable y serializable, y por eso no
lleva adentro ni el repositorio ni una conexión.

## Las tres barreras, y cuál es la que vale

1. **El filtrado del catálogo.** Una herramienta que el rol no puede ejecutar no
   existe para el modelo. Es la barrera real, porque no hay nada que olvidar.
2. **`exigir_rol_autorizado`** en cada escritura. Redundante a propósito: cubre
   la invocación por fuera del catálogo y sigue en pie si la primera se rompe.
3. **`exigir_autoridad_de_aprobacion`** en el gate. Esta no es redundante: el
   catálogo filtra quién *propone*, no quién *aprueba*, y son roles distintos.

## Por qué el proponente no puede aprobar su propia acción

Es separación de funciones, y en una empresa regulada no es una preferencia: es
lo que hace que la aprobación signifique algo. Un supervisor que propone una
parada y la aprueba él mismo produce exactamente el mismo registro de auditoría
que uno que la aprobó sin leerla.

Ver docs/plan/fases/F4-gobernanza.md § F4.1
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from synapseflow.ontology import Action


class ContextoRequeridoError(RuntimeError):
    """Una acción se invocó sin contexto de ejecución.

    Tipo propio porque no es un error del usuario ni del modelo: es un error de
    cableado de la plataforma. Una escritura sin identidad no se puede auditar,
    así que la acción se niega a correr en lugar de registrar «anónimo».
    """


class AutoridadInsuficienteError(PermissionError):
    """Quien intenta aprobar no tiene autoridad para hacerlo.

    Hereda de `PermissionError` para que un manejador genérico de permisos lo
    trate igual, y es un tipo propio para que la API pueda distinguir «no podés
    ejecutar esto» de «no podés aprobar esto», que son 403 con causas distintas.
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


# ─────────────────────────────────────────────────────────────────────────────
# Autoridad para aprobar
# ─────────────────────────────────────────────────────────────────────────────


def puede_aprobar(
    ctx: ExecutionContext, accion: Action, *, propuesta_por: str | None = None
) -> bool:
    """Si este contexto puede aprobar esta acción.

    Args:
        ctx: quien intenta aprobar.
        accion: la acción propuesta, con sus `approver_roles`.
        propuesta_por: uid de quien la propuso. Si coincide con el aprobador, no
            hay aprobación posible.
    """
    if propuesta_por is not None and propuesta_por == ctx.usuario:
        return False
    return ctx.rol in accion.approver_roles


def exigir_autoridad_de_aprobacion(
    ctx: ExecutionContext, accion: Action, *, propuesta_por: str | None = None
) -> None:
    """Falla si este contexto no puede aprobar esta acción.

    A diferencia de `exigir_rol_autorizado`, **esto no es redundante**: el
    catálogo filtra quién puede *proponer* una acción, no quién puede
    *aprobarla*, y son conjuntos distintos. Un técnico puede proponer una orden
    de trabajo y no puede emitirla; el supervisor es el que aprueba.

    Raises:
        AutoridadInsuficienteError: con el motivo distinguido, porque «no sos
            aprobador» y «no podés aprobar la tuya» se corrigen distinto.
    """
    if propuesta_por is not None and propuesta_por == ctx.usuario:
        raise AutoridadInsuficienteError(
            f"'{ctx.usuario}' propuso '{accion.id}' y no puede aprobarla.\n"
            "  La separación de funciones es lo que hace que la aprobación "
            "signifique algo: aprobar la propia propuesta produce el mismo "
            "registro de auditoría que aprobar sin leer."
        )

    if ctx.rol not in accion.approver_roles:
        aprobadores = sorted(accion.approver_roles) or ["(ninguno declarado)"]
        raise AutoridadInsuficienteError(
            f"el rol '{ctx.rol}' no puede aprobar '{accion.id}'. "
            f"Aprobadores declarados en la ontología: {aprobadores}."
        )


def aprobadores_de(accion: Action) -> tuple[str, ...]:
    """Roles que la ontología habilita a aprobar una acción."""
    return tuple(accion.approver_roles)
