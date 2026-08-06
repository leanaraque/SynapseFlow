"""El plan de trabajo tiene que ser internamente consistente.

Este plan lo va a seguir alguien —o algo— sin contexto previo. Si el detector de
estado menciona un commit que ningún documento explica, o si un documento enlaza
a un archivo que no existe, esa persona se queda trabada sin manera de saber qué
hacer.

Estos tests no verifican el código del proyecto: verifican que las instrucciones
para construirlo sean seguibles.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from scripts.estado import COMMITS, FASES, RAIZ


def test_cada_fase_tiene_su_documento() -> None:
    faltantes = [
        f"{fid} → {f.documento}" for fid, f in FASES.items() if not (RAIZ / f.documento).is_file()
    ]
    assert not faltantes, f"fases sin documento: {faltantes}"


def test_cada_commit_esta_documentado() -> None:
    """Un commit sin instrucciones es un commit que nadie puede hacer."""
    sin_documentar = []
    for c in COMMITS:
        doc = RAIZ / FASES[c.fase].documento
        if not doc.is_file():
            continue
        if f"## {c.id} ·" not in doc.read_text(encoding="utf-8"):
            sin_documentar.append(f"{c.id} (falta '## {c.id} ·' en {doc.name})")
    assert not sin_documentar, f"commits sin sección propia: {sin_documentar}"


def test_los_ids_de_commit_son_unicos() -> None:
    ids = [c.id for c in COMMITS]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    assert not repetidos, f"ids repetidos: {repetidos}"


def test_toda_fase_referenciada_existe() -> None:
    huerfanos = [c.id for c in COMMITS if c.fase not in FASES]
    assert not huerfanos, f"commits que apuntan a una fase inexistente: {huerfanos}"


def test_las_dependencias_entre_fases_son_validas() -> None:
    problemas = []
    for fid, fase in FASES.items():
        for dep in fase.depende_de:
            if dep not in FASES:
                problemas.append(f"{fid} depende de {dep}, que no existe")
            elif fid in FASES[dep].depende_de:
                problemas.append(f"dependencia circular entre {fid} y {dep}")
    assert not problemas, problemas


def test_todo_commit_tiene_senal_y_verificacion() -> None:
    """Sin señal nunca se detecta como hecho; sin verificación no se sabe si salió bien."""
    problemas = []
    for c in COMMITS:
        if not c.senales:
            problemas.append(f"{c.id} no declara señal")
        if not c.verificar:
            problemas.append(f"{c.id} no declara cómo verificarlo")
    assert not problemas, problemas


@pytest.mark.parametrize(
    "documento",
    sorted((RAIZ / "docs" / "plan").rglob("*.md")),
    ids=lambda p: str(p.relative_to(RAIZ / "docs" / "plan")),
)
def test_los_enlaces_relativos_resuelven(documento: Path) -> None:
    rotos = [
        destino
        for destino in re.findall(r"\]\((\.\.?/[^)#]+)\)", documento.read_text(encoding="utf-8"))
        if not (documento.parent / destino).resolve().exists()
    ]
    assert not rotos, f"enlaces rotos en {documento.name}: {rotos}"
