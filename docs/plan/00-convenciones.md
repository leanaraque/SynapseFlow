# Convenciones obligatorias

Leé esto entero antes del primer commit. La sección **API verificada** es la más
importante: documenta hallazgos que contradicen la mayoría del material que hay
en internet, y saltearla produce código que no compila o que hay que rehacer.

---

## 1 · Autoría de los commits

**Nunca agregar co-autores.** Nada de `Co-Authored-By`, ni firmas, ni menciones
de herramientas de IA en mensajes de commit, tags, descripciones de PR o
comentarios del código.

Toda la autoría es de **Leandro Araque**. La identidad ya está configurada a
nivel del repositorio; no la cambies.

Verificación antes de pushear:

```bash
git log --format="%B" | grep -iE "co-authored-by|generated with|assisted by"
git log --format="%an <%ae>" | sort -u    # un solo autor
```

Si la primera devuelve algo, el historial está contaminado y hay que corregirlo
antes de publicar.

> El patrón incluía antes `claude|anthropic`. Daba tres falsos positivos, todos
> por la palabra «Anthropic» usada como **nombre de proveedor** en el cuerpo de
> commits que hablan del catálogo de modelos. Un chequeo que grita en falso se
> termina ignorando, y entonces deja de proteger de lo que existía para
> proteger. Se busca el trailer, que es lo que efectivamente contamina la
> autoría, y se contrasta la lista de autores, que es lo que se quería garantizar.

## 2 · Idioma

- **El código, los comentarios, los docstrings y los ADRs van en español.** Es
  deliberado: el dominio es normativa técnica en español, y mezclar idiomas entre
  el YAML del dominio y el código que lo interpreta genera fricción real al leer.
- Los nombres de símbolos de LangChain y LangGraph se dejan como están:
  `BaseCheckpointSaver`, `interrupt`, `AgentMiddleware`, `StateGraph`.
- El `README.md` está en **inglés** y es la fuente de verdad; `README.es.md` es
  su traducción. **Los dos se actualizan en el mismo commit** — hay un job de CI
  que falla si tocás uno solo.

## 3 · Estilo de los comentarios

Los comentarios explican **por qué**, no **qué**. Si un comentario parafrasea la
línea que tiene debajo, sobra.

```python
# MAL: incrementa el contador
contador += 1

# BIEN: Firestore admite hasta 500 operaciones por batch y los corpus de
# normativa superan ese número con facilidad.
for lote in _en_lotes(documentos, 400):
```

Cuando una decisión tiene una alternativa obvia que se descartó, decilo. Eso es
lo que evita que alguien "arregle" en seis meses algo que estaba bien.

## 4 · Formato de los mensajes de commit

```
Asunto en imperativo, sin punto final

Cuerpo que explica POR QUÉ, no qué. El qué ya está en el diff.

Si el commit toma una decisión no obvia, contá qué alternativa se
descartó y por qué.

Verificado: qué comando corriste y qué dio.
```

- Un commit por unidad conceptual. Si toca la ontología, el gateway y el frontend,
  son tres commits.
- Si el cambio afecta comportamiento, viene con un test que falla sin el cambio.

## 5 · Herramientas de calidad

Las tres corren en CI. Un cambio que no las pasa no se integra.

```bash
ruff check .
ruff format .
mypy packages/synapseflow
```

Correlas **antes** de commitear, no después de que falle el CI.

## 6 · Tests

```bash
# La suite completa necesita el emulador de Firestore en otra terminal:
firebase emulators:start --only firestore --project synapseflow-5fc52
pytest
```

Convenciones que ya están decididas y hay que respetar:

- **Un único event loop por sesión.** Está configurado en `pyproject.toml` con
  `asyncio_default_fixture_loop_scope = "session"`. La razón es concreta: el
  cliente de Firestore ata sus canales gRPC al loop donde se creó y se cachea por
  proceso a propósito. Con loops por test, el cliente queda atado a un loop
  cerrado a partir del segundo test. **Si ves tests que pasan solos y fallan en
  conjunto, la causa es esta, y la solución no es tocar el código de producción.**
- Los tests que necesitan el emulador llevan `@pytest.mark.emulator` y se saltean
  con mensaje claro si no está corriendo.
- Los que consumen cuota real de un proveedor llevan `@pytest.mark.live_llm` y no
  corren en CI.
