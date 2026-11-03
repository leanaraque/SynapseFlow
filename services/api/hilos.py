"""Cada hilo de conversación tiene un dueño, y solo él lo continúa.

## El agujero que cierra

`POST /api/consultas` aceptaba cualquier `thread_id` —en el cuerpo o en la
cabecera `X-Thread-Id`— sin preguntar de quién era. Y la bandeja de aprobaciones
le muestra a cada supervisor el hilo de cada propuesta, porque lo necesita para
aprobarla.

Juntas, esas dos cosas permitían saltear la separación de funciones. Un
supervisor tomaba el hilo de un inspector y le pedía al agente una parada. Como
`registrar_pendiente` conserva como proponente a quien abrió el hilo, la
propuesta quedaba a nombre del inspector, y el supervisor la aprobaba: «el
proponente no aprueba lo suyo» no lo frenaba, porque para el sistema el
proponente era otro. Además, cualquiera que conociera un hilo podía hacerle
preguntas al agente sobre una conversación ajena.

## Por qué una colección propia y no el checkpointer

El dueño se tiene que poder consultar con una lectura, antes de armar el grafo.
Derivarlo del primer checkpoint obligaría a reconstruir el estado de LangGraph
para decidir un permiso, y el checkpoint no guarda quién preguntó: guarda qué se
dijo.

## Aprobar no es continuar

`POST /api/aprobaciones/{hilo}` **no pasa por acá**. El supervisor reanuda un
hilo que no es suyo, y es correcto: lo autoriza la ontología, no la propiedad del
hilo. Lo que se cierra es escribirle al agente en una conversación ajena.

## Hilos anteriores a este control

Los hilos que existían antes no tienen registro de dueño, y el primero que los
use los reclama. No hay de dónde reconstruir el dueño original —la bandeja no lo
conserva para los hilos sin gate—, y rechazarlos a todos rompería las
conversaciones en curso. Es una ventana que se cierra sola: un hilo reclamado ya
no se puede volver a reclamar.

Ver docs/05-despliegue.md § El circuito completo
"""

from __future__ import annotations

import contextlib
import datetime as dt
from typing import Any

from fastapi import HTTPException, status
from google.api_core.exceptions import AlreadyExists

from synapseflow.governance import auditoria
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.persistence.client import Collections, get_client


class HiloAjenoError(HTTPException):
    """Se intentó continuar una conversación de otra persona."""


async def reclamar_hilo(ctx: ExecutionContext, thread_id: str, *, cliente: Any = None) -> None:
    """Registra a `ctx.usuario` como dueño del hilo, o verifica que ya lo sea.

    Se usa `create()` y no leer-y-después-escribir: `create()` falla si el
    documento existe, así que dos personas que estrenan el mismo hilo a la vez no
    pueden quedar las dos como dueñas.

    Raises:
        HiloAjenoError: 403 si el hilo es de otra persona. El intento queda en el
            log de auditoría.
    """
    db = cliente or get_client()
    referencia = db.collection(Collections.THREADS).document(thread_id)

    with contextlib.suppress(AlreadyExists):
        await referencia.create(
            {
                "propietario": ctx.usuario,
                "rol": ctx.rol,
                "creado_en": dt.datetime.now(dt.UTC).isoformat(),
            }
        )
        return

    propietario = ((await referencia.get()).to_dict() or {}).get("propietario")
    if propietario == ctx.usuario:
        return

    motivo = f"el hilo '{thread_id}' pertenece a otra persona y solo ella puede continuarlo."
    # Si el registro falla, el 403 sale igual: perder el rastro de un intento es
    # malo, pero dejar pasar el intento por no poder registrarlo sería peor.
    with contextlib.suppress(Exception):
        await auditoria.registrar(
            auditoria.evento_de_acceso_denegado(
                ctx.model_copy(update={"thread_id": thread_id}), None, motivo=motivo
            ),
            cliente=db,
        )

    raise HiloAjenoError(status_code=status.HTTP_403_FORBIDDEN, detail=motivo)
