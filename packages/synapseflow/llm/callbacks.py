"""Contabilidad de tokens y costo de cada llamada al modelo.

El costo por consulta es una métrica de la plataforma, no una estimación de la
factura: se calcula por llamada con los precios del catálogo, y se correlaciona
por `thread_id` para poder responder «cuánto costó esta conversación» y por
perfil para responder «qué tarea se lleva el presupuesto». Ver
docs/adr/0004-gateway-provider-agnostic.md § Contabilidad

## Por qué la correlación es por `run_id`

`on_chat_model_start` recibe `metadata` —de donde salen el `thread_id` y el
perfil— y `on_llm_end` **no lo recibe**: solo `run_id`. Verificado contra
langchain-core 1.5.2. Así que lo que se sabe al empezar se guarda indexado por
`run_id` y se recupera al terminar. No es un rodeo: es la única forma de que una
fila tenga a la vez el consumo y la tarea que lo causó.

## Por qué el precio sale del modelo y no del perfil

Con un respaldo configurado, el perfil `synthesis` puede terminar ejecutándose
en el modelo de otro proveedor. Cobrarle el precio del primero produciría un
panel de costos que miente justo cuando algo salió mal. El nombre del modelo que
se ejecutó de verdad llega en `invocation_params`, y el precio se busca por ese
nombre.

## Por qué acumula en memoria y vuelca aparte

Un grafo con supervisor y tres especialistas hace del orden de diez llamadas por
turno. Escribir un documento por llamada, en el camino de la respuesta, agrega
latencia a lo que el usuario está esperando y multiplica por diez las escrituras
facturadas. Se acumula y se vuelca en lote al cerrar el turno.

Ver docs/plan/fases/F1-gateway.md § F1.4
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from pydantic import BaseModel, ConfigDict, Field

from synapseflow.llm import registry
from synapseflow.persistence.client import Collections, get_client

# Claves de `metadata` que la plataforma pone en el config de invocación. El
# prefijo evita chocar con las que agrega LangChain, que usa `ls_`.
CLAVE_PERFIL = "synapseflow_perfil"
CLAVE_THREAD = "thread_id"

# Firestore admite hasta 500 operaciones por batch.
TOPE_POR_LOTE = 400


class Consumo(BaseModel):
    """Una llamada al modelo, ya contabilizada.

    Es lo que se escribe en `llm_usage`, un documento por llamada.
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    run_id: str
    parent_run_id: str | None = None
    thread_id: str | None = None

    perfil: str
    modelo: str
    proveedor: str

    tokens_entrada: int = Field(ge=0)
    tokens_salida: int = Field(ge=0)
    costo_usd: float = Field(ge=0)
    latencia_ms: int = Field(ge=0)
    momento: dt.datetime

    # El modelo que se ejecutó no está en el catálogo, así que el costo es cero
    # por ignorancia y no por gratuidad. Se marca para que el panel lo muestre
    # como un hueco en lugar de sumarlo como si fuera gratis.
    modelo_no_catalogado: bool = False


