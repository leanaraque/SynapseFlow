"""El catálogo completo compila. Es el test que certifica que F2 terminó.

Antes de esta fase, `compile_tools` lanzaba `CompilationError` porque ninguna
acción del YAML tenía implementación registrada. Ese error era intencional —el
proyecto se niega a compilar un catálogo incompleto— y esta fase lo resuelve.

Lo que se verifica no es que las nueve funciones existan, sino que el **puente
entre el YAML y el código esté completo en las dos direcciones**: que ninguna
acción declarada quede sin implementar, y que ninguna implementación registrada
apunte a una acción que ya no existe en la ontología.

Ver docs/plan/fases/F2-dominio.md § F2.5
"""

from __future__ import annotations

import inspect

import pytest

from synapseflow.ontology import ToolResult, compile_tools, get_ontology, registered_actions
from synapseflow.ontology.compiler import _REGISTRY

# Importar el paquete es lo que dispara los `@implements`. Sin esto el registro
# está vacío y todos los tests de acá fallan por una razón que no es la real.
import synapseflow.domain  # noqa: F401  isort: skip


def test_las_nueve_acciones_compilan() -> None:
    """`compile_tools` ya no lanza `CompilationError` para ningún rol.

    Este es el test que certifica que la fase terminó.
    """
    onto = get_ontology()

    for rol in [r.id for r in onto.roles]:
        herramientas = compile_tools(onto, rol)
        esperadas = onto.actions_for_role(rol)

        assert len(herramientas) == len(esperadas), (
            f"el rol '{rol}' ve {len(herramientas)} herramientas y la ontología "
            f"le declara {len(esperadas)} acciones"
        )


def test_toda_accion_de_la_ontologia_tiene_implementacion() -> None:
    """La dirección YAML → código.

    Una acción declarada sin implementar es una herramienta que el modelo puede
    invocar y que no existe.
    """
    declaradas = {a.id for a in get_ontology().actions}
    faltantes = sorted(declaradas - registered_actions())

    assert not faltantes, f"acciones declaradas en el YAML sin implementación: {faltantes}"


def test_toda_implementacion_apunta_a_una_accion_declarada() -> None:
    """La dirección código → YAML.

    Una implementación huérfana es código muerto que igual se lee como parte del
    contrato: alguien la mantiene, la testea y la documenta sin que nada la
    invoque nunca.
    """
    declaradas = {a.id for a in get_ontology().actions}
    huerfanas = sorted(registered_actions() - declaradas)

    assert not huerfanas, f"implementaciones sin acción en el YAML: {huerfanas}"


def test_son_nueve() -> None:
    """El número que publica el README y la CLI."""
    assert len(get_ontology().actions) == 9
    assert len(registered_actions()) == 9


# ─────────────────────────────────────────────────────────────────────────────
# El contrato de cada implementación
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("action_id", sorted(a.id for a in get_ontology().actions))
def test_cada_implementacion_declara_devolver_toolresult(action_id: str) -> None:
    """El compilador lo verifica en runtime, cuando la acción ya se ejecutó.

    Verificarlo sobre la anotación lo adelanta al arranque: una implementación
    que devuelve un dict falla acá y no a mitad de una conversación, después de
    haber escrito en la base.
    """
    impl = _REGISTRY[action_id]
    anotaciones = inspect.get_annotations(impl, eval_str=True)

    assert anotaciones.get("return") is ToolResult, f"'{action_id}' no declara devolver ToolResult"


@pytest.mark.parametrize("action_id", sorted(a.id for a in get_ontology().actions))
def test_cada_implementacion_es_asincronica(action_id: str) -> None:
    """Toda la capa de datos lo es: no hay camino sincrónico."""
    assert inspect.iscoroutinefunction(_REGISTRY[action_id]), (
        f"'{action_id}' no es async, y el compilador la registra como `coroutine`"
    )


@pytest.mark.parametrize("action_id", sorted(a.id for a in get_ontology().actions))
def test_cada_implementacion_recibe_ctx_como_keyword_only(action_id: str) -> None:
    """El compilador la invoca siempre como `impl(**kwargs, ctx=context)`.

    Si `ctx` fuera posicional, un parámetro del YAML llamado igual lo pisaría sin
    que nada avise, y la acción se ejecutaría con la identidad equivocada.
    """
    firma = inspect.signature(_REGISTRY[action_id])
    ctx = firma.parameters.get("ctx")

    assert ctx is not None, f"'{action_id}' no recibe ctx"
    assert ctx.kind is inspect.Parameter.KEYWORD_ONLY, f"el ctx de '{action_id}' no es keyword-only"


@pytest.mark.parametrize("action_id", sorted(a.id for a in get_ontology().actions))
def test_la_firma_cubre_todos_los_parametros_del_yaml(action_id: str) -> None:
    """El compilador arma el `args_schema` desde el YAML e invoca con esos nombres.

    Un parámetro declarado que la función no acepta produce un `TypeError` en la
    primera invocación real — con el usuario esperando, y sin relación aparente
    con el YAML que lo causó.
    """
    accion = next(a for a in get_ontology().actions if a.id == action_id)
    parametros = set(inspect.signature(_REGISTRY[action_id]).parameters)

    faltantes = sorted({p.name for p in accion.parameters} - parametros)
    assert not faltantes, (
        f"'{action_id}' declara en el YAML los parámetros {faltantes} que su "
        "implementación no acepta"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Mínimo privilegio, ya con el catálogo completo
# ─────────────────────────────────────────────────────────────────────────────


def test_ningun_rol_ve_una_accion_que_no_puede_ejecutar() -> None:
    """El filtrado del catálogo es la barrera real de permisos.

    No hay un chequeo posterior que se pueda olvidar porque no hay nada que
    chequear: lo que el rol no puede ejecutar, el modelo no lo ve.
    """
    onto = get_ontology()

    for rol in [r.id for r in onto.roles]:
        for herramienta in compile_tools(onto, rol):
            accion_id = (herramienta.metadata or {})["action_id"]
            accion = next(a for a in onto.actions if a.id == accion_id)
            assert rol in accion.allowed_roles, (
                f"el rol '{rol}' recibió '{accion_id}', que no tiene permitida"
            )


def test_el_rol_de_consulta_no_ve_ninguna_escritura() -> None:
    """Es el rol más restringido y el que más fácil se rompe al agregar acciones."""
    onto = get_ontology()
    efectos = {(h.metadata or {})["effect"] for h in compile_tools(onto, "consulta")}

    assert efectos <= {"read"}, f"el rol 'consulta' ve acciones de efecto {efectos - {'read'}}"


def test_toda_accion_irreversible_visible_tiene_gate() -> None:
    """La propiedad que sostiene el compromiso 2.

    Se verifica sobre el catálogo compilado, no sobre el YAML: es lo que el
    modelo recibe de verdad.
    """
    from synapseflow.ontology import interrupt_config

    onto = get_ontology()

    for rol in [r.id for r in onto.roles]:
        gates = interrupt_config(onto, rol)
        for herramienta in compile_tools(onto, rol):
            metadatos = herramienta.metadata or {}
            if not metadatos["reversible"]:
                assert herramienta.name in gates, (
                    f"'{herramienta.name}' es irreversible y el rol '{rol}' la ve "
                    "sin gate de aprobación"
                )
