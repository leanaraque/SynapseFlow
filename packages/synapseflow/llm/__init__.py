"""Gateway de modelos.

**Este paquete es el único que puede instanciar un modelo.** El resto de la
plataforma pide un perfil de tarea al `Gateway` y recibe un `BaseChatModel`: para
todo lo demás, el proveedor es invisible. Que haya un solo camino de salida es lo
que hace verificable la promesa sobre los datos sensibles, y hay un test
estructural que lo comprueba (`tests/llm/test_frontera.py`).

La decisión de diseño —por qué el código pide un perfil y nunca un nombre de
modelo, y por qué el gateway es el único punto donde el texto cruza el perímetro
de datos— está en docs/adr/0004-gateway-provider-agnostic.md
"""

from synapseflow.llm.callbacks import Consumo, ContabilidadDeCosto
from synapseflow.llm.fake import (
    FakeChatModel,
    FakeChatModelError,
    FakeEmbeddings,
    Llamada,
    Respuesta,
)
from synapseflow.llm.gateway import (
    PERFILES_DE_CHAT,
    Gateway,
    GatewayError,
    PerfilDeChat,
    PoliticaVioladaError,
    ProveedorNoInstaladoError,
)
from synapseflow.llm.registry import (
    PERFILES,
    ModelSpec,
    RegistryError,
    costo_de,
    declara_zero_training,
    dimensiones_del_indice,
    resolver,
)

__all__ = [
    "PERFILES",
    "PERFILES_DE_CHAT",
    "Consumo",
    "ContabilidadDeCosto",
    "FakeChatModel",
    "FakeChatModelError",
    "FakeEmbeddings",
    "Gateway",
    "GatewayError",
    "Llamada",
    "ModelSpec",
    "PerfilDeChat",
    "PoliticaVioladaError",
    "ProveedorNoInstaladoError",
    "RegistryError",
    "Respuesta",
    "costo_de",
    "declara_zero_training",
    "dimensiones_del_indice",
    "resolver",
]
