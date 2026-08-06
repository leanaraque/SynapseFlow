"""Configuración de la plataforma.

Todo lo ambiental entra por acá y en ningún otro lado. Ningún módulo lee
`os.environ` directamente: eso hace que la configuración efectiva sea
inspeccionable en un solo lugar y que los tests puedan sustituirla sin
manipular el entorno del proceso.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CredencialesFaltantesError(RuntimeError):
    """El proveedor de LLM activo no tiene credenciales en el entorno.

    Tipo propio y no `ValueError` para que quien la reciba pueda distinguirla de
    cualquier otro error de configuración y responder con un mensaje útil en
    lugar de un traceback.
    """


class Provider(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    ANTHROPIC = "anthropic"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Proveedor de LLM ─────────────────────────────────────────────────────
    provider: Provider = Field(default=Provider.GEMINI, alias="SYNAPSEFLOW_PROVIDER")

    google_api_key: str | None = Field(default=None, alias="GOOGLE_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    azure_openai_api_key: str | None = Field(default=None, alias="AZURE_OPENAI_API_KEY")
    azure_openai_endpoint: str | None = Field(default=None, alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_version: str = Field(default="2024-10-21", alias="AZURE_OPENAI_API_VERSION")
    azure_openai_chat_deployment: str | None = Field(
        default=None, alias="AZURE_OPENAI_CHAT_DEPLOYMENT"
    )
    azure_openai_embeddings_deployment: str | None = Field(
        default=None, alias="AZURE_OPENAI_EMBEDDINGS_DEPLOYMENT"
    )

    # ── Firebase / GCP ───────────────────────────────────────────────────────
    gcp_project: str = Field(default="synapseflow-5fc52", alias="GOOGLE_CLOUD_PROJECT")
    firestore_database: str = Field(default="(default)", alias="FIRESTORE_DATABASE")
    firestore_emulator_host: str | None = Field(default=None, alias="FIRESTORE_EMULATOR_HOST")

    # ── Observabilidad ───────────────────────────────────────────────────────
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_api_key: str | None = Field(default=None, alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="synapseflow-dev", alias="LANGSMITH_PROJECT")

    # ── Política de gobernanza ───────────────────────────────────────────────
    enforce_zero_training: bool = Field(default=True, alias="SYNAPSEFLOW_ENFORCE_ZERO_TRAINING")
    redact_pii: bool = Field(default=True, alias="SYNAPSEFLOW_REDACT_PII")
    require_approval: bool = Field(default=True, alias="SYNAPSEFLOW_REQUIRE_APPROVAL")

    # ── Dominio activo ───────────────────────────────────────────────────────
    domain: str = Field(default="oil_and_gas", alias="SYNAPSEFLOW_DOMAIN")

    # ── Límites operativos ───────────────────────────────────────────────────
    # Techo de llamadas al modelo por turno. Un agente que entra en un ciclo de
    # herramientas sin converger es el modo de falla más caro que existe: se
    # corta acá y no en la factura.
    max_model_calls_per_run: int = Field(default=12, alias="SYNAPSEFLOW_MAX_MODEL_CALLS")
    retrieval_top_k: int = Field(default=6, alias="SYNAPSEFLOW_RETRIEVAL_TOP_K")
    # Cuántos candidatos trae cada retriever antes de fusionar y comprimir.
    retrieval_fetch_k: int = Field(default=20, alias="SYNAPSEFLOW_RETRIEVAL_FETCH_K")

    def verificar_credenciales_del_proveedor(self) -> None:
        """Falla si el proveedor activo no tiene con qué autenticar.

        La llama el gateway de LLM al construirse, que es el único punto por el
        que sale una llamada a un modelo (ver ADR-0004). Al arrancar la API eso
        ocurre en el startup, así que la garantía es la misma que antes: el
        error aparece ahí y no en la primera llamada, con el usuario esperando y
        el traceback enterrado en el streaming.

        **No es un validador de `Settings`.** Lo fue, y eso ataba toda la
        configuración —proyecto de GCP, emulador, política de gobernanza— a
        tener credenciales de un proveedor de LLM. Consecuencia concreta:
        `scripts/seed.py`, que carga datos sintéticos en el emulador y no invoca
        ningún modelo, no podía construir `Settings` sin una `GOOGLE_API_KEY`.
        El plan de trabajo promete que todas las fases se pueden escribir y
        testear sin esa clave, así que la validación no puede vivir en el
        constructor.
        """
        faltante: list[str] = []
        match self.provider:
            case Provider.GEMINI if not self.google_api_key:
                faltante = ["GOOGLE_API_KEY"]
            case Provider.OPENAI if not self.openai_api_key:
                faltante = ["OPENAI_API_KEY"]
            case Provider.ANTHROPIC if not self.anthropic_api_key:
                faltante = ["ANTHROPIC_API_KEY"]
            case Provider.AZURE_OPENAI:
                faltante = [
                    nombre
                    for nombre, valor in (
                        ("AZURE_OPENAI_API_KEY", self.azure_openai_api_key),
                        ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
                        ("AZURE_OPENAI_CHAT_DEPLOYMENT", self.azure_openai_chat_deployment),
                    )
                    if not valor
                ]
            case _:
                faltante = []

        if faltante:
            raise CredencialesFaltantesError(
                f"SYNAPSEFLOW_PROVIDER={self.provider.value} requiere "
                f"{', '.join(faltante)} en el entorno. Ver .env.example"
            )

    @property
    def using_emulator(self) -> bool:
        return self.firestore_emulator_host is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuración del proceso, resuelta una sola vez."""
    return Settings()


def reset_settings_cache() -> None:
    """Solo para tests: obliga a releer el entorno."""
    get_settings.cache_clear()