- Los tests valiosos verifican **propiedades del sistema**, no implementación.
  «Ninguna acción irreversible es alcanzable sin gate» vale más que «guardar y
  leer devuelve lo mismo».

## 7 · ADRs

Todo cambio que altere una decisión estructural necesita un ADR en `docs/adr/`.
Formato en los cinco existentes. Las dos secciones que hacen la diferencia:

- **Alternativas consideradas**, con por qué se descartó cada una. Un ADR sin
  esto no documenta una decisión: documenta un hecho consumado.
- **Consecuencias en contra.** Un ADR que solo lista ventajas no es honesto y no
  sirve para revisarlo en seis meses.

Un ADR no se edita para cambiar de opinión: se escribe uno nuevo que lo reemplaza.

## 8 · Al cerrar cada commit

1. `ruff check . && ruff format --check . && mypy packages/synapseflow`
2. Correr el comando de verificación que indica `python -m scripts.estado`
3. `python -m scripts.estado` de nuevo, para confirmar que el commit se detecta
4. Si el commit cierra una fase: actualizar la tabla de estado en **ambos**
   READMEs y anotar en el `CHANGELOG.md`

---

# API verificada

Todo lo de esta sección se comprobó por introspección contra las versiones
instaladas el **2026-07-29**. Contradice buena parte del material público, que
está escrito para LangChain 0.3.

**Antes de usar una API de LangChain que no esté acá, verificala en el entorno**
en lugar de confiar en un tutorial:

```python
import inspect
from langchain.agents import create_agent

print(inspect.signature(create_agent))
```

## Versiones fijadas

| Paquete | Versión |
|---|---|
| `langchain` | 1.3.14 |
| `langchain-core` | 1.5.2 |
| `langgraph` | 1.2.10 |
| `langchain-classic` | 1.0.8 |
| `langchain-text-splitters` | 1.1.2 |
| `langchain-google-genai` | 4.3.2 |
| `langchain-openai` | 1.4.1 |

## Hallazgo 1 · `langchain-google-firestore` NO se puede usar

Pinnea `langchain-core <1.0.0`. Es incompatible con este proyecto.

Por eso `FirestoreVectorStore` y `FirestoreSaver` son implementación propia
contra las interfaces abstractas. **No intentes reemplazarlos por el paquete
oficial**: no instala. Ver [ADR-0002](../adr/0002-integraciones-firestore-propias.md).

## Hallazgo 2 · Los retrievers de composición se mudaron

`langchain.retrievers` **ya no existe**. En 1.x:

```python
from langchain_classic.retrievers import (
    EnsembleRetriever,
    MultiQueryRetriever,
    ContextualCompressionRetriever,
)
```

`BM25Retriever` quedó en `langchain-community`, que **no está instalado** y no se
va a instalar por un solo retriever. Se implementa uno propio como `BaseRetriever`
sobre `rank_bm25`, que sí está instalado.

## Hallazgo 3 · `create_react_agent` salió de `langchain.agents`

El prebuilt de 1.x es `create_agent`, y su firma acepta `middleware`:

```python
from langchain.agents import create_agent

create_agent(
    model,
    tools,
    system_prompt=...,
    middleware=[...],  # ← lo importante
    checkpointer=...,
    store=...,
)
```

## Hallazgo 4 · LangChain 1.x trae middlewares de primera clase

En `langchain.agents.middleware`. Los hooks de `AgentMiddleware` son:

`before_agent` · `before_model` · `wrap_model_call` · `wrap_tool_call` ·
`after_model` · `after_agent` (más sus variantes `a*` asincrónicas).

Middlewares ya hechos que este proyecto **usa en lugar de reinventar**:

| Middleware | Para qué acá |
|---|---|
| `HumanInTheLoopMiddleware` | Los gates de aprobación. Se configura con `interrupt_on={nombre: InterruptOnConfig}` |
| `PIIMiddleware` | Redacción. Acepta `detector` propio y estrategias `block/redact/mask/hash` |
| `ModelCallLimitMiddleware` | Techo de llamadas por turno |
| `ModelFallbackMiddleware` | Degradar a otro proveedor |
| `SummarizationMiddleware` | Manejo de ventana de contexto |

