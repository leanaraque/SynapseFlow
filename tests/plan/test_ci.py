"""El CI no llama a proveedores reales.

Los tests `live_llm` consumen cuota y necesitan una clave de verdad. El paso de
integración del CI define `GOOGLE_API_KEY=clave-de-test`, así que si esos tests
se cuelan, llaman a Gemini con una clave de mentira y fallan. Pasó: el CI estuvo
en rojo por eso desde que el paso unitario dejó de fallar antes.

La trampa está en cómo combina pytest los filtros: un `-m` en la línea de
comandos **reemplaza** al de `addopts`, no se suma. Por eso cada invocación del
workflow tiene que excluirlos por su cuenta, o no pasar `-m` en absoluto.
"""

from __future__ import annotations

import re
import shlex
import tomllib

from scripts.estado import RAIZ

WORKFLOWS = sorted((RAIZ / ".github" / "workflows").glob("*.yml"))


def _invocaciones_de_pytest() -> list[tuple[str, str]]:
    """Cada `pytest ...` que aparece en un workflow, con el archivo donde está."""
    encontradas = []
    for workflow in WORKFLOWS:
        for linea in workflow.read_text(encoding="utf-8").splitlines():
            codigo = linea.split("#", 1)[0]
            for coincidencia in re.finditer(r"\bpytest\b[^\"\n]*(?:\"[^\"]*\"[^\"\n]*)*", codigo):
                encontradas.append((workflow.name, coincidencia.group(0).strip()))
    return encontradas


def test_por_defecto_pytest_no_corre_los_tests_en_vivo() -> None:
    configuracion = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = shlex.split(configuracion["tool"]["pytest"]["ini_options"]["addopts"])

    filtro = addopts[addopts.index("-m") + 1]
    assert filtro == "not live_llm"


def test_hay_invocaciones_que_revisar() -> None:
    """El control del de abajo: si el regex dejara de encontrarlas, pasaría solo."""
    assert len(_invocaciones_de_pytest()) >= 2


def test_ninguna_invocacion_del_ci_corre_los_tests_en_vivo() -> None:
    sin_exclusion = [
        f"{archivo}: {comando}"
        for archivo, comando in _invocaciones_de_pytest()
        if " -m " in f" {comando} " and "not live_llm" not in comando
    ]
    assert not sin_exclusion, (
        "estas invocaciones pasan `-m` sin excluir `live_llm`, y un `-m` en la línea "
        f"de comandos reemplaza al de addopts: {sin_exclusion}"
    )
