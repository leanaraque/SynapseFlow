"""Contrato del gateway de modelos.

Lo que se verifica no es que se pueda construir un `ChatGoogleGenerativeAI` —eso
lo hace LangChain— sino las garantías que el gateway existe para dar:

1. Que se pida un **perfil de tarea** y nunca un nombre de modelo.
2. Que las verificaciones de gobernanza ocurran **al construir**, no en la
   primera llamada con el usuario esperando.
3. Que un proveedor sin SDK, sin credenciales o sin política declarada falle con
   un mensaje que diga qué hacer.

Ninguno de estos tests sale a la red: los adapters se instancian con credenciales
de mentira, que es suficiente porque construir un cliente no lo usa.

Ver docs/plan/fases/F1-gateway.md § F1.3
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from synapseflow.config import CredencialesFaltantesError, Provider, Settings
from synapseflow.llm import registry
from synapseflow.llm.fake import FakeChatModel, FakeEmbeddings, Respuesta
from synapseflow.llm.gateway import (
    PERFILES_DE_CHAT,
    Gateway,
    GatewayError,
    PoliticaVioladaError,
    ProveedorNoInstaladoError,
)


def settings_de(**overrides: Any) -> Settings:
    """`Settings` con credenciales de mentira para todos los proveedores.

    Se parte de un entorno completo y el test quita lo que quiere ver faltar. Al
    revés —partir de vacío y agregar— un test que se olvida de poner una clave
    pasa por la razón equivocada.

    Los valores se pasan por su alias porque es como `Settings` los declara, y
    los argumentos de construcción tienen prioridad sobre el entorno y el `.env`.
    """
    base: dict[str, Any] = {
        "SYNAPSEFLOW_PROVIDER": Provider.FAKE,
        "GOOGLE_API_KEY": "clave-de-mentira",
        "OPENAI_API_KEY": "clave-de-mentira",
        "ANTHROPIC_API_KEY": "clave-de-mentira",
        "AZURE_OPENAI_API_KEY": "clave-de-mentira",
        "AZURE_OPENAI_ENDPOINT": "https://ejemplo.openai.azure.com",
        "AZURE_OPENAI_CHAT_DEPLOYMENT": "deployment-chat",
        "AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT": "deployment-embeddings",
        "SYNAPSEFLOW_FALLBACK_PROVIDER": None,
    }
    base.update(overrides)
    return Settings(**base)


# ─────────────────────────────────────────────────────────────────────────────
# Se pide un perfil, nunca un modelo
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("perfil", PERFILES_DE_CHAT)
def test_cada_perfil_de_chat_devuelve_un_modelo(perfil: str) -> None:
    """Los tres perfiles resuelven contra el proveedor por defecto del demo."""
    gateway = Gateway(Provider.GEMINI, settings=settings_de())
    modelo = gateway.chat(perfil)  # type: ignore[arg-type]

    assert isinstance(modelo, BaseChatModel), (
        "sin respaldo configurado, el gateway tiene que devolver un BaseChatModel: "
        "es lo que create_agent y los middlewares esperan"
    )


def test_el_modelo_devuelto_es_el_que_declara_el_catalogo() -> None:
    """El gateway no elige: resuelve por el registry, que lee `models.yaml`."""
    gateway = Gateway(Provider.GEMINI, settings=settings_de())
    esperado = registry.resolver(Provider.GEMINI, "synthesis").modelo

    assert gateway.chat("synthesis").model == esperado  # type: ignore[union-attr]


def test_pedir_embedding_como_perfil_de_chat_falla_con_mensaje_util() -> None:
    """`embedding` es un perfil del catálogo pero no de `chat()`.

    Sin este chequeo, el registry resolvería un modelo de embeddings y el
    adapter lo instanciaría como modelo de chat: el error aparecería recién en la
    primera invocación, dicho por el proveedor y sin relación aparente.
    """
    gateway = Gateway(Provider.GEMINI, settings=settings_de())
    with pytest.raises(GatewayError, match="no es un perfil de chat"):
        gateway.chat("embedding")  # type: ignore[arg-type]


def test_los_modelos_se_cachean_por_perfil() -> None:
    """Cada adapter abre un cliente HTTP; uno por turno desperdicia conexiones."""
    gateway = Gateway(Provider.GEMINI, settings=settings_de())
    assert gateway.chat("router") is gateway.chat("router")
    assert gateway.chat("router") is not gateway.chat("synthesis")


def test_la_temperatura_es_cero_en_todos_los_perfiles() -> None:
    """Incluido el de redacción.

    La respuesta cita normativa que un ingeniero firma: dos corridas de la misma
    pregunta deberían decir lo mismo. Además la suite de evals de F8 mide
    regresión entre corridas, y con temperatura el ruido del muestreo se
    confundiría con el efecto de un cambio de prompt.

    **Lo que este test verifica es que se pide, no que se obtenga.**
    `gemini-3.5-flash-lite` avisa por `UserWarning` que usa sampling fijo y que
    ignora el parámetro. No hay forma de verificar el determinismo desde acá sin
    salir a la red; se mide en las evals de F8.
    """
    gateway = Gateway(Provider.GEMINI, settings=settings_de())
    for perfil in PERFILES_DE_CHAT:
        assert gateway.chat(perfil).temperature == 0.0  # type: ignore[arg-type,union-attr]


# ─────────────────────────────────────────────────────────────────────────────
# El proveedor de tests recorre el mismo camino que el real
# ─────────────────────────────────────────────────────────────────────────────


def test_el_proveedor_falso_devuelve_el_modelo_falso() -> None:
    gateway = Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.FAKE))
    assert isinstance(gateway.chat("router"), FakeChatModel)


def test_el_proveedor_falso_devuelve_el_modelo_programado() -> None:
    """Un test tiene que poder ejercitar el gateway y recibir su propio modelo.

    Si el gateway construyera siempre uno nuevo, testear el grafo obligaría a
    saltear el gateway, y el camino ejercitado no sería el de producción.
    """
    programado = FakeChatModel(por_defecto=Respuesta(texto="respuesta programada"))
    gateway = Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=programado)

    assert gateway.chat("synthesis") is programado


def test_el_proveedor_falso_no_necesita_credenciales() -> None:
    """No sale del proceso: exigirle una clave sería pedir algo que no usa."""
    gateway = Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.FAKE, GOOGLE_API_KEY=None))
    assert isinstance(gateway.chat("router"), FakeChatModel)


# ─────────────────────────────────────────────────────────────────────────────
# Las verificaciones ocurren al construir
# ─────────────────────────────────────────────────────────────────────────────


def test_sin_credenciales_falla_al_construir_y_no_en_la_primera_llamada() -> None:
    """La diferencia importa: al arrancar la API esto pasa en el startup.

    En la primera llamada pasaría con el usuario esperando y el traceback
    enterrado en el streaming de la respuesta.
    """
    with pytest.raises(CredencialesFaltantesError, match="GOOGLE_API_KEY"):
        Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.GEMINI, GOOGLE_API_KEY=None))


def test_azure_nombra_todas_las_variables_que_le_faltan() -> None:
    """Azure necesita tres. Reportar de a una obliga a tres corridas para saberlo."""
    with pytest.raises(CredencialesFaltantesError) as excinfo:
        Gateway(
            settings=settings_de(
                SYNAPSEFLOW_PROVIDER=Provider.AZURE_OPENAI,
                AZURE_OPENAI_API_KEY=None,
                AZURE_OPENAI_ENDPOINT=None,
            )
        )
    mensaje = str(excinfo.value)
    assert "AZURE_OPENAI_API_KEY" in mensaje and "AZURE_OPENAI_ENDPOINT" in mensaje


def test_un_proveedor_sin_sdk_dice_que_instalar() -> None:
    """`langchain-anthropic` no está en las dependencias del proyecto.

    Anthropic figura en el catálogo como adapter alternativo, así que el caso es
    real y no hipotético: quien lo elija tiene que leer qué instalar, no un
    ImportError pelado desde las entrañas del gateway.
    """
    gateway = Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.ANTHROPIC))
    with pytest.raises(ProveedorNoInstaladoError, match="langchain-anthropic"):
        gateway.chat("router")


# ─────────────────────────────────────────────────────────────────────────────
# Política de zero-training
# ─────────────────────────────────────────────────────────────────────────────


def test_un_proveedor_que_no_declara_zero_training_es_rechazado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La bandera existía en la configuración sin que nada la leyera.

    Una bandera de gobernanza que no se aplica es peor que no tenerla: alguien la
    ve en `true` y concluye que la garantía está.
    """
    monkeypatch.setattr(registry, "declara_zero_training", lambda _p: False)

    with pytest.raises(PoliticaVioladaError, match="ENFORCE_ZERO_TRAINING"):
        Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.GEMINI))


