"""La API de SynapseFlow. FastAPI sobre Cloud Run.

## Qué hace y qué no

Expone el grafo por HTTP con identidad, streaming y los endpoints de aprobación.
**No reimplementa ninguna garantía**: los permisos salen de la ontología, los
gates del middleware y la redacción de PII del gateway. Si esta capa tuviera su
propia lógica de permisos habría dos, y dos reglas que dicen lo mismo son dos
reglas que se desincronizan.

## El grafo se construye por request; el gateway y el checkpointer no

Parece un desperdicio y no lo es: el grafo depende del **rol del usuario**, y
cachearlo por proceso serviría el catálogo de un rol a otro. Compilar un
`StateGraph` cuesta memoria, no red.

Lo que sí se comparte por proceso es el gateway —abre clientes HTTP y no depende
de quién pregunta— y el checkpointer, que mantiene el pool de gRPC de Firestore.

## Los errores de identidad se traducen acá

`ErrorDeIdentidad` es una `HTTPException`, así que FastAPI ya la manejaría. El
manejador propio existe para que el cuerpo tenga la forma que espera la consola
—`{"error": ...}`— y para que `PermissionError`, que viene de la gobernanza y no
sabe nada de HTTP, salga como 403 y no como 500.

Ver docs/plan/fases/F6-api.md
"""

from __future__ import annotations

import functools
import uuid
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.api.auth import ErrorDeIdentidad, roles_del_dominio, usuario_actual
from services.api.streaming import eventos, flujo_sse
from synapseflow.agents.graph import construir_grafo
from synapseflow.governance.pii import Tokenizador
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import compile_tools, get_ontology
from synapseflow.persistence.checkpointer import FirestoreSaver

# Importar el paquete dispara los `@implements` de las nueve acciones. Sin esto
# la API arranca y el catálogo de cualquier rol falla al compilarse.
import synapseflow.domain  # noqa: F401  isort: skip

app = FastAPI(
    title="SynapseFlow",
    description="Plataforma de agentes gobernados para industrias reguladas.",
    version="0.1.0",
)


class Consulta(BaseModel):
    """Lo que manda la consola para preguntar algo."""

    pregunta: str = Field(min_length=1, max_length=4000)
    # Ausente, se abre un hilo nuevo. Presente, se continúa uno: es lo que
    # permite que una conversación sobreviva a la recarga de la página.
    thread_id: str | None = None


@functools.lru_cache(maxsize=1)
def gateway() -> Gateway:
    """Gateway compartido por proceso. Abre clientes HTTP y no depende del rol."""
    return Gateway()


@functools.lru_cache(maxsize=1)
def checkpointer() -> FirestoreSaver:
    """Checkpointer compartido. Mantiene el pool de gRPC de Firestore.

    Sin él, un gate no sobrevive a la muerte del proceso y el human-in-the-loop
    deja de ser asincrónico — que es la promesa que sostiene todo lo demás.
    """
    return FirestoreSaver()


def grafo_para(ctx: ExecutionContext) -> Any:
    """El grafo compilado para este usuario, con su rol y su tokenizador.

    El tokenizador es **uno por conversación**: compartirlo entre hilos
    correlacionaría a la misma persona entre conversaciones distintas, que es
    justo lo que el diseño de `governance.pii` evita.
    """
    return construir_grafo(
        get_ontology(),
        ctx,
        gateway=gateway(),
        tokenizador=Tokenizador(),
        checkpointer=checkpointer(),
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


# ─────────────────────────────────────────────────────────────────────────────
# Consultas
# ─────────────────────────────────────────────────────────────────────────────


@app.post("/api/consultas")
async def consultar(
    consulta: Consulta, ctx: ExecutionContext = Depends(usuario_actual)
) -> StreamingResponse:
    """Una pregunta, respondida en streaming.

    El `thread_id` viaja también en una cabecera porque cuando lo genera el
    servidor la consola no lo conoce, y sin él no puede aprobar el gate que este
    mismo recorrido puede llegar a abrir.

    El contexto se rearma con el hilo definitivo: el `thread_id` es lo que
    correlaciona cada acción con el razonamiento que la produjo, y una acción
    auditada contra un hilo que no es el suyo no es auditable.
    """
    thread_id = consulta.thread_id or ctx.thread_id or str(uuid.uuid4())
    contexto = ctx.model_copy(update={"thread_id": thread_id})

    flujo = eventos(
        grafo_para(contexto),
        {"messages": [{"role": "user", "content": consulta.pregunta}]},
        {"configurable": {"thread_id": thread_id}},
    )

    return StreamingResponse(
        flujo_sse(flujo),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Sin esto, nginx junta la respuesta en un buffer y la entrega al
            # final: el streaming queda técnicamente correcto y prácticamente
            # inexistente.
            "X-Accel-Buffering": "no",
            "X-Thread-Id": thread_id,
        },
    )
