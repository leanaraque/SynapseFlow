"""El único punto por donde el texto sale hacia un proveedor de modelos.

Ningún módulo fuera de `synapseflow.llm` instancia un `ChatModel`. No es una
convención de estilo: es lo que hace *verificable* la promesa de que ningún dato
clasificado sale del perímetro. Con un solo camino de salida, un test puede
recorrer los imports del paquete y afirmar que no existe otro
(`tests/llm/test_frontera.py`). Con varios, «no filtramos PII» sería una
afirmación imposible de comprobar.

## Qué se le pide

Un perfil de tarea, nunca un nombre de modelo:

    gateway = Gateway()
    modelo = gateway.chat("synthesis")     # no `gemini-3.5-flash`
    vectores = gateway.embeddings()

Cambiar la política de costos de toda la plataforma es editar `models.yaml`. Ver
docs/adr/0004-gateway-provider-agnostic.md

## Qué verifica al construirse

1. Que el proveedor activo tenga credenciales.
2. Que el proveedor declare que no entrena con los datos, si la política está
   activa.

Las dos fallan en el constructor y no en la primera llamada. La diferencia
importa: al arrancar la API eso ocurre en el startup, con el error visible; en la
primera llamada ocurre con el usuario esperando y el traceback enterrado en el
streaming.

Ver docs/plan/fases/F1-gateway.md § F1.3
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from pydantic import SecretStr

from synapseflow.config import Provider, Settings, get_settings
from synapseflow.llm import registry
from synapseflow.llm.fake import FakeChatModel, FakeEmbeddings

if TYPE_CHECKING:  # pragma: no cover - solo para el tipado
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel

PerfilDeChat = Literal["router", "synthesis", "verifier"]

# Lo que devuelve `Gateway.chat`.
#
# Sin respaldo configurado es un `BaseChatModel` y nada más. Con respaldo, es el
# `RunnableWithFallbacks` que produce `with_fallbacks()`, que **no** es un
# `BaseChatModel`. Anotar `BaseChatModel` sería mentir, y la mentira la paga el
# siguiente que reciba un objeto sin los atributos que el tipo promete.
#
# Este alias es el supertipo que ambos satisfacen. Verificado contra el entorno:
# `create_agent` anota `str | BaseChatModel` pero acepta el envoltorio y el
# agente completo funciona —tool calling incluido—, porque lo resuelve por
# duck-typing. Ver docs/plan/00-convenciones.md § Hallazgo 7.
ModeloDeChat = Runnable[LanguageModelInput, BaseMessage]

PERFILES_DE_CHAT: tuple[str, ...] = ("router", "synthesis", "verifier")

# Temperatura cero en los tres perfiles, incluido el de redacción.
#
# La tentación es dar algo de temperatura a `synthesis` para que el texto no
# suene mecánico. Acá no corresponde: la respuesta cita normativa que un
# ingeniero firma, y dos corridas de la misma pregunta deberían decir lo mismo.
# Además la suite de evals de F8 mide regresión entre corridas, y con temperatura
# el ruido del muestreo se confunde con el efecto de un cambio de prompt.
#
# ⚠ PEDIRLA NO ES OBTENERLA. Verificado el 2026-08-06: `gemini-3.5-flash-lite`
# —el modelo de los perfiles `router` y `verifier`— avisa por `UserWarning` que
# usa sampling fijo y que **ignora** el parámetro. Se sigue pasando porque los
# modelos que sí lo respetan lo necesitan, pero el determinismo de esos dos
# perfiles depende del proveedor y no de esta constante. Cualquier eval que
# asuma reproducibilidad exacta tiene que medirlo, no darlo por hecho.
TEMPERATURA = 0.0

# Paquete de PyPI que provee cada adapter. Se usa para que un proveedor sin su
# SDK instalado falle diciendo qué instalar, en lugar de un ImportError pelado.
PAQUETE_DEL_PROVEEDOR: dict[Provider, str] = {
    Provider.GEMINI: "langchain-google-genai",
    Provider.OPENAI: "langchain-openai",
    Provider.AZURE_OPENAI: "langchain-openai",
    Provider.ANTHROPIC: "langchain-anthropic",
}


class GatewayError(RuntimeError):
    """El gateway no puede entregar un modelo para lo que se le pidió."""


class ProveedorNoInstaladoError(GatewayError):
    """El adapter del proveedor activo no está instalado en el entorno."""


class PoliticaVioladaError(GatewayError):
    """El proveedor activo no cumple una política que está exigida.

    Tipo propio porque no es un error de configuración que el usuario pueda
    corregir con una variable de entorno: es una decisión de gobernanza. Quien
    la reciba tiene que poder distinguirla y reportarla como tal.
    """


class Gateway:
    """Fábrica de modelos para un proveedor y una configuración dados.

    Se instancia una vez por proceso —en el startup de la API, o en una fixture
    de test— y se le piden modelos por perfil. Las instancias se cachean: un
    `ChatGoogleGenerativeAI` abre un cliente HTTP, y crear uno por turno de
    conversación desperdicia conexiones sin ganar nada.
    """

    def __init__(
        self,
        proveedor: Provider | None = None,
        *,
        settings: Settings | None = None,
        falso: FakeChatModel | None = None,
    ) -> None:
        """
        Args:
            proveedor: fuerza un proveedor. Por defecto sale de la configuración.
            settings: configuración a usar. Inyectable para que los tests no
                tengan que manipular el entorno del proceso.
            falso: modelo ya programado que se devuelve cuando el proveedor es
                `fake`. Permite que un test ejercite el gateway de punta a punta
                —resolución del perfil, política, caché— y reciba igual el modelo
                que programó.

        Raises:
            CredencialesFaltantesError: si el proveedor activo no tiene con qué
                autenticar.
            PoliticaVioladaError: si la política de zero-training está activa y
                el catálogo no la declara para este proveedor.
        """
        self.settings = settings or get_settings()
        self.proveedor = proveedor or self.settings.provider
        self._falso = falso
        self._cache: dict[str, ModeloDeChat] = {}

        # El orden importa: sin credenciales no hay llamada posible, así que ese
        # error es más específico y se reporta primero.
        self._verificar_credenciales(self.proveedor)
        self._verificar_zero_training(self.proveedor)

    # ── Modelos ──────────────────────────────────────────────────────────────

    def chat(self, perfil: PerfilDeChat) -> ModeloDeChat:
        """Modelo de chat para un perfil de tarea.

        Sin `SYNAPSEFLOW_FALLBACK_PROVIDER` configurado devuelve un
        `BaseChatModel`. Con respaldo devuelve la cadena de `with_fallbacks()`,
        que expone la misma interfaz de invocación y `bind_tools`.

        Raises:
            GatewayError: si el perfil no es un perfil de chat.
        """
        if perfil not in PERFILES_DE_CHAT:
            raise GatewayError(
                f"'{perfil}' no es un perfil de chat. Los válidos son "
                f"{list(PERFILES_DE_CHAT)}. Para vectores, usá Gateway.embeddings()."
            )

        if perfil not in self._cache:
            self._cache[perfil] = self._construir_chat(perfil)
        return self._cache[perfil]

    def embeddings(self) -> Embeddings:
        """Modelo de embeddings, con la dimensión que exige el índice vectorial.

        La resolución puede caer a otro proveedor: Anthropic no publica modelo de
        embeddings y el catálogo declara `embedding_fallback: gemini`. Por eso el
        adapter se construye para `spec.proveedor` y no para el proveedor activo,
        y las credenciales que hacen falta son las de aquel.
        """
        spec = registry.resolver(self.proveedor, registry.PERFIL_EMBEDDING)
        efectivo = Provider(spec.proveedor)

        if efectivo is not self.proveedor:
            # La caída es del catálogo, pero las credenciales son del entorno: si
            # faltan, el error tiene que nombrar al proveedor al que se cayó, o
            # el mensaje pide una clave que nadie configuró a propósito.
            faltante = self.settings.credenciales_faltantes(efectivo)
            if faltante:
                raise GatewayError(
                    f"el proveedor '{self.proveedor.value}' no tiene modelo de "
                    f"embeddings propio y el catálogo cae a '{efectivo.value}', "
                    f"que necesita {', '.join(faltante)} en el entorno."
                )

        return self._construir_embeddings(efectivo, spec)

    def spec(self, perfil: str) -> registry.ModelSpec:
        """Modelo concreto al que resuelve un perfil, sin instanciarlo.

        Lo consume la contabilidad de costo (F1.4), que necesita el precio y el
        nombre del modelo pero no el cliente.
        """
        return registry.resolver(self.proveedor, perfil)

    # ── Construcción por proveedor ───────────────────────────────────────────

    def _construir_chat(self, perfil: str) -> ModeloDeChat:
        spec = registry.resolver(self.proveedor, perfil)
        modelo = self._adapter_de_chat(self.proveedor, spec)

        respaldo = self._respaldo_de_chat(perfil)
        if respaldo is None:
            return modelo
        return modelo.with_fallbacks([respaldo])

    def _respaldo_de_chat(self, perfil: str) -> BaseChatModel | None:
        """Modelo alternativo al que degradar, si hay uno configurado y usable.

        Un respaldo sin credenciales es peor que ninguno: la cadena falla igual,
        pero el error que ve el usuario es el del respaldo y no el del proveedor
        que eligió. Por eso se descarta en silencio acá y no se encadena.
        """
        destino = self.settings.fallback_provider
        if destino is None or destino == self.proveedor:
            return None
        if self.settings.credenciales_faltantes(destino):
            return None

        try:
            spec = registry.resolver(destino, perfil)
        except registry.RegistryError:
            return None
        return self._adapter_de_chat(destino, spec)

    def _adapter_de_chat(self, proveedor: Provider, spec: registry.ModelSpec) -> BaseChatModel:
        """Instancia el `ChatModel` del proveedor.

        Los imports son perezosos y por proveedor a propósito: importar los
        cuatro SDK al cargar el módulo obligaría a tenerlos los cuatro
        instalados para usar uno. Anthropic, de hecho, no está en las
        dependencias del proyecto —se declara en el catálogo como adapter
        alternativo— y con imports al tope este módulo no cargaría.
        """
        if proveedor is Provider.FAKE:
            return self._falso if self._falso is not None else FakeChatModel()

        try:
            match proveedor:
                case Provider.GEMINI:
                    from langchain_google_genai import ChatGoogleGenerativeAI

                    return ChatGoogleGenerativeAI(
                        model=spec.modelo,
                        google_api_key=self.settings.google_api_key,
                        temperature=TEMPERATURA,
                    )

                case Provider.OPENAI:
                    from langchain_openai import ChatOpenAI

                    return ChatOpenAI(
                        model=spec.modelo,
                        api_key=_secreto(self.settings.openai_api_key),
                        temperature=TEMPERATURA,
                    )

                case Provider.AZURE_OPENAI:
                    from langchain_openai import AzureChatOpenAI

                    # `spec.modelo` es el nombre del *deployment*, que en Azure
                    # es lo que identifica al modelo. Sale del entorno, no del
                    # catálogo: lo define quien administra el recurso.
                    return AzureChatOpenAI(
                        azure_deployment=spec.modelo,
                        azure_endpoint=self.settings.azure_openai_endpoint,
                        api_version=self.settings.azure_openai_api_version,
                        api_key=_secreto(self.settings.azure_openai_api_key),
                        temperature=TEMPERATURA,
                    )

                case Provider.ANTHROPIC:
                    from langchain_anthropic import ChatAnthropic  # type: ignore[import-not-found]

                    return ChatAnthropic(
                        model=spec.modelo,
                        api_key=_secreto(self.settings.anthropic_api_key),
                        temperature=TEMPERATURA,
                    )
        except ImportError as exc:
            raise _sin_sdk(proveedor) from exc

        raise GatewayError(f"no hay adapter de chat para el proveedor '{proveedor}'")

    def _construir_embeddings(self, proveedor: Provider, spec: registry.ModelSpec) -> Embeddings:
        if proveedor is Provider.FAKE:
            # La dimensión sale del spec y no de una constante: es la misma que
            # el registry ya contrastó contra firestore.indexes.json.
            return FakeEmbeddings(dimensiones=spec.dimensiones or registry.dimensiones_del_indice())

        try:
            match proveedor:
                case Provider.GEMINI:
                    from langchain_google_genai import GoogleGenerativeAIEmbeddings

                    # La clave va como `api_key` y no como `google_api_key`: en
                    # esta clase el campo declara ese alias, al revés que en
                    # `ChatGoogleGenerativeAI`, que no lo declara. Las dos están
                    # en el mismo paquete y aceptan cosas distintas.
                    #
                    # `output_dimensionality` no es opcional acá:
                    # gemini-embedding-001 devuelve 3072 por defecto y el índice
                    # está creado para 768. Sin el parámetro, la ingesta escribe
                    # vectores que el índice rechaza. El catálogo declara qué
                    # pedir en `output_dimensionality`.
                    return GoogleGenerativeAIEmbeddings(
                        model=f"models/{spec.modelo}",
                        api_key=_secreto(self.settings.google_api_key),
                        output_dimensionality=spec.dimensionalidad_pedida,
                    )

                case Provider.OPENAI:
                    from langchain_openai import OpenAIEmbeddings

                    return OpenAIEmbeddings(
                        model=spec.modelo,
                        api_key=_secreto(self.settings.openai_api_key),
                        dimensions=spec.dimensionalidad_pedida or spec.dimensiones,
                    )

                case Provider.AZURE_OPENAI:
                    from langchain_openai import AzureOpenAIEmbeddings

                    return AzureOpenAIEmbeddings(
                        azure_deployment=spec.modelo,
                        azure_endpoint=self.settings.azure_openai_endpoint,
                        api_version=self.settings.azure_openai_api_version,
                        api_key=_secreto(self.settings.azure_openai_api_key),
                        dimensions=spec.dimensionalidad_pedida or spec.dimensiones,
                    )
        except ImportError as exc:
            raise _sin_sdk(proveedor) from exc

        raise GatewayError(
            f"el proveedor '{proveedor.value}' no tiene adapter de embeddings. "
            "Declarar un 'embedding_fallback' en models.yaml."
        )

    # ── Verificaciones del constructor ───────────────────────────────────────

    def _verificar_credenciales(self, proveedor: Provider) -> None:
        """Falla si el proveedor activo no tiene con qué autenticar.

        Vive acá y no en un validador de `Settings` porque atarla al constructor
        de la configuración obligaba a tener una `GOOGLE_API_KEY` para leer la
        configuración de Firestore, y dejaba a `scripts/seed.py` sin poder correr
        contra el emulador. Ver `Settings.verificar_credenciales_del_proveedor`.
        """
        if proveedor == self.settings.provider:
            self.settings.verificar_credenciales_del_proveedor()

    def _verificar_zero_training(self, proveedor: Provider) -> None:
        """Rechaza un proveedor que no declare que no entrena con los datos.

        `SYNAPSEFLOW_ENFORCE_ZERO_TRAINING` existía en la configuración desde el
        primer commit sin que nada la leyera. Una bandera de gobernanza que no
        se aplica es peor que no tenerla: alguien la ve en `true` y concluye que
        la garantía está.

        Lo que se verifica es lo que **declara el catálogo**, no el proveedor: no
        hay forma programática de comprobarlo. Para un cliente regulado el
        respaldo es el contrato, y el catálogo es dónde queda anotado quién lo
        firmó. Ver models.yaml § zero_training_note.
        """
        if not self.settings.enforce_zero_training:
            return
        try:
            declara = registry.declara_zero_training(proveedor)
        except registry.RegistryError as exc:
            raise PoliticaVioladaError(
                f"no se puede verificar la política de entrenamiento de '{proveedor.value}': {exc}"
            ) from exc

        if not declara:
            raise PoliticaVioladaError(
                f"SYNAPSEFLOW_ENFORCE_ZERO_TRAINING está activo y el catálogo no "
                f"declara zero_training para '{proveedor.value}'.\n"
                "  Mandar datos del cliente a un proveedor que entrena con ellos "
                "es justamente lo que la política impide.\n"
                "  Opciones: cambiar SYNAPSEFLOW_PROVIDER, o desactivar la "
                "política de forma explícita si el contrato lo respalda."
            )


def _secreto(valor: str | None) -> SecretStr | None:
    """Envuelve una clave para los adapters que la piden como `SecretStr`.

    Los de LangChain la declaran así para que un `repr()` accidental —en un log,
    en un traceback, en el estado de un grafo— no imprima la credencial. Vale la
    pena respetarlo en lugar de silenciar el tipo: es justo la clase de fuga que
    nadie revisa hasta que aparece en un log de producción.
    """
    return SecretStr(valor) if valor else None


def _sin_sdk(proveedor: Provider) -> ProveedorNoInstaladoError:
    """Traduce un `ImportError` de adapter a un error que dice qué instalar.

    Anthropic está en el catálogo como adapter alternativo y su SDK **no** es
    dependencia del proyecto, así que este camino no es hipotético: es lo que ve
    quien ponga `SYNAPSEFLOW_PROVIDER=anthropic` sin instalar nada.
    """
    paquete = PAQUETE_DEL_PROVEEDOR.get(proveedor, "el adapter correspondiente")
    return ProveedorNoInstaladoError(
        f"SYNAPSEFLOW_PROVIDER={proveedor.value} necesita el paquete "
        f"'{paquete}', que no está instalado.\n"
        f"  Instalalo con: pip install {paquete}"
    )
