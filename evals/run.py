"""Corredor de evaluación con comparación contra línea base.

    python -m evals.run --suite normativa
    python -m evals.run --suite all --comparar-linea-base

## Por qué reporta por caso y no solo por métrica

Saber que la fidelidad bajó de 0,91 a 0,87 no sirve para nada: no dice qué
romper ni qué mirar. Saber **qué tres casos se rompieron** sí. El promedio existe
para la comparación automática contra la línea base; el detalle por caso, para la
persona que tiene que arreglarlo.

## Qué cuenta como regresión

Una métrica que baja **más allá de un margen**. El margen no es indulgencia: el
evaluador de fidelidad usa un modelo, y un modelo tiene varianza. Sin margen, el
CI fallaría de manera intermitente y el equipo aprendería a reintentar hasta que
pase — que es la peor forma de tener un CI.

Los evaluadores determinísticos no tienen varianza, así que para ellos el margen
efectivo es cero: si `precision_de_citas` baja, algo se rompió.

## La caché

La misma pregunta se evalúa muchas veces —una por corrida— y pagarla cada vez no
tiene sentido. La caché es por proceso: sobrevive a una corrida completa y no
entre corridas. Una caché persistente exigiría `langchain-community`, que este
proyecto no instala por una sola pieza (ver las convenciones), y el ahorro real
está dentro de la corrida, donde el supervisor pregunta lo mismo varias veces.

Ver docs/plan/fases/F8-evals.md § F8.3
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from langchain_core.caches import InMemoryCache
from langchain_core.globals import set_llm_cache
from pydantic import BaseModel, ConfigDict, Field

from evals.evaluadores import calculos as ev_calculos
from evals.evaluadores import citas as ev_citas
from evals.evaluadores import fidelidad as ev_fidelidad
from evals.evaluadores import rechazo as ev_rechazo
from evals.evaluadores.base import Caso, Fragmento, RespuestaDelSistema, Resultado
from synapseflow.agents.graph import construir_grafo
from synapseflow.governance.rbac import ExecutionContext
from synapseflow.llm.gateway import Gateway
from synapseflow.ontology import get_ontology
from synapseflow.persistence.client import Collections, get_client

# Importar el paquete es lo que dispara los `@implements` de las nueve acciones.
# Sin esto, `compile_tools` falla diciendo que faltan implementaciones — con los
# archivos ahí y las funciones escritas. Ver synapseflow/domain/__init__.py.
import synapseflow.domain  # noqa: F401  isort: skip

DATASETS = Path(__file__).resolve().parent / "datasets"
SUITES = ("normativa", "datos", "calculos", "rechazo")

# Margen por métrica antes de declarar regresión.
#
# Cero para las determinísticas: si bajan, algo se rompió y no hay ruido que lo
# explique. La fidelidad usa un modelo, y sin margen el CI fallaría de manera
# intermitente — y un CI intermitente enseña a reintentar hasta que pase.
MARGEN: dict[str, float] = {
    "precision_de_citas": 0.0,
    "correccion_del_rechazo": 0.0,
    "exactitud_del_calculo": 0.0,
    "no_exposicion_de_datos": 0.0,
    "fidelidad": 0.05,
}


class Corrida(BaseModel):
    """El resultado de una corrida completa, tal como se guarda en Firestore."""

    model_config = ConfigDict(frozen=True)

    id: str
    ts: dt.datetime
    suite: str
    rama: str
    commit: str = ""
    metricas: dict[str, float] = Field(default_factory=dict)
    casos: list[dict[str, Any]] = Field(default_factory=list)
    total_casos: int = 0
    fallados: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Carga de casos
# ─────────────────────────────────────────────────────────────────────────────


def cargar_casos(suite: str) -> list[Caso]:
    """Casos de una suite, o de todas si `suite` es `all`."""
    nombres = SUITES if suite == "all" else (suite,)
    casos: list[Caso] = []

    for nombre in nombres:
        ruta = DATASETS / f"{nombre}.jsonl"
        if not ruta.exists():
            raise FileNotFoundError(f"no existe la suite '{nombre}' en {DATASETS}")
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                casos.append(Caso.model_validate(json.loads(linea)))

    return casos


# ─────────────────────────────────────────────────────────────────────────────
# Ejecución
# ─────────────────────────────────────────────────────────────────────────────


async def responder(caso: Caso, grafo: Any, ctx: ExecutionContext) -> RespuestaDelSistema:
    """Corre un caso por el grafo y arma la respuesta que evalúan los evaluadores."""
    config = {"configurable": {"thread_id": f"eval-{caso.id}-{uuid.uuid4().hex[:8]}"}}
    estado = await grafo.ainvoke({"messages": [{"role": "user", "content": caso.pregunta}]}, config)

    return RespuestaDelSistema(
        texto=_ultimo_texto(estado),
        citas=tuple(f"{c['doc_id']} §{c['seccion']}" for c in estado.get("citas") or []),
        calculos=dict(estado.get("calculos") or {}),
        veredicto=estado.get("veredicto"),
        fragmentos=tuple(
            Fragmento(
                doc_id=str(d.metadata.get("doc_id") or ""),
                seccion=str(d.metadata.get("seccion") or ""),
                contenido=d.page_content,
            )
            for d in estado.get("recuperados") or []
        ),
    )


def _ultimo_texto(estado: dict[str, Any]) -> str:
    for mensaje in reversed(estado.get("messages") or []):
        contenido = str(getattr(mensaje, "content", "") or "").strip()
        if contenido:
            return contenido
    return ""


async def evaluar_caso(
    caso: Caso, respuesta: RespuestaDelSistema, *, gateway: Gateway | None = None
) -> list[Resultado]:
    """Aplica los cuatro evaluadores. Los que no corresponden devuelven `None`."""
    posibles = [
        ev_citas.evaluar(caso, respuesta),
        ev_rechazo.evaluar(caso, respuesta),
        ev_rechazo.evaluar_no_expone(caso, respuesta),
        ev_calculos.evaluar(caso, respuesta),
        await ev_fidelidad.evaluar(caso, respuesta, gateway=gateway),
    ]
    return [r for r in posibles if r is not None]


async def correr(
    suite: str,
    *,
    gateway: Gateway | None = None,
    ctx: ExecutionContext | None = None,
    rama: str = "local",
    commit: str = "",
) -> Corrida:
    """Corre una suite completa y devuelve el resultado agregado."""
    # La caché vive dentro de la corrida: el supervisor pregunta lo mismo varias
    # veces y no tiene sentido pagarlo por cada caso.
    set_llm_cache(InMemoryCache())

    casos = cargar_casos(suite)
    contexto = ctx or ExecutionContext(usuario="eval", rol="inspector", thread_id="eval")
    grafo = construir_grafo(get_ontology(), contexto, gateway=gateway)

    filas: list[dict[str, Any]] = []
    por_metrica: dict[str, list[float]] = {}

    for caso in casos:
        try:
            respuesta = await responder(caso, grafo, contexto)
            resultados = await evaluar_caso(caso, respuesta, gateway=gateway)
        except Exception as exc:
            # Un caso que revienta es un dato, no una excusa para abortar. Se
            # registra como fallo de todas las métricas y la corrida sigue: con
            # un `raise`, un caso roto ocultaría el resultado de los otros 30.
            filas.append(
                {
                    "caso": caso.id,
                    "metrica": "ejecucion",
                    "puntaje": 0.0,
                    "aprobado": False,
                    "detalle": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
            )
            por_metrica.setdefault("ejecucion", []).append(0.0)
            continue

        por_metrica.setdefault("ejecucion", []).append(1.0)
        for resultado in resultados:
            filas.append(resultado.model_dump())
            por_metrica.setdefault(resultado.metrica, []).append(resultado.puntaje)

    metricas = {m: sum(v) / len(v) for m, v in por_metrica.items() if v}

    return Corrida(
        id=f"{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}",
        ts=dt.datetime.now(dt.UTC),
        suite=suite,
        rama=rama,
        commit=commit,
        metricas=metricas,
        casos=filas,
        total_casos=len(casos),
        fallados=len({f["caso"] for f in filas if not f["aprobado"]}),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Persistencia y línea base
# ─────────────────────────────────────────────────────────────────────────────


async def guardar(corrida: Corrida, *, cliente: Any = None) -> str:
    """Guarda la corrida en `eval_runs` y devuelve su id."""
    db = cliente or get_client()
    await (
        db.collection(Collections.EVAL_RUNS)
        .document(corrida.id)
        .set(corrida.model_dump(mode="json"))
    )
    return corrida.id


async def linea_base(suite: str, *, rama: str = "main", cliente: Any = None) -> Corrida | None:
    """La última corrida de la rama de referencia para esta suite.

    Devuelve `None` si no hay ninguna. La primera corrida no puede regresar
    contra nada, y tratarla como fallo bloquearía el primer PR que agregue evals.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter

    db = cliente or get_client()
    consulta = (
        db.collection(Collections.EVAL_RUNS)
        .where(filter=FieldFilter("rama", "==", rama))
        .where(filter=FieldFilter("suite", "==", suite))
        .order_by("ts", direction="DESCENDING")
        .limit(1)
    )

    async for documento in consulta.stream():
        return Corrida.model_validate(documento.to_dict())
    return None


