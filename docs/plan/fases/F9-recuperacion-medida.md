# F9 · Recuperación y evaluación, medidas

**Depende de:** F3 (la recuperación híbrida existe) y F8 (la suite de evaluación
existe).
**Estado al escribir esta fase:** F0 a F8 completas, 43 de 43 commits. Nada de
F9 está empezado.

## Por qué existe esta fase

El RAG de F3 funciona, pero nunca se **midió**. Hay cuatro preguntas que un
sistema de recuperación en producción tiene que poder contestar con números, y
hoy este proyecto solo puede contestarlas con argumentos:

| # | Pregunta | Qué hay hoy | Qué agrega F9 |
|---|---|---|---|
| 1 | Si hay información muy similar semánticamente, ¿cómo se asegura que se recupera la adecuada? | Metadatos por fragmento y filtro de vigencia | Extractor de filtros, umbral de relevancia y re-ranker: F9.4, F9.5, F9.6 |
| 2 | ¿Con qué métricas se mide el resultado del LLM para mostrárselo a un cliente? | Cuatro evaluadores propios (F8); LangSmith declarado pero sin usar | Ragas y trazas: F9.7 |
| 3 | ¿Cómo funciona el híbrido con BM25, y conviene? | Implementado en `rag/retrievers.py`, con pesos elegidos a criterio | Comparación medida de las tres ramas: F9.3 |
| 4 | ¿Cómo se reduce el costo de la base vectorial? | Se piden 768 dimensiones en vez de 3072 (`llm/models.yaml`) | Experimento de cuantización: F9.8 |

F9.1 y F9.2 no responden ninguna pregunta: son la base para medir las cuatro.

## Leé esto antes de empezar

- **El corpus es demasiado chico para medir:** 6 documentos y 42 fragmentos.
  Casi cualquier búsqueda acierta, así que ninguna mejora se nota. Por eso F9.1
  amplía el corpus antes que nada.
- **Costos.** El dueño del proyecto pidió que nada impacte en la facturación sin
  su aprobación. La clave de Gemini es de un proyecto sin facturación (tier
  gratuito), pero cualquier servicio pago nuevo (Vertex AI Ranking, Cohere,
  LangSmith pago, una base vectorial gestionada) **requiere preguntarle antes**.
  Preferí lo que corre local.
- **Los datos son sintéticos y el repositorio es público.** Todo documento nuevo
  se inventa siguiendo el estilo de `data/corpus/`. Nunca se copia texto de una
  norma real ni se usa información de un cliente.
- **Verificá cada API contra el entorno instalado**, no contra un tutorial
  (`CLAUDE.md`, regla 4, y la sección *API verificada* de
  `docs/plan/00-convenciones.md`). Vale sobre todo para Ragas, que
  cambió su API entre versiones.
- **No instales `langchain-community`.** Las convenciones lo prohíben. Si una
  dependencia nueva lo arrastra, frená y evaluá alternativas antes de seguir.

## Cómo está construida la recuperación hoy

| Pieza | Dónde | Qué hace |
|---|---|---|
| Troceado | `packages/synapseflow/rag/ingesta.py` → `trocear` | Un fragmento por sección `## N · Título`; antepone `doc_id §sección · título` al texto |
| Metadatos | mismo archivo, dict `base` | `doc_id`, `titulo`, `seccion`, `titulo_seccion`, `tipo_documento`, `vigencia` |
| Rama vectorial | `packages/synapseflow/persistence/vectorstore.py` | `find_nearest` de Firestore, coseno, filtros de igualdad **antes** de buscar; la distancia vuelve en el campo `_distancia` |
| Rama léxica | `packages/synapseflow/rag/retrievers.py` → `BM25Retriever` | `rank_bm25.BM25Okapi` en memoria, descarta puntaje ≤ 0 |
| Fusión | mismo archivo → `construir_retriever` | `EnsembleRetriever` (RRF ponderado, c = 60), pesos `PESOS = (0.6, 0.4)`, 10 candidatos por rama, corta en 6 |
| Embeddings | `packages/synapseflow/llm/models.yaml` | `gemini-embedding-001` con `output_dimensionality: 768` |
| Evals | `evals/` | Citas, cálculos y rechazo determinísticos; fidelidad con LLM-as-judge |
| Trazas | `packages/synapseflow/config.py` | `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` declarados |

Todas las mediciones de esta fase van a un documento nuevo,
`docs/07-recuperacion.md`, con una sección por experimento. Crealo en el primer
commit que lo necesite (F9.3).

---

## F9.1 · Corpus con documentos casi gemelos

**Produce:** documentos nuevos en `data/corpus/` y el campo `instalacion` en la
ingesta.

El problema de la pregunta 1 no aparece si no hay fragmentos que se parezcan.
Agregá al menos dos pares de documentos **escritos a propósito para confundir a
la búsqueda semántica**:

| Par | Cómo se parecen | En qué difieren |
|---|---|---|
| `PROC-INT-030` y `PROC-INT-031` | Mismo procedimiento de inspección, misma plantilla y vocabulario | Cada uno aplica a una instalación distinta, con frecuencias y umbrales distintos |
| `API-570-2009` (derogado) | Misma estructura que `API-570-2016` | Criterios distintos en algunas secciones, incluida la §7.4 |

