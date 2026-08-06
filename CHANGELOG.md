# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El proyecto sigue [Versionado Semántico](https://semver.org/lang/es/).

## [No publicado]

### Agregado

**Datos del dominio — fase F0 completa**

- `scripts/generar_datos.py`: generador de instalaciones, activos, inspecciones
  y órdenes de trabajo. Reproducible por semilla —dos corridas con la misma
  semilla producen archivos idénticos byte a byte— y con coherencia temporal:
  los espesores de un activo decrecen a una velocidad de corrosión propia de su
  fluido de proceso. Valida su propia salida contra la ontología antes de
  escribir, de modo que una divergencia futura entre el YAML y el generador
  falle en la corrida y no en la ejecución de un agente.
- El caso `P-2101-A` está fijo y no depende de la semilla: cuatro mediciones
  entre 2019 y 2026, `t_min` 7,1 mm, última medición 6,8 mm, velocidad
  0,21 mm/año y vida remanente −1,43 años. Reproduce exactamente los números
  que publica la transcripción del README.
- `data/corpus/`: seis documentos de normativa con 42 secciones citables.
  Parafrasean la estructura y el criterio de los códigos de inspección en
  servicio de API sin reproducir su texto. Incluye `PROC-INT-009`, marcado
  `derogado`, que contradice al procedimiento vigente en el criterio de
  aceptación: es lo que permite que el test de vigencia de F3.5 pruebe algo.
- `scripts/seed.py`: carga idempotente a Firestore. El id de cada documento es
  la clave natural que declara la ontología, así que una segunda corrida
  sobreescribe en lugar de duplicar. El destino se verifica antes de leer un
  archivo: sin `FIRESTORE_EMULATOR_HOST` el script se niega a correr, y tocar la
  base real exige `--permitir-produccion`.
- 45 tests de coherencia sobre tres semillas distintas. Verifican propiedades de
  los datos, no del generador: que exista un activo bajo `t_min`, que los
  espesores decrezcan, que haya un activo de una sola medición, que la
  integridad referencial se sostenga, y que el caso `P-2101-A` siga produciendo
  los números que publica el README. Incluyen el corpus: frontmatter contrastado
  contra los enums de la ontología, secciones numeradas sin repetir, y toda cita
  publicada en la documentación respaldada por una sección real.

### Corregido

- La nota de la sección de tests de ambos READMEs afirmaba que no existían tests
  ejecutables sin el emulador. Dejó de ser cierto al incorporarse los 17 tests
  de consistencia del plan.
- `.gitignore` ignoraba `data/generated/`, ruta en inglés que no correspondía a
  ninguna carpeta real; el generador escribe en `data/generado/`.

### Cambiado

- La comprobación de credenciales del proveedor de LLM dejó de ser un validador
  de `Settings` y pasó a `Settings.verificar_credenciales_del_proveedor()`, que
  llama el gateway. Atada al constructor, obligaba a tener una `GOOGLE_API_KEY`
  para leer la configuración de Firestore, y dejaba a `scripts/seed.py` sin
  poder correr contra el emulador.

### En curso

- Fase F1: gateway de LLM multi-proveedor. El catálogo de modelos, perfiles de
  tarea y precios está definido en `packages/synapseflow/llm/models.yaml`;
  falta el código.

### Deuda declarada

- La capa de ontología figura como verificada en la tabla de estado y no tiene
  tests propios. El primer commit del plan que la ejercita es `F2.5`.
- El ADR-0002 declara `FirestoreStore` entre las integraciones implementadas.
  No existe, y ningún commit del plan lo produce.

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
