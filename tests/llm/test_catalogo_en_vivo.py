"""El catálogo tiene que nombrar modelos que existan de verdad.

Un modelo retirado no rompe nada al cargar el catálogo: `models.yaml` sigue
siendo YAML válido y el registry lo resuelve sin quejarse. Rompe en la primera
llamada, en producción.

Pasó exactamente eso. El catálogo declaraba `gemini-2.5-flash`,
`gemini-2.5-pro` y `text-embedding-004`, y con una clave nueva los tres
responden 404 o 429. Nada en la suite lo detectaba, porque todo lo demás corre
contra el modelo falso a propósito.

Estos tests son el único punto donde el proyecto toca un proveedor real. Llevan
`live_llm`, así que **no corren en CI** y no corren por defecto:

    pytest -m live_llm

Consumen cuota. Es el precio de saber que el catálogo no miente.

Ver docs/plan/00-convenciones.md § 6 y docs/adr/0004-gateway-provider-agnostic.md
"""

from __future__ import annotations

import pytest

from synapseflow.config import Provider, get_settings
from synapseflow.llm import registry

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        not (get_settings().google_api_key or ""),
        reason="sin GOOGLE_API_KEY en el entorno: no hay proveedor al que preguntarle",
    ),
]

PERFILES_DE_CHAT = ("router", "synthesis", "verifier")


@pytest.mark.parametrize("perfil", PERFILES_DE_CHAT)
def test_el_modelo_de_cada_perfil_de_chat_responde(perfil: str) -> None:
    """Resuelve el perfil como lo haría el gateway y hace una llamada real."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    spec = registry.resolver(Provider.GEMINI, perfil)
    modelo = ChatGoogleGenerativeAI(
        model=spec.modelo,
        google_api_key=get_settings().google_api_key,
    )
    respuesta = modelo.invoke("Respondé únicamente con la palabra OK.")

    assert str(respuesta.content).strip(), (
        f"el perfil '{perfil}' resuelve a '{spec.modelo}', que no devolvió texto"
    )
    assert respuesta.usage_metadata, (
        "sin usage_metadata la contabilidad de costos de F1.4 no tiene qué contar"
    )


def test_el_modelo_de_embeddings_devuelve_la_dimension_del_indice() -> None:
    """La comprobación que evita reindexar el corpus entero.

    El registry ya exige que `dimensions` coincida con `firestore.indexes.json`,
    pero eso solo verifica lo que el catálogo *declara*. Acá se comprueba contra
    el proveedor: `gemini-embedding-001` devuelve 3072 por defecto y hay que
    pedirle explícitamente la dimensionalidad del índice.
    """
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    spec = registry.resolver(Provider.GEMINI, "embedding")
    esperadas = registry.dimensiones_del_indice()
    assert spec.dimensiones == esperadas

    embeddings = GoogleGenerativeAIEmbeddings(
        model=f"models/{spec.modelo}",
        google_api_key=get_settings().google_api_key,
        output_dimensionality=spec.dimensionalidad_pedida,
    )
    vector = embeddings.embed_query("espesor por debajo del mínimo requerido")

    assert len(vector) == esperadas, (
        f"'{spec.modelo}' devolvió {len(vector)} dimensiones y el índice "
        f"vectorial está creado para {esperadas}. Indexar así obliga a recrear "
        "el índice y reindexar el corpus completo."
    )


def test_pedir_la_dimensionalidad_por_defecto_no_entraria_en_el_indice() -> None:
    """El caso negativo, que es el que justifica el parámetro.

    Sin `output_dimensionality`, el modelo devuelve su tamaño nativo. Si algún
    día coincidiera con el índice, este test lo avisaría: el parámetro habría
    dejado de hacer falta.
    """
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    spec = registry.resolver(Provider.GEMINI, "embedding")
    if spec.dimensionalidad_pedida is None:
        pytest.skip("el catálogo no pide una dimensionalidad explícita")

    nativo = GoogleGenerativeAIEmbeddings(
        model=f"models/{spec.modelo}",
        google_api_key=get_settings().google_api_key,
    )
    assert len(nativo.embed_query("x")) != registry.dimensiones_del_indice(), (
        "el modelo ya devuelve la dimensión del índice por su cuenta: "
        "`output_dimensionality` dejó de ser necesario y conviene quitarlo"
    )