No uses `PROC-INT-021`: ya está referenciado en `PROC-INT-014`.

Pasos:

1. Agregá `instalacion` como campo opcional del frontmatter, de
   `DocumentoFuente` y del dict `base` de `trocear`. Sin ese campo, el extractor
   de F9.4 no tiene contra qué filtrar.
2. Escribí los documentos con secciones `## N · Título`. Usá códigos de
   instalación que existan en los datos generados (`scripts/generar_datos.py`).
3. Actualizá los tests que cuentan documentos (`tests/rag/test_ingesta.py`
   afirma `len(leer_corpus()) == 6`).
4. Reindexá el corpus con el seed. Esto llama al modelo de embeddings una vez
   por fragmento nuevo.

**Ojo:** el caso de referencia del proyecto (la parada de `P-2101-A`, fundada en
`API-570-2016 §7.4`) tiene que seguir funcionando. El documento de 2009 va como
`vigencia: derogado` y el filtro de vigencia lo tiene que dejar afuera.

**Verificar:** `pytest tests/rag -v` y `python -m scripts.seed --dry-run`

---

## F9.2 · Set de evaluación de la recuperación

**Produce:** `evals/datasets/recuperacion.jsonl` y `evals/recuperacion.py`

Las evals de F8 miden la respuesta final. Esta mide **solo la recuperación**:
dada una pregunta, ¿qué fragmentos trae el retriever y en qué posición sale el
correcto? No genera respuestas, así que no llama al modelo de chat: solo a
embeddings, para la consulta.

Dataset: **al menos 30 preguntas**, cada una con sus fuentes esperadas (mismo
formato de fuente que `normativa.jsonl`: `"API-570-2016 §7.4"`). Repartilas en
cuatro tipos y guardá el tipo en el caso, porque cada uno castiga a una rama
distinta:

| Tipo | Ejemplo | Qué rama sufre |
|---|---|---|
| `concepto` | «¿qué hago si la pared se adelgazó más de lo admitido?» | La léxica |
| `identificador` | «¿qué dice API 570 §7.4?» | La vectorial |
| `gemelo` | Una pregunta que solo se responde bien con uno de los documentos de F9.1 | Las dos |
| `derogado` | Una pregunta cuya respuesta literal está en el documento derogado | Ninguna, si el filtro funciona |

Métricas, como funciones puras y testeadas:

- `recall_en_k(recuperadas, esperadas, k)`: proporción de fuentes esperadas que
  aparecen entre las primeras `k`.
- `mrr(recuperadas, esperadas)`: promedio de `1 / posición` de la primera fuente
  correcta; 0 si no aparece.

`python -m evals.recuperacion` imprime recall@1, recall@3, recall@6 y MRR, en
total y por tipo.

**Verificar:** `pytest tests/evals -k recuperacion -v` y `python -m evals.recuperacion`

---

## F9.3 · Comparación de ramas: vectorial, léxica e híbrida

**Produce:** la opción `--rama` en `evals/recuperacion.py` y la sección
`## Comparación de ramas` en `docs/07-recuperacion.md`.

Responde la pregunta 3 con datos. Corré el set de F9.2 con:

1. Solo vectorial.
2. Solo léxica (BM25).
3. Híbrida con los pesos actuales `(0.6, 0.4)`.
4. Híbrida con al menos otros dos pares de pesos, por ejemplo `(0.5, 0.5)` y
   `(0.7, 0.3)`.

Documentá la tabla de resultados por tipo de pregunta y la conclusión. Si otros
pesos ganan, cambiá `PESOS` en `retrievers.py` **y explicá el cambio en el
comentario que ya existe ahí**, con el número que lo justifica.

**Verificar:** `python -m evals.recuperacion --rama vectorial`, `--rama lexica` y
`--rama hibrida`

---

## F9.4 · Extractor de filtros de la pregunta

**Produce:** `packages/synapseflow/rag/extractor.py` con `extraer_filtros`.

Primera etapa de la pregunta 1. Lee la pregunta y devuelve filtros de igualdad
sobre metadatos, por ejemplo `{"instalacion": "<código>"}` o
`{"doc_id": "API-570-2016"}`.

- **Primero determinístico:** expresiones regulares para códigos de instalación,
  de equipo y de documento. Cubre la mayoría de los casos sin costo ni varianza.
- **El LLM, solo si hace falta:** salida estructurada con un esquema Pydantic,
  a través del gateway, con el perfil `router`.
- Si no encuentra nada, devuelve `{}` y la búsqueda sigue como hoy. Un filtro
  equivocado es peor que ninguno: deja afuera el fragmento correcto.

Conectalo en `construir_retriever_vigente` sin romper su firma. Medí con F9.2,
sobre todo las preguntas `gemelo`, y anotá el resultado en `docs/07-recuperacion.md`.

**Verificar:** `pytest tests/rag -k extractor -v` y `python -m evals.recuperacion`

---

## F9.5 · Umbral de relevancia