`InterruptOnConfig` es un `TypedDict` con `allowed_decisions`
(`approve`/`edit`/`reject`/`respond`), `description`, `args_schema` y `when`.

**`description`, cuando es callable, recibe TRES argumentos**, no uno:

```python
def descripcion(tool_call: ToolCall, state: AgentState, runtime: Runtime) -> str: ...
```

`tool_call` es el `ToolCall` de LangChain —un `TypedDict` con `name`, `args` e
`id`—, no un objeto con atributo `.tool_call`. Está declarado en
`langchain.agents.middleware.human_in_the_loop._DescriptionFactory`.

> Este documento decía antes que recibía un `ToolCallRequest`. Era falso, y el
> compilador estaba escrito contra esa firma: el gate lanzaba `TypeError` en la
> primera acción irreversible que se propusiera. Lo detectó recién el primer test
> que ejecutó un gate de punta a punta. **Verificá la firma en el entorno.**

**Reanudar un gate** lleva el payload envuelto:

```python
Command(resume={"decisions": [{"type": "approve"}]})
```

**El compilador de la ontología ya produce esa configuración**:
`interrupt_config(ontology, role)` en `packages/synapseflow/ontology/compiler.py`.
No la escribas a mano.

## Hallazgo 5 · Firestore async

```python
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

cliente = firestore.AsyncClient(project=..., database=...)
```

- `find_nearest(vector_field, query_vector, limit, distance_measure, *,
  distance_result_field=None, distance_threshold=None)`
- **Los filtros `where()` van ANTES de `find_nearest`.** Filtrar después
  desperdicia cupo de resultados.
- **`get_all()` devuelve un generador asincrónico, no una corrutina.** Se recorre
  con `async for`, no con `await`. Este error ya se cometió una vez en este
  repositorio y lo detectó `mypy`, no un test.
- Límite de **1 MiB por documento**. Es la razón de que el checkpointer separe los
  valores de canal en blobs.
- Un índice vectorial fija su **dimensión** al crearse. El proyecto declara 768 en
  `firestore.indexes.json`. Cambiar a un modelo de embeddings con otra dimensión
  obliga a recrear el índice y reindexar todo el corpus.

## Hallazgo 6 · Contrato del checkpointer

Ya está implementado, pero si necesitás tocarlo:

- `serde.dumps_typed(obj) -> (str, bytes)` y `loads_typed((str, bytes))`
- `Checkpoint` es un `TypedDict`; `channel_values` se guarda aparte, por canal y
  versión
- `put_writes` recibe canales especiales con índices negativos fijos definidos en
  `WRITES_IDX_MAP`; `__interrupt__` es uno de ellos y es donde aterriza el payload
  de una pausa
- Existe el helper `get_checkpoint_metadata(config, metadata)`

## Hallazgo 7 · `with_fallbacks()` y qué acepta `create_agent`

Verificado por ejecución el **2026-08-06**.

`Runnable.with_fallbacks()` devuelve un `RunnableWithFallbacks`, que **no es un
`BaseChatModel`**:

```python
conf = modelo.with_fallbacks([respaldo])
isinstance(conf, BaseChatModel)  # False
```

`create_agent` anota `model: str | BaseChatModel`, así que la lectura obvia es
que el envoltorio no sirve para armar un agente. **Es falsa.** Se comprobó
ejecutando el agente completo —con `bind_tools` y una invocación de herramienta—
y funciona: la resolución es por duck-typing, no por el tipo declarado.

Consecuencia para este repositorio: `Gateway.chat()` no puede anotar
`BaseChatModel`, porque con respaldo configurado devuelve otra cosa. Anota
`ModeloDeChat = Runnable[LanguageModelInput, BaseMessage]`, que es el supertipo
que ambos satisfacen. **Sin respaldo devuelve un `BaseChatModel` de verdad**, y
hay un test que lo fija.

## Hallazgo 8 · Las clases de un mismo paquete no aceptan los mismos argumentos

`ChatGoogleGenerativeAI` y `GoogleGenerativeAIEmbeddings` están las dos en
`langchain-google-genai` y difieren:

| Clase | Campo | Alias |
|---|---|---|
| `ChatGoogleGenerativeAI` | `google_api_key` | — |
| `GoogleGenerativeAIEmbeddings` | `google_api_key` | `api_key` |

