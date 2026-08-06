"""Catálogo de modelos: resuelve *perfil de tarea + proveedor → modelo concreto*.

El código de la plataforma pide un **perfil** —`router`, `synthesis`,
`verifier`, `embedding`— y nunca un nombre de modelo. Cambiar la política de
costos de todo el sistema es editar `models.yaml`, no buscar literales
desparramados por el código. Ver docs/adr/0004-gateway-provider-agnostic.md

Este módulo no instancia ningún modelo ni hace red: solo resuelve y calcula.
Instanciar es trabajo del gateway (F1.3), que es el único punto por donde el
texto cruza el perímetro.

## La validación que justifica que esto exista

Un índice vectorial de Firestore **fija su dimensión al crearse**. Si el modelo
de embeddings devuelve otra cantidad, no hay error hasta que se intenta escribir:
para entonces hay que borrar el índice, recrearlo y reindexar el corpus entero.

Por eso `resolver(..., "embedding")` compara la dimensión del modelo contra la
declarada en `firestore.indexes.json` y falla si no coinciden. El desajuste se
descubre al arrancar, no a mitad de una ingesta.
"""

from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from synapseflow.config import Provider, get_settings

CATALOGO = Path(__file__).parent / "models.yaml"
INDICES = Path(__file__).resolve().parents[3] / "firestore.indexes.json"

# Los cuatro perfiles de tarea. El orden es el de la tabla del ADR-0004.
PERFILES: tuple[str, ...] = ("router", "synthesis", "verifier", "embedding")

PERFIL_EMBEDDING = "embedding"

# Campo que guarda el vector en los documentos del corpus. Tiene que coincidir
# con persistence.vectorstore.VECTOR_FIELD; se repite acá para no importar la
# capa de persistencia solo por una constante.
CAMPO_VECTORIAL = "embedding"

# A partir de acá el catálogo de precios se considera sin verificar. Los
# proveedores cambian precios sin aviso y una contabilidad de costos que miente
# es peor que no tener contabilidad: se usa para decidir qué modelo va en qué
# perfil.
DIAS_DE_VIGENCIA_DE_PRECIOS = 90


class RegistryError(RuntimeError):
    """El catálogo no se puede cargar, o el modelo pedido no se puede resolver."""


class ModelSpec(BaseModel):
    """Un modelo concreto, ya resuelto para un perfil y un proveedor.

    Lleva `proveedor` y `perfil` además del nombre del modelo porque es lo que
    la contabilidad de costos escribe en `llm_usage`: sin ellos, una fila del
    dashboard dice cuánto costó pero no qué tarea lo consumió, que es
    justamente la pregunta que se le hace a ese panel.
    """

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    proveedor: str
    perfil: str
    modelo: str
    entrada_por_1m: float = Field(ge=0)
    salida_por_1m: float = Field(ge=0)
    ventana_contexto: int | None = None
    dimensiones: int | None = None
    # Lo que el gateway tiene que pedirle al proveedor para obtener
    # `dimensiones`. None significa que el modelo ya las devuelve así.
    dimensionalidad_pedida: int | None = None
    nota: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Meta-esquema del catálogo
#
# Se valida con Pydantic por la misma razón que la ontología: un error de tipeo
# en `models.yaml` —`input_per_1M` en lugar de `input_per_1m`— tiene que fallar
# al cargar el catálogo y no producir un costo de cero que nadie note.
# ─────────────────────────────────────────────────────────────────────────────


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


class _PerfilCrudo(_Estricto):
    # `model` es null para Azure, donde el id lo define quien administra el
    # recurso y llega por `env`.
    model: str | None = None
    env: str | None = None
    input_per_1m: float = Field(ge=0)
    output_per_1m: float = Field(ge=0)
    context_window: int | None = None
    dimensions: int | None = None
    # Parámetro que hay que pasarle al proveedor para que devuelva `dimensions`.
    # Los modelos de embeddings modernos devuelven más de lo que el índice
    # admite y lo truncan a pedido: sin este dato, el gateway pediría el valor
    # por defecto y el resultado no entraría en el índice.
    output_dimensionality: int | None = None
    note: str | None = None