def ejecuto_algo(corrida: Corrida) -> bool:
    """Si al menos un caso llegó a producir una respuesta.

    Una corrida donde todos los casos reventaron no mide la calidad del sistema:
    mide que algo del entorno está roto. Distinguirla de una regresión importa
    porque el diagnóstico es completamente distinto —credenciales o red, no
    prompts—.
    """
    return corrida.metricas.get("ejecucion", 0.0) > 0.0


def regresiones(actual: Corrida, base: Corrida) -> list[str]:
    """Métricas que empeoraron más allá de su margen."""
    caidas: list[str] = []

    for metrica, valor in actual.metricas.items():
        anterior = base.metricas.get(metrica)
        if anterior is None:
            continue
        margen = MARGEN.get(metrica, 0.0)
        if valor < anterior - margen:
            caidas.append(f"{metrica}: {anterior:.3f} → {valor:.3f} (margen {margen:.2f})")

    return caidas


# ─────────────────────────────────────────────────────────────────────────────
# Reporte
# ─────────────────────────────────────────────────────────────────────────────


def reportar(corrida: Corrida, base: Corrida | None = None) -> str:
    """Reporte legible: métricas arriba, casos rotos abajo."""
    lineas = [
        f"Suite: {corrida.suite}  ·  {corrida.total_casos} casos  ·  "
        f"{corrida.fallados} con al menos un fallo",
        "",
        "MÉTRICAS",
    ]

    for metrica in sorted(corrida.metricas):
        valor = corrida.metricas[metrica]
        anterior = (base.metricas.get(metrica) if base else None) or None
        delta = f"  (base {anterior:.3f}, Δ {valor - anterior:+.3f})" if anterior else ""
        lineas.append(f"  {metrica:28} {valor:.3f}{delta}")

    rotos = [f for f in corrida.casos if not f["aprobado"]]
    if rotos:
        lineas += ["", "CASOS QUE FALLARON"]
        for fila in rotos:
            lineas.append(f"  [{fila['caso']}] {fila['metrica']}: {fila['detalle']}")
    else:
        lineas += ["", "Ningún caso falló."]

    return "\n".join(lineas)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description="Corre la suite de evaluación y compara contra la línea base.",
    )
    parser.add_argument("--suite", default="all", choices=[*SUITES, "all"])
    parser.add_argument(
        "--comparar-linea-base",
        action="store_true",
        help="contrasta contra la última corrida de main y falla ante regresión",
    )
    parser.add_argument("--rama", default="local", help="rama con la que etiquetar la corrida")
    parser.add_argument("--commit", default="", help="sha del commit, para trazar la corrida")
    parser.add_argument(
        "--sin-guardar",
        action="store_true",
        help="no escribe en Firestore; para probar el corredor sin dejar rastro",
    )
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    corrida = await correr(args.suite, rama=args.rama, commit=args.commit)

    base = None
    if args.comparar_linea_base:
        base = await linea_base(args.suite)

    print(reportar(corrida, base))

    if not args.sin_guardar:
        print(f"\nCorrida guardada: {await guardar(corrida)}")

    # Una corrida donde no ejecutó ningún caso está rota, no «regresada». Sin
    # este corte salía con código 0 cuando no había línea base contra la cual
    # comparar, y un CI con el proveedor mal configurado habría reportado
    # fracaso total en verde.
    if not ejecuto_algo(corrida):
        print(
            "\nNINGÚN caso se pudo ejecutar. La corrida no mide nada: revisar el "
            "proveedor, las credenciales y el acceso a Firestore antes de leer "
            "las métricas."
        )
        return 2

    if base is None:
        if args.comparar_linea_base:
            print("\nSin línea base para esta suite: no hay contra qué comparar.")
        return 0

    caidas = regresiones(corrida, base)
    if caidas:
        print("\nREGRESIÓN respecto de la línea base:")
        for caida in caidas:
            print(f"  {caida}")
        return 1

    print("\nSin regresión respecto de la línea base.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(main()))
