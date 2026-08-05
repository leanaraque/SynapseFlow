# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El proyecto sigue [Versionado Semántico](https://semver.org/lang/es/).

## [No publicado]

### En curso

- Gateway de LLM multi-proveedor: catálogo de modelos, perfiles de tarea y
  precios definidos en `packages/synapseflow/llm/models.yaml`.

## [0.1.0] — 2026-07-29

Primera capa de la plataforma: el dominio como dato y la persistencia durable.

### Agregado

**Ontología**

- Meta-esquema Pydantic que valida el dominio al arrancar el proceso. Fuerza las
  invariantes de gobernanza: una acción irreversible sin rol aprobador no
  compila.
- Compilador de YAML al catálogo de herramientas de LangChain. Deriva los
  schemas de argumentos, el filtrado por rol y la configuración de los gates de
  aprobación humana desde una única fuente de verdad.
- Dominio de integridad de activos de petróleo y gas: 5 entidades, 5 relaciones,
  9 acciones y 5 roles, alineado con los códigos de inspección en servicio
  API 510, 570 y 653.
- Derivación automática de los campos PII y de la matriz rol × acción.

**Persistencia**

- `FirestoreVectorStore`: `VectorStore` de LangChain sobre la búsqueda vectorial
  nativa de Firestore, con filtros de igualdad aplicados antes del `find_nearest`
  e ingesta idempotente por hash de contenido.
- `FirestoreSaver`: `BaseCheckpointSaver` de LangGraph con los valores de canal
  separados en blobs por canal y versión, para no chocar contra el límite de
  1 MiB por documento.

**Herramientas**

- CLI `synapseflow` para inspeccionar el dominio sin credenciales ni red:
  `ontology validate`, `tools --role`, `roles` y `graph`.

**Infraestructura**

- Reglas de Firestore que cierran el acceso directo del cliente a todas las
  colecciones y hacen inmutable el log de auditoría.
- Índices vectoriales y compuestos declarados.
- `firebase.json` con rewrite de `/api/**` a Cloud Run.

**Documentación**

- ADR-0001 a ADR-0005, cada uno con sus alternativas descartadas, sus
  consecuencias en contra y su forma de verificación.

### Notas técnicas

- El proyecto usa **LangChain 1.x**, que cambió APIs respecto de 0.3.x. Los
  retrievers de composición migraron a `langchain-classic` y el prebuilt de
  agentes es `langchain.agents.create_agent`, con soporte de `AgentMiddleware`.
- Las integraciones de Firestore son implementación propia y no
  `langchain-google-firestore`, porque ese paquete todavía restringe
  `langchain-core` a versiones menores a 1.0. Ver
  [ADR-0002](docs/adr/0002-integraciones-firestore-propias.md).

### Verificado

- 8 tests del checkpointer contra el emulador de Firestore, incluyendo el
  escenario de human-in-the-loop que sobrevive a la muerte del proceso.
- La ontología compila 9 acciones y el mínimo privilegio se sostiene por rol.

[No publicado]: https://github.com/leanaraque/SynapseFlow/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/leanaraque/SynapseFlow/releases/tag/v0.1.0
