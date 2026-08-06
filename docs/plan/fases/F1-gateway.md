# F1 · Gateway de LLM

**Depende de:** nada. Se puede empezar ya.
**Aporta:** precondición de los compromisos 4 y 5.
**Bloqueo externo:** `GOOGLE_API_KEY` solo para el test en vivo. Todo lo demás
corre con el modelo falso de `F1.2`.

## Por qué esta fase existe

Es el único punto por donde el texto sale del perímetro hacia un proveedor
externo. Que haya **un solo camino de salida** es lo que hace *verificable* la
promesa de que ningún dato sensible se filtra: con múltiples puntos, «no
filtramos PII» sería una afirmación imposible de comprobar.

Además concentra la política de costos: el código pide un **perfil de tarea**,
nunca un nombre de modelo.

Contexto completo en [ADR-0004](../../adr/0004-gateway-provider-agnostic.md).

---

## F1.1 · Registry de modelos y precios

**Produce:** `packages/synapseflow/llm/registry.py`

`models.yaml` ya existe en `packages/synapseflow/llm/`. Este commit lo carga y lo
expone.

**Qué implementar**

```python
class ModelSpec(BaseModel):
    """Un modelo concreto, resuelto para un perfil y un proveedor."""

    modelo: str
    entrada_por_1m: float
    salida_por_1m: float
    ventana_contexto: int | None = None
    dimensiones: int | None = None


def resolver(proveedor: Provider, perfil: str) -> ModelSpec: ...
def costo_de(spec: ModelSpec, tokens_entrada: int, tokens_salida: int) -> float: ...
def dimensiones_del_indice() -> int: ...
```

**Detalle que no se puede omitir.** Al resolver el perfil `embedding`, comparar
`spec.dimensiones` contra la dimensión declarada en `firestore.indexes.json` y
fallar con un mensaje explícito si no coinciden. Un índice vectorial de Firestore
fija su dimensión al crearse; descubrir el desajuste a mitad de la ingesta del
corpus significa recrear el índice y reindexar todo.

Para Azure OpenAI, los ids salen del entorno (`deployment_from_env: true` en el
YAML), no del catálogo: los define quien administra el recurso.

**Verificar:** `pytest tests/llm/test_registry.py -v`

Tests mínimos: resolver los cuatro perfiles para cada proveedor; el cálculo de
costo; que un desajuste de dimensiones falle al resolver.

---

## F1.2 · Modelo falso determinístico

**Produce:** `packages/synapseflow/llm/fake.py`

**Este commit desbloquea todas las fases siguientes.** Sin él, testear el grafo
de agentes exigiría cuota real y red, y los tests serían lentos y no
reproducibles.

**Qué implementar**

```python
class FakeChatModel(BaseChatModel):
    """Modelo determinístico para tests. No hace red.

    Se le programa qué responder ante qué entrada, incluidas invocaciones de
    herramienta, para poder ejercitar el grafo completo sin un proveedor.
    """
```

Tiene que soportar:

- Respuestas de texto fijas o por patrón sobre el último mensaje.
- **Emitir invocaciones de herramienta**, que es lo que permite testear el bucle
  del agente y —sobre todo— los gates de aprobación.
- Un contador de llamadas, para verificar los límites de `ModelCallLimitMiddleware`.
- Salida estructurada, que necesita el verificador de F3.

**Verificar:** `pytest tests/llm/test_fake.py -v`

---

## F1.3 · Gateway con adapters por proveedor

**Produce:** `packages/synapseflow/llm/gateway.py`

**Qué implementar**

```python
class Gateway:
    def chat(self, perfil: Literal["router", "synthesis", "verifier"]) -> BaseChatModel: ...
    def embeddings(self) -> Embeddings: ...
```

Adapters: Gemini (`langchain_google_genai`), OpenAI y Azure OpenAI
(`langchain_openai`), Anthropic. El proveedor activo sale de
`settings.provider`.

**Reglas**

- Devuelve siempre un `BaseChatModel`: para el resto del código el proveedor es
  invisible.
- Anthropic no tiene modelo de embeddings propio; el YAML declara
  `embedding_fallback: gemini` y el gateway lo resuelve solo.
- Usar `with_fallbacks()` de LangChain para degradar a un proveedor alternativo.
- Cuando `settings.provider` es el de tests, devolver `FakeChatModel`.

**Verificar:** `pytest tests/llm/test_gateway.py -v`

---

## F1.4 · Contabilidad de tokens y costo

**Produce:** `packages/synapseflow/llm/callbacks.py`

**Qué implementar**

```python
class ContabilidadDeCosto(BaseCallbackHandler):
    """Registra tokens, modelo, perfil, latencia y costo derivado."""
```

Escribe a la colección `llm_usage` (ya está en `Collections`), correlacionando
por `thread_id` y `run_id`. El costo se calcula con `registry.costo_de`, así que
es exacto por llamada y no una estimación.

**Verificar:** `pytest tests/llm/test_callbacks.py -v`

---

## F1.5 · Test estructural de la frontera de datos

**Produce:** `tests/llm/test_frontera.py`

**Este test es el que convierte una convención en una garantía.**

```python
def test_ningun_modulo_externo_importa_un_chatmodel():
    """Solo synapseflow.llm puede instanciar un modelo.

    Si otro módulo lo hace, existe un segundo camino de salida y la promesa
    de que los datos sensibles se redactan antes de salir deja de ser
    verificable.
    """
```

Recorrer los archivos `.py` del paquete —excluyendo `synapseflow/llm/`— y
verificar que ninguno importe `ChatGoogleGenerativeAI`, `ChatOpenAI`,
`AzureChatOpenAI` ni `ChatAnthropic`.

Agregar también `tests/llm/test_pricing_freshness.py`, que falla si el
`verified_on` de `models.yaml` tiene más de noventa días.

**Verificar:** `pytest tests/llm -v`

---

## Al cerrar F1

- Actualizar la fila del gateway en la tabla de estado de **ambos** READMEs
  (🚧 → ✅).
- Anotar en el `CHANGELOG.md`.
- `python -m scripts.estado` debe mostrar F0 o F2 como fase siguiente.