class ContabilidadDeCosto(AsyncCallbackHandler):
    """Registra tokens, modelo, perfil, latencia y costo de cada llamada.

    Se pasa en el `config` de la invocación, o se registra en el gateway para
    toda la sesión:

        contabilidad = ContabilidadDeCosto(thread_id="hilo-42")
        await agente.ainvoke(
            entrada,
            config={
                "callbacks": [contabilidad],
                "metadata": {"synapseflow_perfil": "synthesis"},
            },
        )
        await contabilidad.volcar()

    **No hace I/O durante la ejecución.** Acumula en memoria y escribe cuando se
    lo pide `volcar()`.
    """

    # `raise_error = False` es el default de LangChain y acá se respeta a
    # propósito: si la contabilidad falla, la respuesta al usuario tiene que
    # salir igual. Perder una fila del panel de costos es preferible a tirar
    # abajo una conversación por un problema de instrumentación.

    def __init__(self, *, thread_id: str | None = None, perfil: str | None = None) -> None:
        """
        Args:
            thread_id: hilo con el que correlacionar, si no viene en la metadata
                de cada invocación.
            perfil: perfil por defecto, para call sites que hacen una sola clase
                de llamada y no quieren declararlo en cada una.
        """
        self.thread_id = thread_id
        self.perfil_por_defecto = perfil
        self.consumos: list[Consumo] = []
        # Lo que se sabe al empezar una llamada, indexado por run_id, esperando
        # a que termine. Ver el docstring del módulo.
        self._en_vuelo: dict[UUID, _EnVuelo] = {}

    # ── Hooks ────────────────────────────────────────────────────────────────

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        md = metadata or {}
        invocacion = kwargs.get("invocation_params") or {}

        self._en_vuelo[run_id] = _EnVuelo(
            comenzo=time.perf_counter(),
            momento=dt.datetime.now(dt.UTC),
            parent_run_id=str(parent_run_id) if parent_run_id else None,
            thread_id=md.get(CLAVE_THREAD) or self.thread_id,
            perfil=md.get(CLAVE_PERFIL) or self.perfil_por_defecto or "desconocido",
            # `ls_model_name` lo pone LangChain; `model` viene de los
            # `_identifying_params` del adapter. Se prueban los dos porque no
            # todos los adapters publican ambos.
            modelo=str(md.get("ls_model_name") or invocacion.get("model") or "desconocido"),
        )

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        inicio = self._en_vuelo.pop(run_id, None)
        if inicio is None:
            # Una llamada que terminó sin haber empezado no es contabilizable, y
            # tampoco es un error del sistema: pasa si el handler se enganchó a
            # mitad de una ejecución. Se ignora en lugar de inventar una fila.
            return

        entrada, salida = _tokens_de(response)
        spec = registry.spec_por_modelo(inicio.modelo)

        self.consumos.append(
            Consumo(
                run_id=str(run_id),
                parent_run_id=inicio.parent_run_id,
                thread_id=inicio.thread_id,
                perfil=inicio.perfil,
                modelo=inicio.modelo,
                proveedor=spec.proveedor if spec else "desconocido",
                tokens_entrada=entrada,
                tokens_salida=salida,
                costo_usd=registry.costo_de(spec, entrada, salida) if spec else 0.0,
                latencia_ms=int((time.perf_counter() - inicio.comenzo) * 1000),
                momento=inicio.momento,
                modelo_no_catalogado=spec is None,
            )
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """Descarta la llamada que falló.

        Sin esto, `_en_vuelo` crece sin límite en un proceso de larga vida cada
        vez que un proveedor devuelve un error, que no es un caso raro.
        """
        self._en_vuelo.pop(run_id, None)

    # ── Consulta ─────────────────────────────────────────────────────────────

    @property
    def total_usd(self) -> float:
        return sum(c.costo_usd for c in self.consumos)

    @property
    def tokens_totales(self) -> int:
        return sum(c.tokens_entrada + c.tokens_salida for c in self.consumos)

    def por_perfil(self) -> dict[str, float]:
        """Costo agrupado por perfil de tarea.

        Es la pregunta que se le hace al panel: no «cuánto gastamos» sino «qué
        tarea se lleva el presupuesto», que es lo que permite decidir si conviene
        mover un perfil a un modelo más barato.
        """
        acumulado: dict[str, float] = {}
        for consumo in self.consumos:
            acumulado[consumo.perfil] = acumulado.get(consumo.perfil, 0.0) + consumo.costo_usd
        return acumulado

    # ── Persistencia ─────────────────────────────────────────────────────────

    async def volcar(self, *, limpiar: bool = True) -> int:
        """Escribe lo acumulado en `llm_usage` y devuelve cuántas filas escribió.

        Args:
            limpiar: vaciar el acumulador después de escribir. En falso, un
                segundo volcado duplicaría las filas.
        """
        if not self.consumos:
            return 0

        cliente = get_client()
        coleccion = cliente.collection(Collections.USAGE)
        escritos = 0

        for desde in range(0, len(self.consumos), TOPE_POR_LOTE):
            lote = cliente.batch()
            for consumo in self.consumos[desde : desde + TOPE_POR_LOTE]:
                # El id es el run_id: LangChain lo genera único por llamada, así
                # que un reintento del volcado sobreescribe en lugar de duplicar.
                lote.set(coleccion.document(consumo.run_id), consumo.model_dump(mode="json"))
                escritos += 1
            await lote.commit()

        if limpiar:
            self.consumos = []
        return escritos


class _EnVuelo(BaseModel):
    """Lo que se sabe de una llamada entre que empieza y termina."""

    model_config = ConfigDict(frozen=True)

    comenzo: float
    momento: dt.datetime
    parent_run_id: str | None
    thread_id: str | None
    perfil: str
    modelo: str


def _tokens_de(response: LLMResult) -> tuple[int, int]:
    """Tokens de entrada y salida de una respuesta.

    Se leen del `usage_metadata` del mensaje, que es lo que reportan los
    adapters de la línea 1.x. `llm_output` queda como respaldo porque algunos
    proveedores todavía lo llenan y otros lo dejan en `None`.
    """
    for generaciones in response.generations:
        for generacion in generaciones:
            mensaje = getattr(generacion, "message", None)
            uso = getattr(mensaje, "usage_metadata", None)
            if uso:
                return int(uso.get("input_tokens", 0)), int(uso.get("output_tokens", 0))

    crudo = (response.llm_output or {}).get("token_usage") or {}
    return int(crudo.get("prompt_tokens", 0)), int(crudo.get("completion_tokens", 0))