class _ProveedorCrudo(_Estricto):
    zero_training: bool
    zero_training_note: str | None = None
    deployment_from_env: bool = False
    # Proveedor al que se cae para embeddings cuando este no tiene modelo propio.
    embedding_fallback: str | None = None
    profiles: dict[str, _PerfilCrudo]


class _Catalogo(_Estricto):
    verified_on: dt.date
    vector_index_dimensions: int = Field(gt=0)
    providers: dict[str, _ProveedorCrudo]


@lru_cache(maxsize=1)
def _catalogo() -> _Catalogo:
    """Catálogo cargado y validado, una vez por proceso."""
    try:
        crudo = yaml.safe_load(CATALOGO.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError(f"no se puede leer el catálogo '{CATALOGO}': {exc}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"'{CATALOGO.name}' no se puede parsear: {exc}") from exc

    try:
        catalogo = _Catalogo.model_validate(crudo)
    except Exception as exc:
        raise RegistryError(f"'{CATALOGO.name}' no es válido:\n  {exc}") from exc

    declarada = dimensiones_del_indice()
    if catalogo.vector_index_dimensions != declarada:
        raise RegistryError(
            f"'{CATALOGO.name}' declara vector_index_dimensions="
            f"{catalogo.vector_index_dimensions} y firestore.indexes.json declara "
            f"{declarada}. Son la misma cosa dicha en dos lugares y no coinciden."
        )
    return catalogo


# ─────────────────────────────────────────────────────────────────────────────
# Dimensión del índice vectorial
# ─────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def dimensiones_del_indice() -> int:
    """Dimensión que declara `firestore.indexes.json` para el campo vectorial.

    Se lee del archivo de índices y no de una constante porque ese archivo es lo
    que efectivamente se despliega: una constante podría quedar desincronizada
    con lo que Firestore tiene creado, que es exactamente el error que esta
    función existe para impedir.
    """
    try:
        indices = json.loads(INDICES.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError(f"no se puede leer '{INDICES}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"'{INDICES.name}' no es JSON válido: {exc}") from exc

    dimensiones = {
        campo["vectorConfig"]["dimension"]
        for indice in indices.get("indexes", [])
        for campo in indice.get("fields", [])
        if campo.get("fieldPath") == CAMPO_VECTORIAL and "vectorConfig" in campo
    }

    if not dimensiones:
        raise RegistryError(
            f"'{INDICES.name}' no declara ningún índice vectorial sobre el campo "
            f"'{CAMPO_VECTORIAL}'. Sin él, find_nearest no funciona."
        )
    if len(dimensiones) > 1:
        raise RegistryError(
            f"'{INDICES.name}' declara índices vectoriales con dimensiones "
            f"distintas: {sorted(dimensiones)}. Un solo corpus no puede indexarse "
            "con dos dimensiones a la vez."
        )
    return int(dimensiones.pop())


# ─────────────────────────────────────────────────────────────────────────────
# Resolución
# ─────────────────────────────────────────────────────────────────────────────


def resolver(proveedor: Provider | str, perfil: str) -> ModelSpec:
    """Modelo concreto para un perfil de tarea y un proveedor.

    Args:
        proveedor: proveedor activo. Se acepta `Provider` o su valor.
        perfil: uno de `PERFILES`.

    Raises:
        RegistryError: si el perfil no existe, si el proveedor no está en el
            catálogo, si Azure no tiene el deployment en el entorno, o si la
            dimensión del modelo de embeddings no coincide con la del índice.
    """
    nombre_proveedor = proveedor.value if isinstance(proveedor, Provider) else str(proveedor)

    if perfil not in PERFILES:
        raise RegistryError(f"perfil desconocido '{perfil}'. Los válidos son: {list(PERFILES)}")

    catalogo = _catalogo()
    definicion = catalogo.providers.get(nombre_proveedor)
    if definicion is None:
        raise RegistryError(
            f"el proveedor '{nombre_proveedor}' no está en {CATALOGO.name}. "
            f"Declarados: {sorted(catalogo.providers)}"
        )

    crudo = definicion.profiles.get(perfil)

    # Anthropic no tiene modelo de embeddings propio. El catálogo declara a qué
    # proveedor caer, y la caída es explícita en el YAML en lugar de estar
    # cableada acá.
    if crudo is None and perfil == PERFIL_EMBEDDING and definicion.embedding_fallback:
        return resolver(definicion.embedding_fallback, perfil)

    if crudo is None:
        raise RegistryError(
            f"el proveedor '{nombre_proveedor}' no declara el perfil '{perfil}' "
            f"en {CATALOGO.name}, ni un embedding_fallback al que caer."
        )

    spec = ModelSpec(
        proveedor=nombre_proveedor,
        perfil=perfil,
        modelo=_nombre_del_modelo(nombre_proveedor, perfil, crudo),
        entrada_por_1m=crudo.input_per_1m,
        salida_por_1m=crudo.output_per_1m,
        ventana_contexto=crudo.context_window,
        dimensiones=crudo.dimensions,
        dimensionalidad_pedida=crudo.output_dimensionality,
        nota=crudo.note,
    )

    if perfil == PERFIL_EMBEDDING:
        _verificar_dimensiones(spec)

    return spec


def spec_por_modelo(nombre: str) -> ModelSpec | None:
    """Busca en el catálogo el modelo que se invocó de verdad.

    La contabilidad de costo no puede resolver el precio por perfil. Con un
    respaldo configurado, el perfil `synthesis` puede terminar ejecutándose en el
    modelo de otro proveedor, y cobrarle el precio del primero produce un panel
    de costos que miente justo cuando algo salió mal.

    Devuelve `None` si el modelo no está en el catálogo, en lugar de inventar un
    precio: quien contabiliza tiene que poder marcar la fila como no catalogada.
    Un costo cero indistinguible de un costo real es peor que un hueco visible.
    """
    for proveedor, definicion in _catalogo().providers.items():
        for perfil, crudo in definicion.profiles.items():
            if crudo.model == nombre:
                return ModelSpec(
                    proveedor=proveedor,
                    perfil=perfil,
                    modelo=nombre,
                    entrada_por_1m=crudo.input_per_1m,
                    salida_por_1m=crudo.output_per_1m,
                    ventana_contexto=crudo.context_window,
                    dimensiones=crudo.dimensions,
                    dimensionalidad_pedida=crudo.output_dimensionality,
                    nota=crudo.note,
                )
    return None


def _nombre_del_modelo(proveedor: str, perfil: str, crudo: _PerfilCrudo) -> str:
    """Resuelve el id del modelo, que en Azure sale del entorno.

    En Azure OpenAI los modelos se invocan por nombre de *deployment*, que lo
    define quien administra el recurso y no el catálogo. El YAML declara qué
    variable lo trae; el valor se lee de `Settings` y no de `os.environ`, porque
    config.py define que toda la configuración ambiental entra por un solo lugar.
    """
    if crudo.model:
        return crudo.model

    if not crudo.env:
        raise RegistryError(
            f"el perfil '{perfil}' de '{proveedor}' no declara ni 'model' ni 'env' "
            f"en {CATALOGO.name}: no hay forma de saber qué modelo invocar."
        )

    # El nombre del campo en Settings es el alias en minúsculas. La
    # correspondencia es directa y está declarada en config.py.
    atributo = crudo.env.lower()
    valor: Any = getattr(get_settings(), atributo, None)
    if not valor:
        raise RegistryError(
            f"el perfil '{perfil}' de '{proveedor}' resuelve su modelo desde "
            f"{crudo.env}, que no está definida en el entorno. En Azure OpenAI el "
            "id es el nombre del deployment y lo define quien administra el "
            "recurso. Ver .env.example"
        )
    return str(valor)


def _verificar_dimensiones(spec: ModelSpec) -> None:
    """Falla si el modelo de embeddings no coincide con el índice desplegado."""
    if spec.dimensiones is None:
        raise RegistryError(
            f"el perfil 'embedding' de '{spec.proveedor}' no declara 'dimensions' "
            f"en {CATALOGO.name}. Sin ese dato no se puede verificar que el "
            "corpus sea indexable."
        )

    esperadas = dimensiones_del_indice()
    if spec.dimensiones != esperadas:
        raise RegistryError(
            f"el modelo de embeddings '{spec.modelo}' devuelve {spec.dimensiones} "
            f"dimensiones y el índice vectorial declarado en {INDICES.name} está "
            f"creado para {esperadas}.\n"
            "  Un índice vectorial de Firestore fija su dimensión al crearse, así "
            "que usar este modelo obliga a recrear el índice y reindexar el corpus "
            "completo.\n"
            f"  Opciones: usar un proveedor cuyo modelo devuelva {esperadas} "
            f"dimensiones, o actualizar {INDICES.name} y models.yaml a "
            f"{spec.dimensiones} y reindexar."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Costos
# ─────────────────────────────────────────────────────────────────────────────


def costo_de(spec: ModelSpec, tokens_entrada: int, tokens_salida: int) -> float:
    """Costo en dólares de una llamada, según el catálogo.

    Los precios del YAML son por millón de tokens. No se redondea: el costo por
    llamada es del orden de millonésimos de dólar y redondear acá lo colapsaría
    a cero antes de poder sumarlo. El redondeo es del que muestra, no del que
    calcula.
    """
    if tokens_entrada < 0 or tokens_salida < 0:
        raise RegistryError(
            f"conteo de tokens negativo (entrada={tokens_entrada}, "
            f"salida={tokens_salida}): es un error de la contabilidad, no un costo"
        )
    return (tokens_entrada * spec.entrada_por_1m + tokens_salida * spec.salida_por_1m) / 1_000_000


# ─────────────────────────────────────────────────────────────────────────────
# Accesores del catálogo
# ─────────────────────────────────────────────────────────────────────────────


def proveedores() -> tuple[str, ...]:
    return tuple(sorted(_catalogo().providers))


def perfiles_de(proveedor: Provider | str) -> tuple[str, ...]:
    """Perfiles que el proveedor declara, sin contar los que resuelve por caída."""
    nombre = proveedor.value if isinstance(proveedor, Provider) else str(proveedor)
    definicion = _catalogo().providers.get(nombre)
    if definicion is None:
        raise RegistryError(f"el proveedor '{nombre}' no está en {CATALOGO.name}")
    return tuple(p for p in PERFILES if p in definicion.profiles)


def declara_zero_training(proveedor: Provider | str) -> bool:
    """Si el catálogo declara que el proveedor no entrena con datos del cliente.

    Lo consume el enforcement de la política (F4.4). Vive acá porque el dato
    está en `models.yaml` y volver a parsear el YAML desde otro módulo sería
    tener dos lectores del mismo archivo.

    Es una **declaración del catálogo**, no una verificación contra el proveedor.
    Para un cliente regulado, el respaldo es el contrato.
    """
    nombre = proveedor.value if isinstance(proveedor, Provider) else str(proveedor)
    definicion = _catalogo().providers.get(nombre)
    if definicion is None:
        raise RegistryError(f"el proveedor '{nombre}' no está en {CATALOGO.name}")
    return definicion.zero_training


def verificado_el() -> dt.date:
    return _catalogo().verified_on


def dias_desde_verificacion(hoy: dt.date | None = None) -> int:
    """Días transcurridos desde que se contrastaron los precios con el proveedor.

    `hoy` es inyectable para que el test no dependa de la fecha del sistema.
    """
    return ((hoy or dt.date.today()) - verificado_el()).days


def limpiar_cache() -> None:
    """Solo para tests: obliga a releer el catálogo y los índices."""
    _catalogo.cache_clear()
    dimensiones_del_indice.cache_clear()
