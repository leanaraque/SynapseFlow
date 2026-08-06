"""Carga idempotente de los datos del dominio a Firestore.

    python -m scripts.seed --dry-run     # reporta sin escribir
    python -m scripts.seed               # escribe contra el emulador

## Idempotencia

El id de cada documento es la clave natural que declara la ontología —`tag` para
un activo, `id_ot` para una orden de trabajo— así que una segunda corrida
sobreescribe los mismos documentos en lugar de agregar copias. Correrlo dos
veces deja exactamente la misma cantidad de registros.

Es la diferencia entre poder recargar los datos cuando cambia el generador y
tener que vaciar las colecciones a mano cada vez.

## Por qué el destino se verifica antes de escribir

Escribir en la base real por accidente es el modo de falla más caro de este
script: no hay deshacer, y los datos sintéticos quedarían mezclados con lo que
hubiera. Por eso el destino por defecto es el emulador y apuntar a un proyecto
real exige `--permitir-produccion` de forma explícita.

No alcanza con documentarlo: la variable `FIRESTORE_EMULATOR_HOST` se pierde al
cambiar de terminal, y ese olvido no produce ningún error visible — produce
escrituras.

## El corpus no se carga acá

`data/corpus/` necesita trocearse y vectorizarse antes de entrar a
`corpus_chunks`, y vectorizar exige un modelo de embeddings, que llega con el
gateway de F1. La ingesta del corpus es F3.1 (`rag/ingesta.py`). Este script
carga los datos estructurados del dominio y reporta el corpus como pendiente.

Ver docs/plan/fases/F0-datos.md § F0.3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.generar_datos import SALIDA_POR_DEFECTO, validar_contra_ontologia

from synapseflow.config import get_settings
from synapseflow.ontology import Ontology, get_ontology
from synapseflow.persistence.client import get_client

RAIZ = Path(__file__).resolve().parent.parent

# Entidades que este script carga. `standard_document` queda afuera a
# propósito: su colección `corpus_chunks` la escribe la ingesta de F3.1, que es
# la que trocea y vectoriza.
ENTIDADES_DEL_DOMINIO = ("installation", "asset", "inspection", "work_order")

# Firestore admite hasta 500 operaciones por batch.
TAMANO_DE_LOTE = 400


class SeedError(RuntimeError):
    """La carga no se puede realizar. El mensaje dice qué corregir."""


@dataclass(frozen=True)
class ResumenDeColeccion:
    entidad: str
    coleccion: str
    clave: str
    registros: int
    # Cantidad de documentos que quedaron en la colección después de escribir.
    # Es el número que hay que comparar entre dos corridas para comprobar la
    # idempotencia; en dry-run queda en None porque no se consulta la base.
    total_en_destino: int | None = None


@dataclass(frozen=True)
class ResumenDeCarga:
    destino: str
    dry_run: bool
    colecciones: list[ResumenDeColeccion] = field(default_factory=list)

    @property
    def registros(self) -> int:
        return sum(c.registros for c in self.colecciones)


def _describir_destino(permitir_produccion: bool) -> str:
    """Resuelve contra qué base se va a escribir, o falla.

    Se llama antes de leer un solo archivo: si el destino no es admisible, no
    tiene sentido hacer nada más.
    """
    settings = get_settings()

    if settings.using_emulator:
        return f"emulador {settings.firestore_emulator_host} (proyecto {settings.gcp_project})"

    if not permitir_produccion:
        raise SeedError(
            "FIRESTORE_EMULATOR_HOST no está definida, así que la escritura iría "
            f"a la base REAL del proyecto '{settings.gcp_project}'.\n"
            "  Para cargar contra el emulador:\n"
            "    firebase emulators:start --only firestore --project synapseflow-lean\n"
            "    export FIRESTORE_EMULATOR_HOST=localhost:8080\n"
            "  Si de verdad querés escribir en el proyecto real, pasá "
            "--permitir-produccion."
        )

    return f"PRODUCCIÓN — proyecto {settings.gcp_project}, base {settings.firestore_database}"


def leer_datos(origen: Path, onto: Ontology) -> dict[str, list[dict[str, Any]]]:
    """Lee los JSON generados y los devuelve indexados por id de entidad.

    Valida contra la ontología con la misma función que usa el generador. Un
    registro inválido tiene que frenar acá: una vez escrito en Firestore, el
    error aparece recién cuando un agente lo consulte, y para entonces nadie
    recuerda de qué corrida salió.
    """
    if not origen.is_dir():
        raise SeedError(
            f"no existe el directorio de datos '{origen}'.\n"
            "  Generalos primero con: python -m scripts.generar_datos"
        )

    datos: dict[str, list[dict[str, Any]]] = {}
    for entidad_id in ENTIDADES_DEL_DOMINIO:
        archivo = origen / f"{onto.entity(entidad_id).collection}.json"
        if not archivo.is_file():
            raise SeedError(
                f"falta el archivo '{archivo.name}' en '{origen}'.\n"
                "  Regeneralo con: python -m scripts.generar_datos"
            )
        datos[entidad_id] = json.loads(archivo.read_text(encoding="utf-8"))

    errores = validar_contra_ontologia(onto, datos)
    if errores:
        detalle = "\n".join(f"  · {e}" for e in errores[:20])
        extra = f"\n  … y {len(errores) - 20} más" if len(errores) > 20 else ""
        raise SeedError(f"los datos no cumplen la ontología:\n{detalle}{extra}")

    return datos


async def cargar(
    origen: Path | None = None,
    *,
    dry_run: bool = False,
    permitir_produccion: bool = False,
) -> ResumenDeCarga:
    """Carga los datos del dominio a Firestore.

    Args:
        origen: directorio con los JSON generados. Por defecto, `data/generado`.
        dry_run: si es True, reporta qué escribiría y no toca la base ni la red.
        permitir_produccion: habilita escribir fuera del emulador.
    """
    onto = get_ontology()
    destino = _describir_destino(permitir_produccion)
    datos = leer_datos(origen or RAIZ / SALIDA_POR_DEFECTO, onto)

    if dry_run:
        return ResumenDeCarga(
            destino=destino,
            dry_run=True,
            colecciones=[
                ResumenDeColeccion(
                    entidad=eid,
                    coleccion=onto.entity(eid).collection,
                    clave=onto.entity(eid).key,
                    registros=len(registros),
                )
                for eid, registros in datos.items()
            ],
        )

    cliente = get_client()
    colecciones: list[ResumenDeColeccion] = []

    for entidad_id, registros in datos.items():
        entidad = onto.entity(entidad_id)
        referencia = cliente.collection(entidad.collection)

        for lote in _en_lotes(registros, TAMANO_DE_LOTE):
            batch = cliente.batch()
            for registro in lote:
                # El id del documento ES la clave natural: es lo que hace que
                # una segunda corrida sobreescriba en lugar de duplicar.
                batch.set(referencia.document(str(registro[entidad.key])), registro)
            await batch.commit()

        total = await referencia.count().get()
        colecciones.append(
            ResumenDeColeccion(
                entidad=entidad_id,
                coleccion=entidad.collection,
                clave=entidad.key,
                registros=len(registros),
                total_en_destino=int(total[0][0].value),
            )
        )

    return ResumenDeCarga(destino=destino, dry_run=False, colecciones=colecciones)


def _en_lotes(items: list[Any], tamano: int) -> list[list[Any]]:
    return [items[i : i + tamano] for i in range(0, len(items), tamano)]


# ─────────────────────────────────────────────────────────────────────────────
# Línea de comandos
# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed",
        description="Carga idempotente de los datos del dominio a Firestore.",
        epilog="Por defecto escribe contra el emulador. Ver --permitir-produccion.",
    )
    parser.add_argument(
        "--origen",
        default=None,
        help=f"directorio con los JSON generados (por defecto {SALIDA_POR_DEFECTO})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="reporta qué escribiría, sin tocar la base ni la red",
    )
    parser.add_argument(
        "--permitir-produccion",
        action="store_true",
        help="habilita escribir fuera del emulador; requerido para tocar la base real",
    )
    return parser


def imprimir(resumen: ResumenDeCarga) -> None:
    print(f"Destino    {resumen.destino}")
    print(f"Modo       {'dry-run, no se escribe nada' if resumen.dry_run else 'escritura'}")
    print()

    ancho = max(len(c.coleccion) for c in resumen.colecciones)
    encabezado = f"  {'COLECCION'.ljust(ancho)}  {'CLAVE':18s}  REGISTROS"
    if not resumen.dry_run:
        encabezado += "  EN DESTINO"
    print(encabezado)
    print(
        f"  {'-' * ancho}  {'-' * 18}  ---------" + ("  ----------" if not resumen.dry_run else "")
    )

    for c in resumen.colecciones:
        linea = f"  {c.coleccion.ljust(ancho)}  {c.clave:18s}  {c.registros:9d}"
        if c.total_en_destino is not None:
            linea += f"  {c.total_en_destino:10d}"
        print(linea)

    print()
    print(f"  Total      {resumen.registros} registros")
    print()
    print("  corpus_chunks queda pendiente: lo escribe la ingesta de F3.1, que")
    print("  necesita el modelo de embeddings del gateway de F1.")

    if not resumen.dry_run:
        print()
        print("  Idempotencia: volvé a correrlo y la columna EN DESTINO no debe cambiar.")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    origen = Path(args.origen) if args.origen else None
    if origen is not None and not origen.is_absolute():
        origen = RAIZ / origen

    try:
        resumen = asyncio.run(
            cargar(
                origen,
                dry_run=args.dry_run,
                permitir_produccion=args.permitir_produccion,
            )
        )
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    imprimir(resumen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
