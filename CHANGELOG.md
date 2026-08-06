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

**Gateway de LLM — fase F1, en curso**

- `packages/synapseflow/llm/registry.py`: resuelve perfil de tarea + proveedor →
  modelo concreto y calcula el costo de una llamada desde el catálogo de
  precios. En Azure OpenAI el id sale del nombre de deployment que declara el
  entorno, porque lo define quien administra el recurso.
- **Un modelo de embeddings incompatible con el índice vectorial no se puede
  resolver.** Un índice de Firestore fija su dimensión al crearse; el desajuste
  se detecta al resolver y no a mitad de la ingesta, cuando ya obligaría a
  recrear el índice y reindexar el corpus completo. Hoy eso deja a OpenAI y
  Azure fuera para embeddings —devuelven 1536 contra las 768 declaradas— y
  Anthropic, que no tiene modelo propio, cae a Gemini por catálogo.
- `tests/llm/test_pricing_freshness.py` falla cuando los precios llevan más de
  noventa días sin verificarse contra el proveedor. Va a fallar solo, y es
  intencional: un catálogo viejo no rompe nada visible, sigue calculando mal.
- `packages/synapseflow/llm/gateway.py`: el único punto por donde el texto sale
  hacia un proveedor. Adapters para Gemini, OpenAI, Azure OpenAI y Anthropic
  detrás de `BaseChatModel`; el código pide un perfil de tarea y nunca un nombre
  de modelo. Los imports de cada SDK son perezosos, así que usar un proveedor no
  obliga a tener instalados los otros tres: Anthropic, que no es dependencia del
  proyecto, falla diciendo qué paquete instalar en lugar de un `ImportError`.
- **`SYNAPSEFLOW_ENFORCE_ZERO_TRAINING` dejó de ser una bandera inerte.** Existía
  desde el primer commit sin que ningún código la leyera. El gateway ahora
  rechaza al construirse un proveedor que el catálogo no declare como
  zero-training. Una bandera de gobernanza que no se aplica es peor que no
  tenerla: alguien la ve en `true` y concluye que la garantía está.
- Las dos verificaciones —credenciales y política— ocurren **en el constructor**
  del gateway. Al arrancar la API eso pasa en el startup, con el error visible,
  y no en la primera llamada con el usuario esperando.
- `SYNAPSEFLOW_FALLBACK_PROVIDER` permite degradar a otro proveedor con
  `with_fallbacks()`. Vacío por defecto a propósito: un respaldo silencioso manda
  el texto a un proveedor que nadie eligió. Un respaldo sin credenciales se
  descarta en lugar de encadenarse, porque encadenarlo falla igual y hace que el
  usuario vea el error del respaldo en vez del real.
- `SYNAPSEFLOW_PROVIDER=fake` levanta la plataforma entera sin credenciales de
  ningún proveedor. Es un valor del enum y no una bandera aparte para que el
  camino que ejercitan los tests sea el mismo que el de producción; el proveedor
  falso está en `models.yaml` y se resuelve por el registry como cualquier otro.
- `FakeEmbeddings` con similitud léxica real, por *hashing trick* y no por hash
  del texto entero. Es lo que va a permitir que los tests de recuperación de F3
  afirmen que el fragmento pertinente sale primero, en lugar de solo que la
  ingesta escribió algo. Usa `hashlib` y no `hash()`, que está salteado por
  proceso: con él, un corpus indexado en una corrida no sería recuperable en la
  siguiente.
- 28 tests del gateway, ninguno con red.

### En curso

- Fase F1: falta la contabilidad de tokens y costo, y el test estructural de la
  frontera de datos.

**Gobernanza de la ontología — deuda saldada**

- **El gate de aprobación lanzaba `TypeError` al dispararse.** El compilador
  emitía un `description` con la firma equivocada: el middleware lo invoca como
  `description(tool_call, state, runtime)` y estaba escrito con un solo
  argumento. Debajo había un segundo bug del mismo origen, que habría rendido
  todos los campos como «no informado». Nada lo detectaba porque la capa no
  tenía tests.
- **El meta-esquema no hacía cumplir la clasificación de datos.** Un rol de
  clasificación `public` podía recibir una herramienta que devuelve datos
  `confidential` sin que nada protestara. `can_role_read_entity` existía desde
  el primer commit y no lo llamaba nadie. Es la verificación que el ADR-0003
  declaraba desde el inicio.
- `pii_fields` ignoraba la clasificación heredada de la entidad: marcar una
  entidad entera como `restricted` no habría redactado ninguno de sus campos.
- **`synapseflow ontology validate` se caía al redirigir la salida en Windows.**
  Es el comando que el README ofrece como lo primero que funciona tras clonar.
  En Linux no ocurre, así que el CI no lo detectaba.
- `tests/ontology/` existe: 35 tests, cuatro de los cuales corren un agente real
  contra los gates derivados del YAML.

### Deuda declarada

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
