"""El workflow de evals tiene que hacer lo que dice que hace.

Un CI de regresión es una promesa: «un PR que empeore la calidad no se mergea».
La promesa se rompe en silencio de tres formas, y las tres se verifican acá:

1. El corredor se invoca sin `--comparar-linea-base`, así que nunca compara.
2. El código de salida del corredor se pierde —por un `tee`, por ejemplo— y el
   job sale verde con la regresión adentro.
3. El workflow no corre en los PR que tocan lo que importa.

Ver docs/plan/fases/F8-evals.md § F8.4
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "evals.yml"


@pytest.fixture(scope="module")
def definicion() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def texto() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pasos(definicion: dict[str, Any]) -> list[dict[str, Any]]:
    return definicion["jobs"]["evaluar"]["steps"]


# ─────────────────────────────────────────────────────────────────────────────
# La promesa: compara y falla
# ─────────────────────────────────────────────────────────────────────────────


def test_el_corredor_se_invoca_comparando_contra_la_linea_base(texto: str) -> None:
    """**Sin esta bandera el job corre las evals y no compara nada.**

    Saldría verde siempre, y nadie notaría que la red de contención no existe.
    """
    assert "--comparar-linea-base" in texto


def test_el_codigo_de_salida_del_corredor_no_se_pierde(texto: str) -> None:
    """`| tee` devuelve el código de `tee`, no el del comando.

    Es la forma más fácil de tener un CI que reporta la regresión en el log y
    sale en verde.
    """
    assert "PIPESTATUS" in texto, (
        "el corredor se canaliza por `tee` sin rescatar su código de salida"
    )


def test_hay_un_paso_que_falla_ante_regresion(pasos: list[dict[str, Any]]) -> None:
    fallar = [p for p in pasos if "regresión" in (p.get("name") or "").lower()]
    assert fallar, "ningún paso falla ante regresión"
    assert "exit 1" in fallar[0]["run"]


def test_se_distinguen_los_tres_codigos_de_salida(texto: str) -> None:
    """0 sin regresión, 1 regresión, 2 corrida rota.

    Confundir 2 con 1 mandaría a alguien a mirar prompts cuando el problema es
    que las credenciales no funcionan.
    """
    assert "Regresión respecto de la línea base" in texto
    assert "Ningún caso se pudo ejecutar" in texto


# ─────────────────────────────────────────────────────────────────────────────
# Cuándo corre
# ─────────────────────────────────────────────────────────────────────────────


def test_corre_en_los_pull_requests(definicion: dict[str, Any]) -> None:
    # PyYAML interpreta `on:` como el booleano True. Es el gotcha clásico de los
    # workflows de GitHub leídos con YAML 1.1.
    disparadores = definicion.get("on") or definicion.get(True)
    assert "pull_request" in disparadores


@pytest.mark.parametrize(
    "ruta",
    [
        "packages/synapseflow/agents/**",
        "packages/synapseflow/rag/**",
        "packages/synapseflow/llm/**",
        "evals/**",
        "data/corpus/**",
    ],
)
def test_se_dispara_al_tocar_lo_que_cambia_la_calidad(
    definicion: dict[str, Any], ruta: str
) -> None:
    """Es el único job que consume cuota real.

    Limitarlo por rutas es lo que hace que no se pague una corrida por cambiar
    el README — y dejar afuera una ruta que sí importa es peor: el PR que rompe
    la calidad pasa sin evaluarse.
    """
    disparadores = definicion.get("on") or definicion.get(True)
    assert ruta in disparadores["pull_request"]["paths"]


def test_tiene_techo_de_tiempo(definicion: dict[str, Any]) -> None:
    """Un proveedor que no responde no puede dejar el job consumiendo minutos."""
    assert definicion["jobs"]["evaluar"]["timeout-minutes"] <= 30


# ─────────────────────────────────────────────────────────────────────────────
# Autenticación sin claves
# ─────────────────────────────────────────────────────────────────────────────


def test_autentica_por_workload_identity_y_no_por_clave(texto: str) -> None:
    """**La organización prohíbe las claves descargables, y hace bien.**

    Una clave en los secretos de GitHub es una credencial de larga vida que hay
    que rotar y que nadie rota.
    """
    assert "google-github-actions/auth" in texto
    assert "workload_identity_provider" in texto
    assert "credentials_json" not in texto, "está usando una clave en lugar de WIF"


def test_pide_el_permiso_de_token_oidc(definicion: dict[str, Any]) -> None:
    """Sin `id-token: write`, GitHub no emite el token que WIF intercambia."""
    assert definicion["permissions"]["id-token"] == "write"


def test_se_saltea_si_la_infraestructura_no_esta_configurada(texto: str) -> None:
    """Un CI que falla por infraestructura que nadie configuró todavía enseña a
    ignorar el rojo, y entonces deja de proteger de lo que sí importa."""
    assert "preflight" in texto
    assert "no configuradas" in texto


def test_el_encabezado_documenta_como_configurar_wif(texto: str) -> None:
    """Un job que se saltea sin decir qué falta se saltea para siempre."""
    assert "workload-identity-pools create" in texto
    assert "WIF_PROVIDER" in texto


# ─────────────────────────────────────────────────────────────────────────────
# El reporte
# ─────────────────────────────────────────────────────────────────────────────


def test_publica_el_reporte_en_el_resumen_del_job(texto: str) -> None:
    """El detalle por caso tiene que estar donde alguien lo va a leer.

    Enterrado en el log de un paso, no lo lee nadie.
    """
    assert "GITHUB_STEP_SUMMARY" in texto


def test_el_reporte_se_publica_aunque_la_corrida_falle(pasos: list[dict[str, Any]]) -> None:
    """Es cuando más se lo necesita."""
    publicar = [p for p in pasos if "Publicar" in (p.get("name") or "")]
    assert publicar
    assert "always()" in publicar[0]["if"]
