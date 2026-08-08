"""Contrato del cliente: **el navegador no habla con Firestore**.

`firestore.rules` cierra el acceso directo del cliente a todas las colecciones a
propósito. El dominio se sirve por la API, que es la que aplica el RBAC derivado
de la ontología.

Si alguien agrega una llamada al SDK de Firestore desde el frontend «para
resolver algo rápido», va a fallar por reglas. **Eso es el diseño funcionando.**
Este test lo detiene antes, con el motivo escrito, en lugar de dejar que se
descubra como un error opaco de permisos en el navegador.

Es el mismo argumento que `tests/llm/test_frontera.py` hace sobre el gateway: una
garantía que se sostiene por estructura no se puede olvidar. Y va en Python, no
en el runner del frontend, para que corra en el CI que ya existe — sin instalar
node.

Ver docs/plan/fases/F7-consola.md § F7.1
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
WEB = RAIZ / "apps" / "web"
FUENTES = sorted((WEB / "src").rglob("*.ts")) + sorted((WEB / "src").rglob("*.tsx"))

PAQUETE = json.loads((WEB / "package.json").read_text(encoding="utf-8"))

# `from "..."` o `import("...")`. Cubre las dos formas de traer un módulo.
IMPORTS = re.compile(r"""(?:from|import)\s*\(?\s*["']([^"']+)["']""")


def importados(archivo: Path) -> set[str]:
    return set(IMPORTS.findall(archivo.read_text(encoding="utf-8")))


# ─────────────────────────────────────────────────────────────────────────────
# La frontera
# ─────────────────────────────────────────────────────────────────────────────


def test_hay_fuentes_que_revisar() -> None:
    """Un test que no encuentra archivos pasa por vacío, no por correcto."""
    assert FUENTES, "no se encontró código en apps/web/src"


@pytest.mark.parametrize("archivo", FUENTES, ids=lambda p: p.name)
def test_ningun_modulo_importa_el_sdk_de_firestore(archivo: Path) -> None:
    """**La garantía del commit.**

    El cliente no lee la base: las reglas lo prohíben y el RBAC vive en la API.
    """
    prohibidos = {i for i in importados(archivo) if "firebase/firestore" in i}

    assert not prohibidos, (
        f"{archivo.relative_to(RAIZ)} importa {sorted(prohibidos)}. "
        "El cliente no habla con Firestore: las reglas cierran el acceso directo "
        "y el RBAC lo aplica la API. Servilo por un endpoint."
    )


@pytest.mark.parametrize("archivo", FUENTES, ids=lambda p: p.name)
def test_solo_firebase_ts_toca_el_sdk(archivo: Path) -> None:
    """Un solo módulo importa Firebase, igual que un solo módulo habla con el
    proveedor de LLM. Concentrarlo es lo que hace verificable la frontera."""
    del_sdk = {i for i in importados(archivo) if i.startswith("firebase/")}

    if not del_sdk:
        return

    assert archivo.name == "firebase.ts", (
        f"{archivo.relative_to(RAIZ)} importa {sorted(del_sdk)} directamente. "
        "El SDK entra por src/firebase.ts, que es donde la frontera se puede leer."
    )


@pytest.mark.parametrize("archivo", FUENTES, ids=lambda p: p.name)
def test_nadie_llama_a_fetch_por_fuera_del_cliente_de_api(archivo: Path) -> None:
    """Un `fetch` suelto se olvida del `Authorization`.

    Y no falla de manera visible en desarrollo —el proxy puede ser permisivo—
    sino con 401 en producción. Con una sola puerta, olvidarse no es posible.
    """
    if archivo.name == "api.ts":
        return

    texto = archivo.read_text(encoding="utf-8")
    lineas = [
        numero
        for numero, linea in enumerate(texto.splitlines(), start=1)
        if re.search(r"\bfetch\s*\(", linea) and not linea.strip().startswith(("*", "//"))
    ]

    assert not lineas, (
        f"{archivo.relative_to(RAIZ)} llama a fetch() en las líneas {lineas}. "
        "Las peticiones van por src/api.ts, que es el único lugar donde se arma "
        "la cabecera de identidad."
    )


# ─────────────────────────────────────────────────────────────────────────────
# El scaffold
# ─────────────────────────────────────────────────────────────────────────────


def test_el_build_sale_donde_firebase_lo_busca() -> None:
    """**Ya se cometió una vez en este repositorio un error de este tipo.**

    `firebase.json` declara `apps/web/dist` como `public`. Si el build saliera a
    otro lado, el despliegue publicaría una carpeta vacía sin fallar.
    """
    firebase = json.loads((RAIZ / "firebase.json").read_text(encoding="utf-8"))
    declarado = firebase["hosting"]["public"]

    configuracion = (WEB / "vite.config.ts").read_text(encoding="utf-8")

    assert declarado == "apps/web/dist"
    assert 'outDir: "dist"' in configuracion


def test_el_build_verifica_los_tipos() -> None:
    """Vite transpila sin chequear tipos: `vite build` solo no detecta nada.

    Sin `tsc` en el script, `strict` es decoración.
    """
    assert "tsc" in PAQUETE["scripts"]["build"]


def test_las_dependencias_estan_fijadas() -> None:
    """Un rango en un proyecto de referencia hace que dos clones instalen cosas
    distintas, y el que falla no es el que se puede reproducir."""
    versiones = {**PAQUETE["dependencies"], **PAQUETE["devDependencies"]}
    flexibles = {n: v for n, v in versiones.items() if not re.fullmatch(r"\d+\.\d+\.\d+", v)}

    assert not flexibles, f"versiones sin fijar: {flexibles}"


def test_el_ejemplo_de_entorno_no_trae_claves() -> None:
    """La configuración de Firebase no es secreta, pero el archivo de ejemplo es
    una plantilla: si alguien la completa y la commitea, el diff no lo delata."""
    ejemplo = (WEB / ".env.example").read_text(encoding="utf-8")

    con_valor = [
        linea
        for linea in ejemplo.splitlines()
        if linea.startswith("VITE_FIREBASE_API_KEY=") and linea.split("=", 1)[1].strip()
    ]

    assert not con_valor, "el .env.example trae una API key cargada"


def test_el_html_declara_el_idioma_del_dominio() -> None:
    """El dominio es normativa técnica en español. Declarar `en` haría que un
    lector de pantalla pronuncie mal cada término."""
    assert '<html lang="es">' in (WEB / "index.html").read_text(encoding="utf-8")


def test_la_consola_no_se_indexa() -> None:
    """Muestra datos de activos y propuestas sobre equipos en servicio."""
    assert "noindex" in (WEB / "index.html").read_text(encoding="utf-8")
