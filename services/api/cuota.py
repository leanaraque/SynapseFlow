"""Cuántos recorridos del grafo puede pedir una persona por hora.

## Qué riesgo acota

Sin rol no se llega al modelo: eso lo resuelve `auth.py`. Pero una cuenta **con**
rol no tenía techo. Si su contraseña se filtra, o alguien automatiza consultas,
cada recorrido son unas diez llamadas a Gemini facturadas, y lo único que había
era un tope de llamadas por turno. La alerta de presupuesto avisa; no corta.

## Por qué en Firestore y no en memoria

Cloud Run corre hasta tres instancias, y cada una tendría su propio contador: el
tope real sería el triple del configurado, y cambiaría con la carga. Un
documento por persona y por hora es la cuenta compartida más simple que hay.

## Por qué con precondición

Leer el contador, comparar y escribir el siguiente son tres pasos. Sin
precondición, dos consultas simultáneas leen el mismo valor y escriben el mismo
siguiente: las dos pasan y el contador sube uno. Con la precondición sobre la
versión leída, la que pierde reintenta con el valor nuevo. Es el mismo mecanismo
que impide aprobar dos veces una propuesta (ver aprobaciones.py).

## Por qué también se reintenta `Aborted`

Firestore puede abortar una escritura con contención sobre el mismo documento
sin aplicarla: el emulador lo hace como «Transaction lock timeout» y el servicio
real lo documenta como ABORTED. No es un error de la persona: es la misma
carrera que la precondición, y se reintenta igual. Sin esto, una ráfaga salía
como un 500 en lugar de contarse o de rechazarse con 429. Entre reintentos se
espera un poco, al azar, para que las que chocaron no vuelvan a chocar juntas.

## Qué cuenta

Las consultas y las aprobaciones: las dos corren el grafo y llaman al modelo. Se
cuenta antes de armar el grafo, así que una consulta rechazada no gasta nada.

## Ventana fija, no deslizante

La hora calendario en UTC. Una ventana deslizante sería más justa en el borde
—quien consulta a las 10:59 y a las 11:01 tiene dos cupos seguidos— y costaría
guardar cada consulta en lugar de un contador. Para acotar el gasto alcanza.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
from typing import Any

from fastapi import HTTPException, status
from google.api_core.exceptions import Aborted, AlreadyExists, FailedPrecondition

from synapseflow.config import get_settings
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.persistence.client import Collections, get_client

# Reintentos ante una escritura concurrente. Con un humano detrás de cada cuenta
# casi nunca hace falta el segundo; si se agotan, es que hay una ráfaga
# automatizada, y rechazarla es correcto.
REINTENTOS = 5

# Espera base entre reintentos, en segundos. Crece al doble en cada uno y se
# toma al azar entre cero y ese valor.
ESPERA_BASE = 0.02


class CuotaAgotadaError(HTTPException):
    """La persona llegó al tope de recorridos de esta hora."""


def _ventana(ahora: dt.datetime) -> str:
    return ahora.astimezone(dt.UTC).strftime("%Y%m%d%H")


def _segundos_hasta_la_proxima_hora(ahora: dt.datetime) -> int:
    utc = ahora.astimezone(dt.UTC)
    proxima = utc.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    return max(1, int((proxima - utc).total_seconds()))


async def consumir_cuota(
    ctx: ExecutionContext,
    *,
    cliente: Any = None,
    limite: int | None = None,
    ahora: dt.datetime | None = None,
) -> int:
    """Cuenta un recorrido de `ctx.usuario` y devuelve cuántos lleva en la hora.

    Raises:
        CuotaAgotadaError: 429, con `Retry-After` hasta el comienzo de la hora
            siguiente, si ya usó todos los de esta.
    """
    db = cliente or get_client()
    tope = limite if limite is not None else get_settings().consultas_por_hora
    momento = ahora or dt.datetime.now(dt.UTC)
    referencia = db.collection(Collections.QUOTAS).document(f"{ctx.usuario}_{_ventana(momento)}")

    for intento in range(REINTENTOS):
        if intento:
            # Separar los reintentos al azar, no es criptografía.
            await asyncio.sleep(random.uniform(0, ESPERA_BASE * 2**intento))  # noqa: S311
        try:
            return await _intentar(db, referencia, ctx, tope, momento)
        except (AlreadyExists, FailedPrecondition, Aborted):
            # Otra consulta de la misma persona escribió a la vez: se relee.
            continue

    raise CuotaAgotadaError(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="demasiadas consultas simultáneas desde la misma cuenta. Probá de nuevo.",
        headers={"Retry-After": "5"},
    )


async def _intentar(
    db: Any, referencia: Any, ctx: ExecutionContext, tope: int, momento: dt.datetime
) -> int:
    """Un intento de leer, comparar y escribir.

    Raises:
        AlreadyExists, FailedPrecondition, Aborted: otra escritura ganó; el que
            llama reintenta con el valor nuevo.
        CuotaAgotadaError: la persona ya usó todo el cupo de esta hora.
    """
    instantanea = await referencia.get()

    if not instantanea.exists:
        # `create()` falla si otra consulta de la misma persona estrenó la hora
        # a la vez.
        await referencia.create(
            {
                "usuario": ctx.usuario,
                "ventana": _ventana(momento),
                "usados": 1,
                "limite": tope,
            }
        )
        return 1

    usados = int((instantanea.to_dict() or {}).get("usados") or 0)
    if usados >= tope:
        raise CuotaAgotadaError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"llegaste al tope de {tope} consultas por hora. Cada consulta "
                "llama varias veces al modelo, y el tope existe para acotar el "
                "gasto. Se libera al comenzar la hora siguiente."
            ),
            headers={"Retry-After": str(_segundos_hasta_la_proxima_hora(momento))},
        )

    await referencia.update(
        {"usados": usados + 1},
        option=db.write_option(last_update_time=instantanea.update_time),
    )
    return usados + 1