**Produce:** la constante `UMBRAL_RELEVANCIA` en `rag/retrievers.py`.

Segunda etapa de la pregunta 1. Hoy la rama vectorial devuelve siempre sus k
candidatos, aunque ninguno sirva. La distancia ya vuelve en `_distancia`
(`persistence/vectorstore.py`). Descartá los fragmentos que no alcancen el
umbral, igual que la rama léxica ya descarta los de puntaje ≤ 0.

El umbral se **calibra con F9.2**, no se elige a ojo. Barré varios valores y
elegí el que mejor equilibra recall y fragmentos de ruido. Documentá el barrido.

Cuidado con el rechazo: si el umbral es muy alto, el sistema se niega a
preguntas que sí tienen respuesta. Corré también las evals de F8
(`rechazo.jsonl`).

**Verificar:** `pytest tests/rag -v`, `python -m evals.recuperacion` y `python -m evals.run`

---

## F9.6 · Re-ranker

**Produce:** `packages/synapseflow/rag/reranker.py` con la clase `Reranker`, y
`docs/adr/0007-reranker.md`.

Tercera etapa de la pregunta 1. Un cross-encoder lee la pregunta y cada
candidato **juntos** y los reordena. Va después de la fusión: la búsqueda
híbrida trae 20 o más candidatos, el re-ranker los reordena y se cortan los 6
mejores.

El ADR decide cuál usar. Opciones a evaluar:

| Opción | A favor | En contra |
|---|---|---|
| Cross-encoder local multilingüe (por ejemplo, la familia `bge-reranker`) | Sin costo por llamada; los datos no salen | Dependencias pesadas (`torch`), imagen de Docker más grande |
| API de ranking gestionada (Vertex AI, Cohere) | Sin dependencias | **Costo: requiere aprobación del dueño** |
| LLM del gateway como re-ranker | Ya está integrado | Más lento, con varianza |

**El corpus está en español:** un re-ranker entrenado solo en inglés no sirve.

Implementalo como envoltorio de `BaseRetriever`, igual que `_Acotado` en
`retrievers.py`. Medí con F9.2 y anotá cuánto mejora cada tipo de pregunta.

**Verificar:** `pytest tests/rag -k reranker -v` y `python -m evals.recuperacion`

---

## F9.7 · Métricas con Ragas y trazas

**Produce:** `evals/ragas_corrida.py` y la sección `## Métricas con Ragas` en
`docs/07-recuperacion.md`.

Responde la pregunta 2. Corré Ragas sobre los casos de `normativa.jsonl` y
`recuperacion.jsonl`, con estas métricas:

| Métrica | Mide |
|---|---|
| `faithfulness` | Que cada afirmación de la respuesta esté respaldada por el contexto |
| `answer_relevancy` | Que la respuesta conteste lo que se preguntó |
| `context_precision` | Que lo recuperado sea relevante y esté arriba |
| `context_recall` | Que se haya recuperado todo lo necesario |

Puntos a resolver:

- **Dependencia:** agregá `ragas` en un grupo opcional, no en las dependencias
  del paquete. Verificá que no arrastre `langchain-community`; si lo arrastra,
  documentá la alternativa elegida.
- **El juez es un LLM:** usá Gemini a través del gateway, no la configuración
  por defecto de Ragas, que asume OpenAI.
- **Comparación con F8:** la fidelidad propia (`evals/evaluadores/fidelidad.py`)
  y la `faithfulness` de Ragas miden lo mismo con métodos distintos. Documentá
  cuánto coinciden. Esa es la calibración del juez.
- **Trazas:** activá `LANGSMITH_TRACING` y verificá que cada ejecución del grafo
  aparezca. Si LangSmith requiere plan pago, usá Phoenix, que corre local, y
  dejalo documentado.

Esto **no** entra al CI: cuesta llamadas al modelo y tiene varianza. Es una
corrida manual, como las evals en vivo de F8.

**Verificar:** `python -m evals.ragas_corrida`

---

## F9.8 · Experimento de cuantización

**Produce:** `scripts/experimento_cuantizacion.py` y la sección
`## Cuantización` en `docs/07-recuperacion.md`.

Responde la pregunta 4. **Firestore no permite cuantizar su índice
vectorial**, así que esto es un experimento fuera de la base, no un cambio de
producción:

1. Obtené los embeddings del corpus (de Firestore o recalculándolos) y los de
   las consultas de F9.2.
2. Con `numpy`, que ya está instalado, armá cuatro versiones: `float32`
   (referencia), `float16`, `int8` (cuantización escalar por dimensión) y
   binaria (signo de cada número).
3. Para la binaria, medí también con **re-puntuación**: buscar con binario
   muchos candidatos y reordenar con `float32`.
4. Para cada versión: bytes por vector, recall@k y MRR contra la referencia.

Documentá la tabla y la conclusión: cuánto se ahorra y cuánto se pierde.

**Fuera de alcance, para otro proyecto:** cuantización real dentro de una base
(Postgres con `pgvector`, que tiene `halfvec` y `bit`, o Qdrant).

**Verificar:** `python -m scripts.experimento_cuantizacion`