En runtime las dos aceptan `google_api_key=` porque declaran
`populate_by_name=True`, pero **`mypy` solo conoce el alias** y rechaza la
segunda. Lo mismo con `ChatOpenAI`, cuyo campo es `model_name` con alias `model`,
y `AzureChatOpenAI`, con `deployment_name`/`azure_deployment` y
`openai_api_version`/`api_version`.

Además las claves se declaran como `SecretStr`, no `str`: es deliberado del lado
de LangChain, para que un `repr()` accidental en un log o un traceback no
imprima la credencial. El gateway las envuelve con `_secreto()` en lugar de
silenciar el tipo.

**Regla:** antes de pasar un argumento a un adapter, mirar `model_fields` y usar
el alias. Escribir el nombre «obvio» produce código que corre y que `mypy`
rechaza, o —peor— que `mypy` acepta y el adapter ignora en silencio.

## Hallazgo 9 · `from __future__ import annotations` rompe el estado de LangGraph

Verificado contra Python 3.11 el **2026-08-06**.

Con las anotaciones diferidas, las de un `TypedDict` quedan como **cadenas** y
`NotRequired` no se resuelve:

```python
class ConFuturo(TypedDict):  # con `from __future__ import annotations`
    b: NotRequired[str]


ConFuturo.__optional_keys__  # frozenset()  ← vacío


class SinFuturo(TypedDict):  # sin él
    b: NotRequired[str]


SinFuturo.__optional_keys__  # frozenset({'b'})
```

No es cosmético: **LangGraph inspecciona las anotaciones del esquema de estado en
tiempo de ejecución** —para descubrir el reductor `add_messages`, entre otras
cosas— y una anotación que es una cadena no le dice nada.

Por eso `packages/synapseflow/agents/state.py` es el único módulo del paquete sin
`from __future__ import annotations`, y lleva el motivo escrito en su docstring.
Un `ruff` que lo agregara «por consistencia» rompería el grafo en silencio.

---

# Errores ya cometidos en este repositorio

No los repitas.

| Error | Qué pasó | Lección |
|---|---|---|
| `await` sobre `get_all()` | Devuelve un generador asincrónico. La función habría fallado la primera vez que se ejecutara. Ningún test la cubría. | Correr `mypy` antes de commitear |
| Un event loop por test | Los tests pasaban de a uno y fallaban en conjunto. El impulso fue "arreglar" el cliente de Firestore. | El problema estaba en el arnés de pruebas, no en el código |
| `pytest -m "not emulator"` documentado como suite unitaria | Deselecciona los 8 tests y no corre ninguno. | Ejecutar lo que se documenta antes de documentarlo |
| Regla de cache sobre `/index.html` | Con `cleanUrls` la ruta servida es `/`, así que la regla nunca coincidía | Verificar los headers servidos, no la configuración escrita |
| Documentar código inexistente | Un manual describía módulos no construidos sin avisarlo | Marcar siempre qué está implementado y qué es diseño |
| Firma de `description` en el gate | El compilador la escribía con un argumento y el middleware la llama con tres. El gate lanzaba `TypeError` en la primera acción irreversible. La capa de ontología figuraba ✅ en el README y no tenía un solo test. | Una fila ✅ sin test es una afirmación, no un hecho |
| Lista blanca de placeholders en `approval_prompt` | Se admitían `{tag}` y `{prioridad}` «aportados por la entidad objetivo». Nada los aportaba: el gate mostraba «no informado» donde iba el equipo. | Una degradación silenciosa no la reporta nadie |

---

# Qué NO hacer

- **No instalar `langchain-community`** por un solo retriever. Se implementa el
  que haga falta.
- **No reemplazar las integraciones propias de Firestore** por el paquete
  oficial: no es compatible.
- **No escribir gates de aprobación a mano.** Salen de la ontología.
- **No agregar chequeos de permisos dentro de las herramientas.** El catálogo se
  filtra por rol antes de dárselo al modelo; eso es más fuerte y no se puede
  olvidar.
- **No poner clientes ni conexiones en el estado del grafo.** Tiene que ser
  serializable.
- **No dejar que el modelo calcule magnitudes** que después firma un ingeniero.
- **No publicar** `taligent-ai-engineer.md`, `cv/` ni `CLAUDE.md`: están en
  `.gitignore` a propósito.
