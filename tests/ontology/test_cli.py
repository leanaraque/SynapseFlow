"""La CLI tiene que funcionar donde se la documenta.

El README la ofrece como lo primero que funciona después de clonar, y el job de
gobernanza del CI vuelca su salida al resumen del build. Los dos usos redirigen
la salida, que es justo donde fallaba: en Windows `sys.stdout` usa cp1252 cuando
no es una consola, y los caracteres de dibujo de las tablas no se pueden
codificar ahí.

`synapseflow ontology validate > salida.txt` terminaba en UnicodeEncodeError con
código 1 y el archivo cortado a la mitad. En Linux no pasaba, así que el CI no
lo detectaba.

Ver docs/plan/00-convenciones.md § Errores ya cometidos
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]

COMANDOS = [
    ["ontology", "validate"],
    ["ontology", "roles"],
    ["ontology", "graph"],
    ["ontology", "tools", "--role", "tecnico"],
    ["ontology", "tools", "--role", "auditor"],
]


def _correr(argumentos: list[str], destino: Path, codificacion: str) -> subprocess.CompletedProcess:
    """Corre la CLI en un proceso aparte, con la salida redirigida a un archivo.

    Tiene que ser un subproceso: el bug depende de cómo Python resuelve la
    codificación de `sys.stdout` al arrancar, y eso no se puede reproducir
    capturando la salida dentro del mismo intérprete.
    """
    entorno = {
        **dict(__import__("os").environ),
        # Fuerza la codificación que Windows elige por defecto al redirigir.
        "PYTHONIOENCODING": codificacion,
    }
    with destino.open("wb") as salida:
        return subprocess.run(
            [sys.executable, "-m", "synapseflow.cli", *argumentos],
            stdout=salida,
            stderr=subprocess.PIPE,
            cwd=RAIZ,
            env=entorno,
            check=False,
        )


@pytest.mark.parametrize("argumentos", COMANDOS, ids=lambda a: " ".join(a))
@pytest.mark.parametrize("codificacion", ["utf-8", "cp1252"])
def test_la_cli_no_falla_con_la_salida_redirigida(
    argumentos: list[str], codificacion: str, tmp_path: Path
) -> None:
    """El caso de cp1252 es el que fallaba: es el de Windows redirigiendo."""
    destino = tmp_path / "salida.txt"
    resultado = _correr(argumentos, destino, codificacion)

    assert resultado.returncode == 0, (
        f"`synapseflow {' '.join(argumentos)}` falló con codificación "
        f"{codificacion}:\n{resultado.stderr.decode('utf-8', 'replace')}"
    )
    assert destino.stat().st_size > 0, "la salida quedó vacía"


def test_la_salida_completa_llega_al_archivo(tmp_path: Path) -> None:
    """No alcanza con no fallar: el contenido tiene que estar entero.

    El bug truncaba el archivo en el primer carácter no codificable, así que un
    test que solo mirara el código de salida podría pasar con media tabla.
    """
    destino = tmp_path / "validate.txt"
    _correr(["ontology", "validate"], destino, "cp1252")

    # Se lee como UTF-8 porque eso es precisamente lo que el arreglo garantiza:
    # la CLI reconfigura su salida aunque el entorno pida cp1252. Las aserciones
    # usan fragmentos ASCII para que sigan valiendo si la reconfiguración no
    # fuera posible y la salida cayera a los símbolos de reemplazo.
    texto = destino.read_text(encoding="utf-8", errors="replace")

    assert "oil_and_gas_asset_integrity" in texto
    assert "solicitar_parada_equipo" in texto, "falta la tabla de acciones irreversibles"
    assert "inspector_legajo" in texto, "falta la tabla de campos que no salen del perimetro"
    assert "invariantes de gobernanza se sostienen" in texto, (
        "la salida se cortó antes del veredicto final"
    )


def test_un_rol_inexistente_falla_con_codigo_dos(tmp_path: Path) -> None:
    """Distinguible de un error de dominio, que sale con 1."""
    resultado = _correr(["ontology", "tools", "--role", "inexistente"], tmp_path / "x.txt", "utf-8")
    assert resultado.returncode == 2
    assert b"no existe el rol" in resultado.stderr
