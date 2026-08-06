"""Contrato del registry de modelos.

Lo que se verifica no es que el YAML se lea —eso lo hace Pydantic— sino las tres
decisiones que el registry toma por el resto de la plataforma:

1. Qué modelo concreto le toca a cada perfil de tarea.
2. Cuánto cuesta una llamada, que es lo que alimenta el panel de costos.
3. **Que un modelo de embeddings incompatible con el índice no se pueda
   resolver.** Es la validación cara: un índice vectorial de Firestore fija su
   dimensión al crearse, y descubrir el desajuste a mitad de la ingesta obliga a
   recrear el índice y reindexar el corpus entero.

Ver docs/plan/fases/F1-gateway.md § F1.1
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest

from synapseflow.config import Provider, reset_settings_cache
from synapseflow.llm import registry


@pytest.fixture
def azure_configurado(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Azure resuelve sus modelos por nombre de deployment, que sale del entorno."""
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "mi-deployment-gpt41")
    monkeypatch.setenv("AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT", "mi-deployment-embeddings")
    reset_settings_cache()
    yield
    reset_settings_cache()


# ─────────────────────────────────────────────────────────────────────────────
# Resolución por perfil y proveedor
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("perfil", ["router", "synthesis", "verifier"])
@pytest.mark.parametrize("proveedor", ["gemini", "openai", "anthropic"])
def test_los_perfiles_de_chat_resuelven(proveedor: str, perfil: str) -> None:
    spec = registry.resolver(proveedor, perfil)
    assert spec.modelo, f"{proveedor}/{perfil} resolvió a un modelo vacío"
    assert spec.proveedor == proveedor
    assert spec.perfil == perfil


def test_el_perfil_de_embedding_resuelve_para_gemini() -> None:
    spec = registry.resolver(Provider.GEMINI, "embedding")
    assert spec.dimensiones == registry.dimensiones_del_indice()


def test_anthropic_cae_a_gemini_para_embeddings() -> None:
    """Anthropic no tiene modelo de embeddings propio.

    La caída está declarada en el YAML (`embedding_fallback`) y no cableada en el
    código: si mañana Anthropic publica uno, se cambia el catálogo.
    """
    spec = registry.resolver(Provider.ANTHROPIC, "embedding")
    assert spec.proveedor == "gemini", "la caída debería resolver contra gemini"
    assert spec.dimensiones == registry.dimensiones_del_indice()


def test_azure_resuelve_el_modelo_desde_el_entorno(azure_configurado: None) -> None:
    """En Azure el id es el nombre del deployment, que define quien administra."""
    spec = registry.resolver(Provider.AZURE_OPENAI, "router")
    assert spec.modelo == "mi-deployment-gpt41"


def test_azure_falla_con_mensaje_util_si_falta_el_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_CHAT_DEPLOYMENT", raising=False)
    reset_settings_cache()
    try:
        with pytest.raises(registry.RegistryError, match="AZURE_OPENAI_CHAT_DEPLOYMENT"):
            registry.resolver(Provider.AZURE_OPENAI, "router")
    finally:
        reset_settings_cache()


def test_un_perfil_inexistente_falla() -> None:
    with pytest.raises(registry.RegistryError, match="perfil desconocido"):
        registry.resolver(Provider.GEMINI, "traduccion")


def test_un_proveedor_inexistente_falla() -> None:
    with pytest.raises(registry.RegistryError, match="no está en"):
        registry.resolver("cohere", "router")


# ─────────────────────────────────────────────────────────────────────────────
# La validación que justifica el módulo
# ─────────────────────────────────────────────────────────────────────────────


