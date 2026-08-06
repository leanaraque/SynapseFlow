"""Línea de comandos de SynapseFlow.

Sirve para inspeccionar la plataforma sin arrancar nada: sin API key, sin
Firebase y sin red. Es la forma más rápida de entender qué hace el proyecto
después de clonarlo.

    synapseflow ontology validate
    synapseflow ontology tools --role inspector
    synapseflow ontology roles
    synapseflow ontology graph

El comando `validate` es además el que corre en CI: si alguien rompe una
invariante de gobernanza editando el YAML del dominio, el build falla.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import Sequence
from typing import TextIO

from synapseflow.ontology import (
    Effect,
    Ontology,
    OntologyError,
    available_domains,
    get_ontology,
    interrupt_config,
)


def _preparar_salida() -> bool:
    """Deja la salida en UTF-8 si se puede, y dice si admite Unicode.

    En Windows, `sys.stdout` usa cp1252 cuando no es una consola —redirigido a
    un archivo, entubado, capturado por CI—. Los caracteres de dibujo de las
    tablas no se pueden codificar ahí, así que `synapseflow ontology validate >
    salida.txt` terminaba en UnicodeEncodeError con código 1 y la salida
    truncada a la mitad.

    Es el comando que el README ofrece como lo primero que funciona después de
    clonar, y el que corre en el job de gobernanza del CI volcando su salida al
    resumen del build. `scripts/estado.py` ya tenía esta defensa; la CLI no.

    Si no se puede reconfigurar —stdout sustituido por un test, por ejemplo— se
    cae a símbolos ASCII en lugar de fallar.
    """
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    return "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()


UNICODE = _preparar_salida()

# Se evita cualquier dependencia de color: la salida tiene que ser legible
# cuando se redirige a un archivo o se lee en el log de CI.
_RAYA = "─" if UNICODE else "-"
_SEP = _RAYA * 72
_OK = "✓" if UNICODE else "[OK]"


def _print_tabla(filas: Sequence[Sequence[str]], encabezados: Sequence[str], out: TextIO) -> None:
    anchos = [len(h) for h in encabezados]
    for fila in filas:
        for i, celda in enumerate(fila):
            anchos[i] = max(anchos[i], len(celda))
    linea = "  ".join(h.ljust(anchos[i]) for i, h in enumerate(encabezados))
    print(linea.rstrip(), file=out)
    print("  ".join(_RAYA * a for a in anchos), file=out)
    for fila in filas:
        print("  ".join(c.ljust(anchos[i]) for i, c in enumerate(fila)).rstrip(), file=out)


# ─────────────────────────────────────────────────────────────────────────────
# Comandos
# ─────────────────────────────────────────────────────────────────────────────


def cmd_validate(onto: Ontology, out: TextIO) -> int:
    """Valida el dominio y reporta las invariantes de gobernanza.

    Que el YAML cargue ya significa que pasó el meta-esquema: las validaciones
    duras viven en los validadores de Pydantic. Acá se reportan los hechos que
    a un revisor le interesa ver de un vistazo.
    """
    print(f"Dominio    {onto.domain}", file=out)
    print(f"Versión    {onto.version}", file=out)
    print(f"Título     {onto.title}", file=out)
    print(file=out)
    print(
        f"{len(onto.entities)} entidades · {len(onto.relations)} relaciones · "
        f"{len(onto.actions)} acciones · {len(onto.roles)} roles",
        file=out,
    )
    print(file=out)

    irreversibles = onto.irreversible_actions()
    print("Acciones irreversibles y quién puede aprobarlas", file=out)
    print(_SEP, file=out)
    if not irreversibles:
        print("  (ninguna)", file=out)
    else:
        _print_tabla(
            [(a.id, ", ".join(a.approver_roles)) for a in irreversibles],
            ("ACCIÓN", "APROBADORES"),
            out,
        )
    print(file=out)

    pii = onto.pii_fields()
    print("Campos que nunca salen en claro hacia un proveedor de LLM", file=out)
    print(_SEP, file=out)
    if not pii:
        print("  (ninguno)", file=out)
    else:
        _print_tabla(
            [(entidad, ", ".join(campos)) for entidad, campos in sorted(pii.items())],
            ("ENTIDAD", "CAMPOS"),
            out,
        )
    print(file=out)
    print(f"{_OK} la ontología es válida y las invariantes de gobernanza se sostienen", file=out)
    return 0


def cmd_tools(onto: Ontology, rol: str, out: TextIO) -> int:
    """Catálogo de herramientas visible para un rol.

    Muestra el mínimo privilegio en acción: lo que no aparece acá, el modelo
    directamente no lo recibe y por lo tanto no lo puede invocar.
    """
    try:
        rol_obj = onto.role(rol)
    except KeyError:
        print(f"error: no existe el rol '{rol}'", file=sys.stderr)
        print(f"roles disponibles: {', '.join(r.id for r in onto.roles)}", file=sys.stderr)
        return 2

    acciones = onto.actions_for_role(rol)
    gates = interrupt_config(onto, rol)

    print(f"Rol        {rol_obj.id} — {rol_obj.title}", file=out)
    print(f"Clasif.    hasta '{rol_obj.max_classification}'", file=out)
    print(f"Ve         {len(acciones)} de {len(onto.actions)} acciones del dominio", file=out)
    print(file=out)

    filas = []
    for a in acciones:
        if a.id in gates:
            marca = "requiere aprobación"
        elif a.effect is Effect.WRITE:
            marca = "escritura reversible"
        else:
            marca = ""
        params = ", ".join(p.name for p in a.parameters) or "—"
        filas.append((a.id, a.effect.value, params, marca))

    _print_tabla(filas, ("HERRAMIENTA", "EFECTO", "PARÁMETROS", "GATE"), out)
    return 0


def cmd_roles(onto: Ontology, out: TextIO) -> int:
    filas = [
        (
            r.id,
            r.title,
            r.max_classification,
            str(len(onto.actions_for_role(r.id))),
            str(sum(1 for a in onto.actions_for_role(r.id) if a.effect is Effect.WRITE)),
        )
        for r in onto.roles
    ]
    _print_tabla(filas, ("ROL", "TÍTULO", "CLASIFICACIÓN", "ACCIONES", "ESCRITURAS"), out)
    return 0


def cmd_graph(onto: Ontology, out: TextIO) -> int:
    """Emite el grafo de entidades y relaciones en formato Mermaid.

    Pensado para pegar en documentación o en un issue: la ontología se explica
    mejor dibujada que enumerada.
    """
    print("```mermaid", file=out)
    print("erDiagram", file=out)
    for e in onto.entities:
        print(f"    {e.id} {{", file=out)
        for p in e.properties:
            marca = "PK" if p.name == e.key else ("PII" if p.pii else "")
            print(f"        {p.type.value} {p.name} {marca}".rstrip(), file=out)
        print("    }", file=out)
    for r in onto.relations:
        simbolo = {
            "one_to_one": "||--||",
            "one_to_many": "||--o{",
            "many_to_one": "}o--||",
            "many_to_many": "}o--o{",
        }[r.cardinality.value]
        print(f'    {r.from_} {simbolo} {r.to} : "{r.name}"', file=out)
    print("```", file=out)
    return 0


# ─────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synapseflow",
        description="Inspección de la plataforma SynapseFlow.",
        epilog="Ninguno de estos comandos necesita credenciales ni acceso a red.",
    )
    parser.add_argument(
        "--domain",
        default=None,
        help=f"dominio a cargar. Disponibles: {', '.join(available_domains())}",
    )

    sub = parser.add_subparsers(dest="grupo", required=True)
    onto_parser = sub.add_parser("ontology", help="inspeccionar la ontología del dominio")
    onto_sub = onto_parser.add_subparsers(dest="comando", required=True)

    onto_sub.add_parser("validate", help="validar el dominio y sus invariantes de gobernanza")
    p_tools = onto_sub.add_parser("tools", help="catálogo de herramientas de un rol")
    p_tools.add_argument("--role", required=True, help="rol a inspeccionar")
    onto_sub.add_parser("roles", help="listar los roles y su alcance")
    onto_sub.add_parser("graph", help="emitir el diagrama de entidades en Mermaid")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = sys.stdout

    try:
        onto = get_ontology(args.domain) if args.domain else get_ontology()
    except OntologyError as exc:
        # El mensaje de OntologyError ya viene formateado para ser accionable:
        # dice qué corregir en el YAML y dónde.
        print(f"error: {exc}", file=sys.stderr)
        return 1

    match args.comando:
        case "validate":
            return cmd_validate(onto, out)
        case "tools":
            return cmd_tools(onto, args.role, out)
        case "roles":
            return cmd_roles(onto, out)
        case "graph":
            return cmd_graph(onto, out)
        case _:  # pragma: no cover - argparse lo impide
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
