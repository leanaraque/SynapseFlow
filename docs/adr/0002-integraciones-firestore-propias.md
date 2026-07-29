# ADR-0002 · Implementar las integraciones de Firestore contra las interfaces de LangChain, en lugar de usar el paquete oficial

- **Estado:** aceptada
- **Fecha:** 2026-07-29
- **Decide:** Leandro Araque

## Contexto

SynapseFlow necesita tres piezas de persistencia sobre Firestore:

1. Un **vector store** para el corpus de normativa técnica.
2. Un **checkpointer** de LangGraph, para que una conversación interrumpida por
   un gate de aprobación humana sobreviva al reinicio del proceso.
3. Un **store** de memoria de largo plazo, para preferencias y contexto
   persistente por usuario.

Existe un paquete oficial, `langchain-google-firestore`, que provee
`FirestoreVectorStore`, `FirestoreChatMessageHistory` y `FirestoreLoader`. Fue
la primera opción evaluada.

Al resolver dependencias apareció el bloqueo:

```
langchain-google-firestore 0.5.0 requiere langchain-core <1.0.0,>=0.1.1
```

El proyecto usa `langchain-core` 1.5.2 y `langgraph` 1.2.10. El paquete oficial
todavía no acompañó el salto a la línea 1.x. Además, cubre solo la primera de
las tres piezas: no aporta checkpointer ni store.

## Alternativas consideradas

**A. Fijar el proyecto en `langchain-core` 0.3.x** para poder usar el paquete
oficial. Descartada: renuncia a las APIs de la línea 1.x —entre ellas el modelo
de contenido estandarizado y las mejoras de `interrupt()` en LangGraph 1.x— y
arranca el proyecto con deuda de versión el día uno.

**B. Usar una vector DB externa** (Pinecone, Qdrant, pgvector sobre Cloud SQL),
que sí tiene integración vigente en 1.x. Descartada: agrega un servicio, un
costo y un perímetro de datos más para gobernar. Firestore ya está en el
proyecto, ya tiene búsqueda vectorial nativa (`find_nearest`) y ya está dentro
del límite de cumplimiento que el diseño define.

**C. Implementar las tres integraciones contra las interfaces abstractas de
LangChain,** usando el SDK `google-cloud-firestore` directamente.

## Decisión

Se toma la alternativa **C**.

Se implementan en `packages/synapseflow/persistence/`:

| Clase | Interfaz que implementa | Origen |
|---|---|---|
| `FirestoreVectorStore` | `langchain_core.vectorstores.VectorStore` | propia |
| `FirestoreSaver` | `langgraph.checkpoint.base.BaseCheckpointSaver` | propia |
| `FirestoreStore` | `langgraph.store.base.BaseStore` | propia |

La búsqueda vectorial usa el operador nativo `find_nearest` de Firestore, con
un índice vectorial declarado en `firestore.indexes.json`.

## Consecuencias

**A favor**

- El proyecto queda en la línea 1.x del ecosistema, sin deuda de versión.
- Las tres piezas comparten un único cliente de Firestore, una única
  configuración de credenciales y un único punto donde se aplica la política de
  clasificación de datos.
- Programar contra la interfaz abstracta —y no contra una implementación
  concreta— mantiene el resto del código intercambiable: sustituir Firestore por
  otro backend es reemplazar estas tres clases, sin tocar los agentes.
- No se agrega ningún servicio al perímetro de datos.

**En contra**

- Es código propio a mantener. La mitigación es el alcance: se implementa el
  subconjunto de cada interfaz que la plataforma realmente ejerce, con tests
  contra el emulador de Firestore, no la superficie completa.
- Si `langchain-google-firestore` se actualiza a 1.x, conviene reevaluar al
  menos la pieza de vector store. Queda como deuda técnica declarada.

## Verificación

- Tests de contrato de cada clase contra el emulador de Firestore, en
  `tests/persistence/`.
- El test del checkpointer incluye el escenario que motiva su existencia:
  interrumpir el grafo en un gate de aprobación, destruir el proceso, y reanudar
  desde el checkpoint con el estado intacto.