def test_un_desajuste_de_dimensiones_falla_al_resolver() -> None:
    """OpenAI devuelve 1536 dimensiones y el índice está creado para 768.

    Tiene que fallar acá, al resolver, y no durante la ingesta. El mensaje debe
    decir qué implica el desajuste: recrear el índice y reindexar el corpus.
    """
    with pytest.raises(registry.RegistryError) as excinfo:
        registry.resolver(Provider.OPENAI, "embedding")

    mensaje = str(excinfo.value)
    assert "1536" in mensaje and str(registry.dimensiones_del_indice()) in mensaje
    assert "reindexar" in mensaje, (
        "el mensaje tiene que decir qué cuesta el desajuste, no solo que existe"
    )


def test_la_dimension_sale_del_archivo_de_indices_desplegado() -> None:
    """Y no de una constante, que podría desincronizarse con lo desplegado."""
    assert registry.dimensiones_del_indice() == 768


def test_el_catalogo_y_los_indices_declaran_la_misma_dimension() -> None:
    """`models.yaml` y `firestore.indexes.json` dicen lo mismo en dos lugares.

    Que carguen sin error ya significa que coinciden: la comprobación vive en la
    carga del catálogo. Este test la deja explícita.
    """
    registry.limpiar_cache()
    assert registry.dimensiones_del_indice() == 768
    registry.resolver(Provider.GEMINI, "embedding")


# ─────────────────────────────────────────────────────────────────────────────
# Costos
# ─────────────────────────────────────────────────────────────────────────────


def test_el_costo_se_calcula_por_millon_de_tokens() -> None:
    spec = registry.ModelSpec(
        proveedor="test",
        perfil="router",
        modelo="m",
        entrada_por_1m=1.0,
        salida_por_1m=10.0,
    )
    assert registry.costo_de(spec, 1_000_000, 0) == pytest.approx(1.0)
    assert registry.costo_de(spec, 0, 1_000_000) == pytest.approx(10.0)
    assert registry.costo_de(spec, 500_000, 100_000) == pytest.approx(0.5 + 1.0)
    assert registry.costo_de(spec, 0, 0) == 0.0


def test_el_costo_no_se_redondea_a_cero() -> None:
    """Una llamada corta cuesta millonésimos de dólar y hay que poder sumarla.

    Redondear en el cálculo colapsaría el costo de cada llamada a cero, y el
    total de una sesión con eso sería cero: el panel de costos mostraría que el
    sistema es gratis.
    """
    spec = registry.resolver(Provider.GEMINI, "router")
    costo = registry.costo_de(spec, 350, 120)
    assert costo > 0, "una llamada real no puede costar exactamente cero"
    assert costo < 0.01


def test_un_conteo_negativo_de_tokens_es_un_error() -> None:
    spec = registry.resolver(Provider.GEMINI, "router")
    with pytest.raises(registry.RegistryError, match="negativo"):
        registry.costo_de(spec, -1, 0)


def test_el_perfil_router_es_mas_barato_que_synthesis() -> None:
    """Es la premisa de todo el ruteo por costo del ADR-0004.

    El supervisor usa `router` en cada turno y `synthesis` solo para redactar. Si
    el catálogo invirtiera esa relación, la decisión de diseño quedaría sin
    efecto y nadie lo notaría hasta ver la factura.
    """
    for proveedor in ("gemini", "openai", "anthropic"):
        router = registry.resolver(proveedor, "router")
        synthesis = registry.resolver(proveedor, "synthesis")
        assert router.entrada_por_1m <= synthesis.entrada_por_1m, (
            f"en {proveedor}, el perfil router no es más barato que synthesis"
        )
        assert router.salida_por_1m <= synthesis.salida_por_1m, (
            f"en {proveedor}, el perfil router no es más barato que synthesis"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Política declarada
# ─────────────────────────────────────────────────────────────────────────────


def test_todos_los_proveedores_declaran_su_politica_de_entrenamiento() -> None:
    """`SYNAPSEFLOW_ENFORCE_ZERO_TRAINING` no puede aplicarse sobre un dato ausente."""
    for proveedor in registry.proveedores():
        assert isinstance(registry.declara_zero_training(proveedor), bool)


def test_verificado_el_es_una_fecha_pasada() -> None:
    assert registry.verificado_el() <= dt.date.today()
