"""La API de SynapseFlow. FastAPI sobre Cloud Run.

## Qué hace y qué no

Expone el grafo por HTTP con identidad, streaming y los endpoints de aprobación.
**No reimplementa ninguna garantía**: los permisos salen de la ontología, los
gates del middleware y la redacción de PII del gateway. Si esta capa tuviera su
propia lógica de permisos habría dos, y dos reglas que dicen lo mismo son dos
reglas que se desincronizan.

Este commit trae la identidad. El streaming (F6.2), los endpoints de aprobación
(F6.3) y la imagen (F6.4) se agregan sobre esta misma app.

## Los errores de identidad se traducen acá

`ErrorDeIdentidad` es una `HTTPException`, así que FastAPI ya la manejaría. El
manejador propio existe para que el cuerpo tenga la forma que espera la consola
—`{"error": ...}`— y para que `PermissionError`, que viene de la gobernanza y no
sabe nada de HTTP, salga como 403 y no como 500.

Ver docs/plan/fases/F6-api.md
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

from services.api.auth import ErrorDeIdentidad, roles_del_dominio, usuario_actual
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.ontology import compile_tools, get_ontology

# Importar el paquete dispara los `@implements` de las nueve acciones. Sin esto
# la API arranca y el catálogo de cualquier rol falla al compilarse.
import synapseflow.domain  # noqa: F401  isort: skip

app = FastAPI(
    title="SynapseFlow",
    description="Plataforma de agentes gobernados para industrias reguladas.",
    version="0.1.0",
)


# ─────────────────────────────────────────────────────────────────────────────
# Errores
# ─────────────────────────────────────────────────────────────────────────────


@app.exception_handler(ErrorDeIdentidad)
async def _identidad(_: Any, exc: ErrorDeIdentidad) -> JSONResponse:
    """401 y 403 se resuelven distinto y por eso se distinguen.

    Uno se arregla volviendo a autenticar; el otro, pidiéndole a alguien que
    configure tus permisos. Devolver 403 para los dos deja al usuario probando
    de nuevo con el mismo token.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
        headers=exc.headers or {},
    )


@app.exception_handler(PermissionError)
async def _permisos(_: Any, exc: PermissionError) -> JSONResponse:
    """Las negativas de la gobernanza son 403, no 500.

    `AutoridadInsuficienteError` hereda de `PermissionError` justamente para
    esto: la capa que decide no sabe nada de HTTP, y la que sabe de HTTP no
    decide.
    """
    return JSONResponse(status_code=403, content={"error": str(exc)})


# ─────────────────────────────────────────────────────────────────────────────
# Diagnóstico
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict[str, Any]:
    """Sonda de arranque de Cloud Run.

    No toca Firestore ni al proveedor a propósito: una sonda que depende de un
    servicio externo reporta caído al servicio propio cuando el que falla es el
    otro, y Cloud Run reinicia contenedores sanos.
    """
    return {"estado": "ok", "version": app.version}


@app.get("/api/yo")
async def yo(ctx: ExecutionContext = Depends(usuario_actual)) -> dict[str, Any]:
    """Quién soy y qué puedo hacer, según la ontología.

    Lo consume la consola para saber qué mostrar. El catálogo sale del mismo
    `compile_tools` que ve el agente: si divergieran, la consola ofrecería
    acciones que el modelo no tiene —o peor, escondería acciones que el usuario
    sí puede pedir.
    """
    herramientas = compile_tools(get_ontology(), ctx.rol, context=ctx)

    return {
        "usuario": ctx.usuario,
        "nombre": ctx.nombre,
        "rol": ctx.rol,
        "acciones": [
            {
                "nombre": h.name,
                "efecto": (h.metadata or {}).get("effect"),
                "requiere_aprobacion": (h.metadata or {}).get("requires_approval"),
            }
            for h in herramientas
        ],
    }


@app.get("/api/roles")
async def roles() -> dict[str, Any]:
    """Roles del dominio. Sin identidad: es información del YAML, no de nadie."""
    return {"roles": list(roles_del_dominio())}
