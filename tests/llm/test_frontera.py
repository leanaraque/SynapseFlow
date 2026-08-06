"""La frontera de datos, verificada sobre la estructura del código.

**Este es el test que convierte una convención en una garantía.**

El compromiso 5 del proyecto dice que los datos sensibles no salen del
perímetro. Esa promesa se apoya en que exista **un solo camino de salida**: el
gateway, donde se aplica la redacción de PII y la política de entrenamiento. Con
dos caminos, la afirmación deja de ser comprobable — y nadie se entera, porque
el segundo camino funciona perfectamente.

Un comentario en el README pidiendo «usá el gateway» no impide nada. Esto sí:
recorre el árbol de sintaxis de cada módulo del paquete y falla si alguno
importa un `ChatModel` de proveedor por fuera de `synapseflow/llm/`.

Se analiza el AST y no el texto: un `grep` daría falso positivo con la palabra
dentro de un docstring —este archivo mismo las nombra todas— y falso negativo
con un import partido en varias líneas.

Ver docs/plan/fases/F1-gateway.md § F1.5 y
docs/adr/0004-gateway-provider-agnostic.md
"""

from __future__ import annotations

import ast
from pathlib import Path

PAQUETE = Path(__file__).resolve().parents[2] / "packages" / "synapseflow"

# El único directorio autorizado a instanciar un modelo.
FRONTERA = PAQUETE / "llm"

# Clases que abren una conexión con un proveedor de LLM. Importar cualquiera de
# ellas fuera de la frontera crea un segundo camino de salida.
CLASES_DE_PROVEEDOR = frozenset(
    {
        "ChatGoogleGenerativeAI",
        "GoogleGenerativeAIEmbeddings",
        "ChatOpenAI",
        "AzureChatOpenAI",
        "OpenAIEmbeddings",
        "AzureOpenAIEmbeddings",
        "ChatAnthropic",
        # `init_chat_model` resuelve un proveedor por nombre en runtime: es un
        # camino de salida que además esquiva la resolución por perfil.
        "init_chat_model",
    }
)

# Paquetes de proveedor. Importar el módulo entero también abre el camino, y no
# lo detectaría una lista de nombres de clase.
MODULOS_DE_PROVEEDOR = ("langchain_google_genai", "langchain_openai", "langchain_anthropic")


def modulos_fuera_de_la_frontera() -> list[Path]:
    """Los `.py` del paquete que no pueden instanciar un modelo."""
    return sorted(
        ruta
        for ruta in PAQUETE.rglob("*.py")
        if FRONTERA not in ruta.parents and "__pycache__" not in ruta.parts
    )


def importaciones(ruta: Path) -> list[tuple[str, str]]:
    """Pares (módulo, nombre) de todo lo que un archivo importa."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    encontradas: list[tuple[str, str]] = []

    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom):
            origen = nodo.module or ""
            encontradas.extend((origen, alias.name) for alias in nodo.names)
        elif isinstance(nodo, ast.Import):
            encontradas.extend((alias.name, alias.name) for alias in nodo.names)

    return encontradas


# ─────────────────────────────────────────────────────────────────────────────
# La garantía
# ─────────────────────────────────────────────────────────────────────────────


def test_ningun_modulo_externo_importa_un_chatmodel() -> None:
    """Solo `synapseflow.llm` puede instanciar un modelo.

    Si otro módulo lo hace, existe un segundo camino de salida y la promesa de
    que los datos sensibles se redactan antes de salir deja de ser verificable.
    """
    infracciones: list[str] = []

    for ruta in modulos_fuera_de_la_frontera():
        for modulo, nombre in importaciones(ruta):
            relativa = ruta.relative_to(PAQUETE.parent)
            if nombre in CLASES_DE_PROVEEDOR:
                infracciones.append(f"{relativa}: importa {nombre} desde '{modulo}'")
            elif modulo.startswith(MODULOS_DE_PROVEEDOR):
                infracciones.append(f"{relativa}: importa del paquete de proveedor '{modulo}'")

    assert not infracciones, (
        "hay un segundo camino de salida hacia un proveedor de LLM:\n  "
        + "\n  ".join(infracciones)
        + "\n\nTodo el tráfico hacia un modelo pasa por synapseflow.llm.Gateway. "
        "Es lo que hace verificable la promesa sobre los datos sensibles: "
        "con dos caminos, no se puede afirmar que la redacción se aplicó."
    )


def test_el_test_detecta_una_infraccion_de_verdad(tmp_path: Path) -> None:
    """El test anterior pasaría igual si el análisis estuviera roto.

    Ya pasó en este repositorio: `can_role_read_entity` existía desde el primer
    commit sin que nadie la llamara, y la fila figuraba como verificada. Un
    control que no se ejercita a sí mismo no es un control.
    """
    infractor = tmp_path / "agente_desobediente.py"
    infractor.write_text(
        "from langchain_openai import ChatOpenAI\n\nmodelo = ChatOpenAI()\n",
        encoding="utf-8",
    )

    detectadas = [nombre for _, nombre in importaciones(infractor) if nombre in CLASES_DE_PROVEEDOR]
    assert detectadas == ["ChatOpenAI"]


def test_un_import_del_paquete_entero_tambien_se_detecta(tmp_path: Path) -> None:
    """`import langchain_openai` no nombra ninguna clase y abre el camino igual."""
    infractor = tmp_path / "otro_desobediente.py"
    infractor.write_text("import langchain_openai\n", encoding="utf-8")

    assert any(modulo.startswith(MODULOS_DE_PROVEEDOR) for modulo, _ in importaciones(infractor))


def test_la_frontera_efectivamente_instancia_modelos() -> None:
    """Si nadie los importara, el test de arriba pasaría por vacío.

    Es la otra mitad del control: la garantía es «solo acá», no «en ningún
    lado». Un refactor que moviera los adapters a otro paquete tiene que romper
    este test y no pasar en silencio.
    """
    dentro = [
        nombre
        for ruta in FRONTERA.rglob("*.py")
        for _, nombre in importaciones(ruta)
        if nombre in CLASES_DE_PROVEEDOR
    ]
    assert dentro, (
        "ningún módulo de synapseflow/llm importa un ChatModel de proveedor: "
        "o los adapters se movieron, o este test dejó de comprobar algo"
    )


def test_hay_modulos_que_revisar() -> None:
    """Un `rglob` que no encuentra nada haría pasar todo por vacío."""
    modulos = modulos_fuera_de_la_frontera()
    assert len(modulos) >= 5, f"solo se analizaron {len(modulos)} módulos: la ruta debe estar mal"


def test_la_frontera_es_el_unico_directorio_exento() -> None:
    """Si mañana se exime otro directorio, que sea una decisión y no un descuido.

    La garantía del compromiso 5 se apoya en que el conjunto de exenciones tenga
    exactamente un elemento. Ampliarlo tiene que romper este test.
    """
    assert FRONTERA.name == "llm"
    assert FRONTERA.parent == PAQUETE