def test_la_politica_se_puede_desactivar_de_forma_explicita(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Desactivarla es una decisión que alguien toma, no un default silencioso."""
    monkeypatch.setattr(registry, "declara_zero_training", lambda _p: False)

    gateway = Gateway(
        settings=settings_de(
            SYNAPSEFLOW_PROVIDER=Provider.GEMINI,
            SYNAPSEFLOW_ENFORCE_ZERO_TRAINING=False,
        )
    )
    assert gateway.chat("router") is not None


def test_todos_los_proveedores_del_catalogo_pasan_la_politica() -> None:
    """Si alguno no la declarara, elegirlo fallaría al arrancar y nadie lo sabría."""
    for proveedor in registry.proveedores():
        assert registry.declara_zero_training(proveedor), (
            f"'{proveedor}' está en el catálogo sin declarar zero_training: "
            "elegirlo haría fallar el arranque"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings
# ─────────────────────────────────────────────────────────────────────────────


def test_los_embeddings_respetan_la_dimension_del_indice() -> None:
    gateway = Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.FAKE))
    vector = gateway.embeddings().embed_query("espesor por debajo del mínimo")

    assert len(vector) == registry.dimensiones_del_indice()


def test_anthropic_cae_a_gemini_para_embeddings() -> None:
    """Anthropic no publica modelo de embeddings.

    La caída está declarada en `models.yaml`, no cableada en el gateway, y el
    adapter que se construye es el del proveedor al que se cayó.
    """
    gateway = Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.ANTHROPIC))
    assert isinstance(gateway.embeddings(), Embeddings)


def test_si_falta_la_credencial_del_proveedor_de_caida_lo_dice() -> None:
    """El error tiene que nombrar a Gemini, no a Anthropic.

    Quien eligió Anthropic no configuró `GOOGLE_API_KEY` a propósito: el mensaje
    tiene que explicar por qué de golpe hace falta.
    """
    gateway = Gateway(
        settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.ANTHROPIC, GOOGLE_API_KEY=None)
    )
    with pytest.raises(GatewayError, match="GOOGLE_API_KEY"):
        gateway.embeddings()


def test_openai_no_puede_resolver_embeddings_contra_este_indice() -> None:
    """1536 dimensiones contra un índice de 768.

    El gateway no lo puede arreglar: es el registry el que se niega, y la
    negativa tiene que llegar hasta acá en lugar de degradar a un vector que el
    índice rechazaría durante la ingesta.
    """
    gateway = Gateway(settings=settings_de(SYNAPSEFLOW_PROVIDER=Provider.OPENAI))
    with pytest.raises(registry.RegistryError, match="reindexar"):
        gateway.embeddings()


# ─────────────────────────────────────────────────────────────────────────────
# Respaldo entre proveedores
# ─────────────────────────────────────────────────────────────────────────────


def test_sin_respaldo_configurado_no_se_encadena_nada() -> None:
    """El default es sin respaldo, y es deliberado.

    Un respaldo silencioso manda el texto a un proveedor que el usuario no
    eligió, y en un cliente regulado eso es una decisión con dueño.
    """
    gateway = Gateway(Provider.GEMINI, settings=settings_de())
    assert isinstance(gateway.chat("router"), BaseChatModel)


def test_con_respaldo_configurado_la_cadena_lo_incluye() -> None:
    gateway = Gateway(
        settings=settings_de(
            SYNAPSEFLOW_PROVIDER=Provider.GEMINI,
            SYNAPSEFLOW_FALLBACK_PROVIDER=Provider.OPENAI,
        )
    )
    modelo = gateway.chat("router")

    assert hasattr(modelo, "fallbacks"), "el respaldo configurado no se encadenó"
    assert not isinstance(modelo, BaseChatModel)


def test_un_respaldo_sin_credenciales_se_descarta() -> None:
    """Encadenarlo produciría una degradación que falla igual.

    Y peor: el error que ve el usuario sería el del respaldo, no el del proveedor
    que eligió.
    """
    gateway = Gateway(
        settings=settings_de(
            SYNAPSEFLOW_PROVIDER=Provider.GEMINI,
            SYNAPSEFLOW_FALLBACK_PROVIDER=Provider.OPENAI,
            OPENAI_API_KEY=None,
        )
    )
    assert isinstance(gateway.chat("router"), BaseChatModel)


def test_el_respaldo_al_mismo_proveedor_no_encadena() -> None:
    """Reintentar contra el que acaba de fallar no es degradar."""
    gateway = Gateway(
        settings=settings_de(
            SYNAPSEFLOW_PROVIDER=Provider.GEMINI,
            SYNAPSEFLOW_FALLBACK_PROVIDER=Provider.GEMINI,
        )
    )
    assert isinstance(gateway.chat("router"), BaseChatModel)


# ─────────────────────────────────────────────────────────────────────────────
# El modelo de embeddings falso
# ─────────────────────────────────────────────────────────────────────────────


def test_los_embeddings_falsos_son_estables_entre_instancias() -> None:
    """`hash()` de Python está salteado por proceso.

    Con él, un corpus indexado en una corrida no sería recuperable en la
    siguiente: el bug silencioso que un modelo falso no puede permitirse.
    """
    texto = "API 570 exige retirar de servicio un componente bajo t_min"
    assert FakeEmbeddings(768).embed_query(texto) == FakeEmbeddings(768).embed_query(texto)


def test_los_embeddings_falsos_tienen_similitud_lexica_real() -> None:
    """Es lo que permite que los tests de recuperación de F3 afirmen algo.

    Con un vector derivado del hash del texto entero, dos textos que comparten
    todas sus palabras salvo una darían vectores sin relación, y «el fragmento
    pertinente sale primero» no se podría testear.
    """
    modelo = FakeEmbeddings(768)
    consulta = modelo.embed_query("espesor mínimo requerido en cañerías")
    pertinente = modelo.embed_query("el espesor mínimo requerido para cañerías en servicio")
    ajeno = modelo.embed_query("frecuencia de calibración de válvulas de alivio")

    def coseno(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert coseno(consulta, pertinente) > coseno(consulta, ajeno)


def test_un_texto_sin_palabras_no_produce_un_vector_de_ceros() -> None:
    """Firestore lo aceptaría y el documento nunca se recuperaría."""
    vector = FakeEmbeddings(768).embed_query("   ...   ")
    assert any(componente != 0.0 for componente in vector)


def test_los_embeddings_falsos_respetan_la_dimension_pedida() -> None:
    assert len(FakeEmbeddings(1536).embed_query("x")) == 1536
