# F3 · RAG con citas obligatorias

**Depende de:** F0 (corpus) y F1 (embeddings vía gateway).
**Cierra el compromiso 4:** sin cita no hay respuesta.

## Por qué esta fase existe

Un modelo puede producir una respuesta que suene técnica y correcta pero no esté
respaldada por ningún fragmento recuperado. En un dominio donde la respuesta
puede terminar en una parada de planta, eso es inaceptable.

La regla del proyecto: **toda afirmación normativa viene con documento y sección,
y un verificador comprueba ese respaldo antes de emitir.** Si no lo hay, el
sistema dice que no sabe.

---

## F3.1 · Ingesta y troceado del corpus

**Produce:** `packages/synapseflow/rag/ingesta.py`

```python
def trocear(documento: DocumentoFuente) -> list[Document]: ...
async def indexar(documentos: list[Document]) -> int: ...
```

**El troceado no puede ser ciego.** Cortar cada 1000 caracteres parte cláusulas
por la mitad y produce fragmentos que citan una sección a la que ya no
pertenecen. Trocear **por sección**, usando los encabezados del Markdown, y
subdividir solo si una sección excede el tamaño máximo.

Cada fragmento conserva en su metadata: `doc_id`, `titulo`, `seccion`,
`tipo_documento`, `vigencia`. **Sin `seccion` no hay cita posible**, así que un
fragmento sin sección es un error de ingesta, no un caso a tolerar.

Usar `RecursiveCharacterTextSplitter` de `langchain_text_splitters` para la
subdivisión interna.

**Verificar:** `pytest tests/rag/test_ingesta.py -v`

Test clave: ningún fragmento queda sin `seccion`.

---

## F3.2 · Retriever híbrido

**Produce:** `packages/synapseflow/rag/retrievers.py`

Dos recuperaciones combinadas, porque fallan de manera distinta:

- **Vectorial** (`FirestoreVectorStore`, ya existe): encuentra por significado.
  «Adelgazamiento de pared» recupera «pérdida de espesor».
- **Léxica** (BM25): encuentra por término exacto. Es la que acierta cuando el
  usuario escribe `P-2101-A` o `API 570 §7.4`, donde el significado no ayuda.

```python
class BM25Retriever(BaseRetriever):
    """Recuperación léxica sobre rank_bm25.

    Propio y no de langchain-community: ese paquete no está instalado y no se
    va a instalar por un solo retriever. Ver docs/plan/00-convenciones.md.
    """


def construir_retriever(filtros: dict | None = None) -> BaseRetriever:
    """Ensemble de vectorial + léxico, con compresión contextual encima."""
```

`EnsembleRetriever` y `ContextualCompressionRetriever` vienen de
**`langchain_classic.retrievers`**, no de `langchain.retrievers`, que ya no
existe.

**Los filtros van antes de la búsqueda vectorial**, no después:

```python
consulta = coleccion.where(filter=FieldFilter("vigencia", "==", "vigente"))
vector_query = consulta.find_nearest(...)
```

Si pedís 6 fragmentos y después descartás los derogados, te pueden quedar 2.
Filtrando primero, los 6 son todos utilizables.

**Verificar:** `pytest tests/rag/test_retrievers.py -v`

---

## F3.3 · Extracción y validación de citas

**Produce:** `packages/synapseflow/rag/citas.py`

```python
class Cita(BaseModel):
    doc_id: str
    seccion: str
    fragmento_id: str


def extraer_citas(texto: str) -> list[Cita]: ...
def validar_citas(citas: list[Cita], recuperados: list[Document]) -> ResultadoValidacion: ...
```

`validar_citas` responde una pregunta concreta: **¿cada cita corresponde a un
fragmento que efectivamente se recuperó?** Una cita a un documento que el sistema
nunca leyó es una alucinación con formato de rigor, y es peor que no citar.

**Verificar:** `pytest tests/rag/test_citas.py -v`

Incluir un test con una cita inventada que no está entre los recuperados.

---

## F3.4 · Verificador de fundamento

**Produce:** `packages/synapseflow/rag/fundamento.py`
**Cierra el compromiso 4.**

```python
class VerificadorDeFundamento:
    """Comprueba que cada afirmación tenga respaldo en el contexto recuperado."""

    async def verificar(self, respuesta: str, recuperados: list[Document]) -> Veredicto: ...
```

Usa el perfil `verifier` del gateway con **salida estructurada**: devuelve por
cada afirmación si está respaldada, y por qué fragmento.

`Veredicto` distingue tres situaciones, y la tercera es la que importa:

| Veredicto | Qué hace el sistema |
|---|---|
| `fundamentada` | Emite la respuesta |
| `parcial` | Emite, marcando qué parte no tiene respaldo |
| `sin_fundamento` | **No emite.** Responde que no encontró base en la normativa disponible |

**Verificar:** `pytest tests/rag/test_fundamento.py -v`

Con `FakeChatModel` programado para devolver cada uno de los tres veredictos.

---

## F3.5 · Tests de rechazo y de normativa derogada

**Produce:** `tests/rag/test_rechazo.py`

```python
def test_se_niega_cuando_no_hay_fundamento():
    """Negarse a responder es una MÉTRICA DE ÉXITO, no un fallo.

    Un asistente que siempre contesta algo es más peligroso que uno que a
    veces dice que no sabe.
    """


def test_no_cita_normativa_derogada():
    """El corpus incluye DEROGADO-PROC-INT-009.md justamente para esto."""
```

Preguntar algo que no está en el corpus —por ejemplo, sobre un dominio ajeno— y
verificar que el sistema se niegue en lugar de improvisar con conocimiento
general del modelo.

**Verificar:** `pytest tests/rag -v`

---

## Al cerrar F3

- Reemplazar la implementación provisoria de `buscar_normativa` de F2.2 por la
  versión con retriever híbrido y citas validadas.
- El compromiso 4 pasa a ✅.
- Actualizar ambos READMEs y el `CHANGELOG.md`.
