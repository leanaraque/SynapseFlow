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
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.api.aprobaciones import (
    ErrorDeAprobacion,
    PedidoDeDecision,
    decidir,
    pendientes,
    registrar_al_pasar,
)
from services.api.auth import ErrorDeIdentidad, roles_del_dominio, usuario_actual
from services.api.streaming import Evento, eventos, flujo_sse
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

# Orígenes de la consola. **No es `*`**: con credenciales el navegador lo
# rechaza, y aunque no fuera así, una API que acepta cualquier origen deja que
# cualquier página monte una interfaz sobre los datos de tu dominio.
#
# Se listan los dos dominios que Firebase Hosting sirve por defecto y el de
# desarrollo. Un dominio propio se agrega acá.
ORIGENES = (
    "https://synapseflow-5fc52.web.app",
    "https://synapseflow-5fc52.firebaseapp.com",
    "http://localhost:5173",
)

# ## Por qué la consola llama a Cloud Run directo y no por el rewrite
#
# **El rewrite de Firebase Hosting corta a los 60 segundos.** Medido: un
# recorrido completo de P-2101-A tarda ~52 s y devolvió 502 exactamente a los
# 60,29 s cuando se pidió por `synapseflow-5fc52.web.app`, y 200 contra la URL
# de Cloud Run. Una consulta más lenta que el promedio falla siempre.
#
# El rewrite sigue existiendo y sirve para los endpoints cortos, pero el flujo
# de `/api/consultas` no puede depender de él. Por eso la consola acepta una URL
# base: es la excepción a «las rutas son relativas», y está acá para que se lea
# junto con su motivo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ORIGENES),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Thread-Id"],
    # Sin esto el navegador no deja leer el hilo que devuelve la respuesta, y sin
    # el hilo la consola no puede aprobar el gate que ese recorrido abrió.
    expose_headers=["X-Thread-Id"],
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


@app.exception_handler(ErrorDeAprobacion)
async def _aprobacion(_: Any, exc: ErrorDeAprobacion) -> JSONResponse:
    """404, 409 y 400 dicen cosas distintas sobre un gate.

    «No existe», «alguien llegó antes» y «esa decisión no está permitida» se
    resuelven de tres maneras distintas, y devolver un 400 genérico para las tres
    le deja a la consola adivinar cuál fue.
    """
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


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
    grafo = grafo_para(contexto)

    flujo = eventos(
        grafo,
        {"messages": [{"role": "user", "content": consulta.pregunta}]},
        {"configurable": {"thread_id": thread_id}},
    )

    return _respuesta_sse(registrar_al_pasar(contexto, grafo, flujo), thread_id)


# ─────────────────────────────────────────────────────────────────────────────
# Aprobaciones
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/api/aprobaciones")
async def bandeja(ctx: ExecutionContext = Depends(usuario_actual)) -> dict[str, Any]:
    """Los gates que **este** usuario puede resolver.

    No es «todos los pendientes con un aviso»: una bandeja que muestra lo que no
    se puede aprobar enseña a ignorarla.
    """
    return {"pendientes": await pendientes(ctx)}


@app.post("/api/aprobaciones/{thread_id}")
async def decidir_aprobacion(
    thread_id: str,
    pedido: PedidoDeDecision,
    ctx: ExecutionContext = Depends(usuario_actual),
) -> StreamingResponse:
    """Resuelve un gate y deja que el grafo siga donde había quedado.

    La respuesta vuelve a ser un flujo SSE porque aprobar **no es un endpoint que
    devuelve ok**: es el resto del recorrido. El usuario ve ejecutarse la acción
    que aprobó y la respuesta que la cierra, por el mismo canal que ya conoce.

    El contexto se rearma con el hilo de la propuesta, no con el del aprobador:
    la ejecución pertenece a esa conversación, y auditarla contra otra la volvería
    imposible de reconstruir.
    """
    contexto = ctx.model_copy(update={"thread_id": thread_id})
    comando, _ = await decidir(contexto, thread_id, pedido)
    grafo = grafo_para(contexto)

    flujo = eventos(grafo, comando, {"configurable": {"thread_id": thread_id}})

    return _respuesta_sse(registrar_al_pasar(contexto, grafo, flujo), thread_id)


def _respuesta_sse(flujo: AsyncIterator[Evento], thread_id: str) -> StreamingResponse:
    """Un flujo de eventos como respuesta SSE.

    El `thread_id` viaja en una cabecera porque cuando lo genera el servidor la
    consola no lo conoce, y sin él no puede aprobar el gate que ese mismo
    recorrido llega a abrir.
    """
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
