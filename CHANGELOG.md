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
- `packages/synapseflow/llm/callbacks.py`: `ContabilidadDeCosto` registra
  tokens, modelo, perfil, latencia y costo de cada llamada, y los vuelca a
  `llm_usage`. **El precio sale del modelo que se ejecutó, no del perfil que se
  pidió**: con un respaldo configurado, `synthesis` puede terminar corriendo en
  el modelo de otro proveedor, y cobrarle el precio del primero produciría un
  panel que miente justo cuando algo salió mal.
- Un modelo que no está en el catálogo se marca `modelo_no_catalogado` en lugar
  de sumar cero. Un costo cero indistinguible de uno real es peor que un hueco
  visible.
- La contabilidad no hace I/O durante la ejecución: acumula y vuelca en lote.
  Un grafo con supervisor y tres especialistas hace del orden de diez llamadas
  por turno, y escribir un documento por llamada agregaría latencia a lo que el
  usuario está esperando. El id del documento es el `run_id`, así que reintentar
  un volcado sobreescribe en lugar de inflar la factura reportada.
- 16 tests de contabilidad, 14 sin dependencias y 2 contra el emulador.

### Corregido

- **`gemini-3.5-flash-lite` ignora `temperature`.** Es el modelo de los perfiles
  `router` y `verifier`, y avisa por `UserWarning` que usa sampling fijo. El
  gateway sigue pidiendo temperatura cero porque los modelos que la respetan la
  necesitan, pero el determinismo de esos dos perfiles depende del proveedor y
  no de la configuración. El test que lo cubre dice ahora que verifica que se
  *pide*, no que se obtenga; medirlo de verdad es trabajo de las evals de F8.
- El modelo falso no publicaba nombre de modelo en `invocation_params`, así que
  la contabilidad de costo no habría podido testearse sin salir a la red — la
  pieza que existe para testear quedaba fuera del alcance de sus propios tests.

- `tests/llm/test_frontera.py`: **el test que convierte la convención en
  garantía.** Recorre el AST de cada módulo del paquete y falla si alguno
  importa un `ChatModel` de proveedor fuera de `synapseflow/llm/`. Se analiza el
  árbol de sintaxis y no el texto porque un `grep` daría falso positivo con la
  palabra dentro de un docstring —el propio archivo las nombra todas— y falso
  negativo con un import partido en varias líneas. Incluye su propio control
  negativo: un archivo infractor sintético que el análisis tiene que detectar, y
  la comprobación de que la frontera sí importa modelos, para que la garantía no
  pase por vacío si alguien mueve los adapters.

**Fase F1 completa.** El gateway es el único punto por donde el texto cruza el
perímetro, y ahora eso es una propiedad verificada de la estructura del código y
no una convención escrita en un README.

**Acciones del dominio — fase F2, en curso**

- `packages/synapseflow/domain/repository.py`: acceso a las colecciones del
  dominio. Los filtros se aplican en Firestore y no en Python —filtrar en
  memoria funcionaría con los 60 activos sintéticos y no con los de una
  operación real— y el límite se acota entre 1 y 50, porque lo elige el modelo:
  un `limite=10000` alucinado no puede convertirse en diez mil lecturas
  facturadas.
- `packages/synapseflow/domain/contexto.py`: `ExecutionContext`, que el plan
  daba por existente. Es la respuesta a la primera pregunta de un auditor —bajo
  la autoridad de quién se ejecutó una acción— y la razón por la que el agente
  hereda los permisos del usuario y no los de la cuenta de servicio.
- `packages/synapseflow/domain/lecturas.py`: las cuatro acciones de lectura.
  `content` lleva un resumen legible y `artifact` el detalle completo, porque lo
  que vuelve en `content` ocupa contexto del modelo en cada turno siguiente.
- **El legajo del inspector no llega al modelo**, y hay un test que lo verifica
  desde ahora aunque la redacción sistemática sea de F4: una fuga que hoy no se
  testea es una fuga que en F4 nadie va a buscar, porque para entonces la
  garantía va a figurar como resuelta.
- `buscar_normativa` filtra por vigencia **antes** de la búsqueda vectorial. El
  corpus incluye un procedimiento derogado que contradice al vigente en el
  criterio de aceptación: que aparezca como fundamento no sería un resultado de
  baja calidad sino un error normativo.

**Compromiso 3 cumplido: el modelo no calcula números.**

- `packages/synapseflow/domain/calculos.py` implementa el método de la sección 7
  de API 570 en Python determinístico. Se calculan la velocidad de corrosión de
  largo plazo y la de corto plazo, y **gobierna la mayor**: un activo que se
  corroyó despacio durante años y se aceleró en la última campaña tiene un
  problema nuevo, y promediarlo contra la historia entera lo esconde justo
  cuando importa.
- Un cálculo que no se puede hacer devuelve `None` con su diagnóstico, nunca
  cero. Una velocidad de corrosión de cero significa «este equipo no se corroe»,
  que es una afirmación fuerte y falsa, y produce una vida remanente infinita.
- Un espesor creciente se reporta como dato inconsistente en lugar de calcularse.
  Devolver la velocidad negativa daría una vida remanente positiva enorme, que
  es la respuesta más peligrosa posible: tranquiliza.
- La vida remanente negativa **no es un error**: es el caso crítico del proyecto
  y el número es válido.
- 16 tests de tabla con valores calculados a mano, sobre fechas separadas por
  múltiplos exactos de 1461 días para que la aritmética dé redonda. Uno de ellos
  reconstruye el caso `P-2101-A` con las constantes del generador y verifica que
  reproduce los 0,21 mm/año y los −1,43 años que publica el README.

- `packages/synapseflow/domain/escrituras.py`: las cuatro acciones de escritura.
  **No implementan el gate de aprobación**, y no es un olvido: se escriben como
  si la aprobación ya hubiera ocurrido, y el freno lo pone el grafo en F5 desde
  la ontología. Un chequeo acá duplicaría la lógica donde se puede olvidar y
  crearía la ilusión de dos barreras cuando la que vale es una.
- Emitir una orden que no está en borrador no vuelve a emitirla: sin eso, un
  reintento tras un timeout movilizaría la cuadrilla dos veces. Una parada de
  equipo verifica que la inspección que la sustenta exista y que sea del mismo
  activo. Ninguna acción borra estado: la parada deja el estado anterior y la
  reclasificación deja la criticidad previa.
- `tests/domain/test_catalogo.py` certifica que **F2 terminó**: `compile_tools`
  ya no lanza `CompilationError` para ningún rol. Verifica el puente YAML ↔
  código en las dos direcciones —ninguna acción declarada sin implementar,
  ninguna implementación huérfana— y el contrato de cada implementación: que sea
  async, que devuelva `ToolResult`, que reciba `ctx` como keyword-only y que su
  firma cubra todos los parámetros del YAML.

**Fase F2 completa.** Las nueve acciones del dominio tienen implementación y el
catálogo compila para los cinco roles.

**RAG con citas — fase F3 completa. Compromiso 4 cumplido.**

- `rag/ingesta.py`: el corpus se trocea **por sección**, no cada N caracteres.
  Cortar a ciegas produciría un fragmento que empieza en la cláusula 7.4 y
  termina en la 7.5 con la metadata de una sola: el sistema citaría §7.4
  respaldando una afirmación que vino de otra cláusula. Un fragmento sin
  `seccion` es un error de ingesta, no un caso a tolerar.
- `rag/retrievers.py`: recuperación híbrida vectorial + BM25. **El filtro de
  vigencia se aplica en las dos ramas.** Ponerlo solo en la búsqueda vectorial
  deja la puerta de atrás abierta: la rama léxica recuperaría el procedimiento
  derogado igual y el ensemble lo fusionaría. Hay un test sobre la rama aislada,
  otro sobre el ensemble y un control negativo que verifica que sin filtro el
  derogado sí aparece.
- `EnsembleRetriever` no acepta un `k`: devuelve la unión de las dos ramas, que
  puede llegar a veinte fragmentos donde el troceado presupuestó seis. Se acota
  con un envoltorio, y no bajando el `k` de cada rama, porque la fusión necesita
  candidatos de sobra para reordenar.
- `rag/citas.py`: las citas se validan contra **lo que efectivamente se
  recuperó**, no contra el corpus. Un modelo que cita una cláusula real que no
  estaba en su contexto no la leyó: la recordó del entrenamiento o la infirió del
  número. Se aceptan tres formatos porque el texto lo redacta un LLM.
- `rag/fundamento.py`: el verificador, en dos capas y la barata primero. Una cita
  inventada rechaza la respuesta **sin llamar al modelo** —hay un test que
  verifica que el contador de llamadas queda en cero—. Tres veredictos, y
  `parcial` es el que hace utilizable al sistema: con solo emite/no emite, una
  respuesta de cinco afirmaciones con cuatro respaldadas se descartaría entera.
- La negativa es texto fijo y no generado. La afirmación sin respaldo no se borra
  de la respuesta: se marca, porque quitarla dejaría un texto que parece completo
  y no lo es.
- `buscar_normativa` dejó de ser la versión provisoria de F2.2.
- `Gateway.estructurado(perfil, schema)` concentra en un solo lugar el cast que
  `chat()` no puede evitar al declarar `Runnable`.

**Gobernanza — fase F4 completa. Compromiso 5 cumplido.**

- `governance/rbac.py`: identidad y **autoridad de aprobación**. El catálogo
  filtra quién *propone*; los `approver_roles` deciden quién *aprueba*, y son
  conjuntos distintos. El proponente no puede aprobar su propia acción: aprobar
  la propia propuesta produce el mismo registro de auditoría que aprobar sin leer.
- `governance/pii.py`: tokenización con **contador por conversación, no hash**.
  Con cien mil legajos posibles, un hash se recupera probando todos: ofusca, no
  anonimiza. El contador da la estabilidad que el modelo necesita dentro de un
  hilo sin permitir correlacionar a la misma persona entre conversaciones.
- `governance/auditoria.py`: log append-only con `thread_id` y `checkpoint_id`,
  que son la llave para reconstruir el razonamiento y no solo el hecho. El id
  lleva un componente aleatorio: un id determinístico haría que un reintento
  sobreescribiera historia, y perder un evento es peor que duplicarlo.
- `governance/politica.py`: la política de datos deja de vivir dentro del
  gateway. El gateway aporta el hecho y la política decide, por inversión de
  dependencia — hay un test sobre el AST que impide reintroducir el ciclo.
- `governance/middleware.py`: el pipeline sobre `AgentMiddleware`. La redacción
  es propia y no `PIIMiddleware` porque sus cuatro estrategias **destruyen** el
  dato: una respuesta que dice «avisale a «REDACTED»» no le sirve a nadie.
- `tests/governance/test_frontera_datos.py`: la garantía probada sobre un agente
  que corre. Una herramienta devuelve dos legajos, y se verifica que el modelo
  nunca los vio, que sí vio los tokens, y que el usuario los recibe de vuelta.
  Incluye control negativo: con la redacción apagada, el mismo recorrido filtra.

**Grafo de agentes — fase F5 completa. LOS CINCO COMPROMISOS CUMPLIDOS.**

- `agents/state.py`: estado estrecho y serializable, con el mismo `serde` que
  usa el checkpointer. Es el único módulo del paquete sin
  `from __future__ import annotations`, porque las anotaciones diferidas dejan
  `NotRequired` sin resolver y LangGraph inspecciona el esquema en runtime.
- `agents/especialistas.py`: cada agente ve solo su subconjunto del catálogo del
  rol. Un especialista **nunca amplía permisos**: si el rol no tiene la
  herramienta, el grafo falla al construirse.
- `agents/verificador.py`: el ciclo de vuelta a normativa cuando falta
  fundamento, con techo de dos vueltas. Es una de las dos razones por las que el
  proyecto usa un motor de grafos.
- `agents/supervisor.py`: rutea con el perfil `router` —la llamada más frecuente
  del sistema— y las invariantes se aplican en Python después de que el modelo
  eligió. Una salida que no valida no mata el turno.
- `agents/graph.py`: el ensamblado. `recuperados` y `calculos` salen del
  `artifact` de las herramientas y no del texto del modelo.
- **El recorrido completo de P-2101-A frena en el gate**, y el test verifica que
  el activo siga en `en_servicio` en Firestore: un gate que se dispara después de
  la escritura no es un gate.

**Evals y CI de regresión — fase F8 completa.**

- Cuatro golden datasets con 31 casos. Las citas esperadas se contrastan contra
  el corpus: un caso que espera una sección inexistente hace la suite
  **infalsificable**, porque la métrica siempre da mal y nadie sabe si el
  problema es el sistema o el dataset.
- Cuatro evaluadores: tres determinísticos y uno con juez. Un juez que es otro
  modelo introduce varianza, y una eval que empeora pasaría a tener dos
  explicaciones. Solo la fidelidad no tiene alternativa.
- **El rechazo se penaliza en las dos direcciones.** Responder sin fundamento va
  a cero; negarse a algo que sí está va a 0,5. Sin la asimetría, un sistema que
  rechaza todo puntuaría perfecto.
- `evals/run.py` reporta por métrica **y por caso**: el promedio es para la
  comparación automática, el detalle para quien tiene que arreglarlo.
- Margen de regresión cero para las determinísticas y 0,05 para la fidelidad. Sin
  margen el CI fallaría de manera intermitente, y un CI intermitente enseña a
  reintentar hasta que pase.
- `.github/workflows/evals.yml` corre solo en los PR que tocan agentes, RAG,
  gobernanza, gateway, dominio, evals o corpus — es el único job que consume
  cuota real. Autentica por Workload Identity Federation, **sin claves**.
- `docs/04-llmops.md`: cómo se lee un reporte y qué hacer ante una regresión. El
  plan lo pedía y el documento no existía.

**Infraestructura: Firestore aprovisionado.** Base `(default)` en `nam5`, reglas
e índices desplegados, 15 índices `READY` incluidos los cuatro vectoriales de 768
dimensiones. Verificado de punta a punta contra la base real: escritura con
vector, `find_nearest` con filtro de vigencia y recuperación correcta.

**API — fase F6, en curso**

- `services/api/auth.py`: del token de Firebase Auth al `ExecutionContext`. El
  rol sale de un custom claim y **se valida contra la ontología**.
- **Un rol inválido es un rechazo, no un valor por defecto.** «Si no tiene rol,
  dale `consulta`» parece prudente —es el rol más restringido— y convierte un
  problema de aprovisionamiento de identidad en un acceso silencioso: la persona
  entra y nadie se entera de que sus claims nunca se configuraron. Lo mismo con
  un rol que no existe en el YAML, que puede ser un typo o un rol eliminado.
- Se distingue 401 de 403 con un tipo propio. No es cosmético: uno se resuelve
  volviendo a autenticar y el otro pidiendo que alguien configure los permisos.
- `services/api/main.py`: la app de FastAPI. **El grafo se construye por request
  y no se cachea**, porque depende del rol de quien pregunta y un grafo cacheado
  serviría el catálogo de un rol a otro. Lo que sí se comparte por proceso es el
  gateway —abre clientes HTTP— y el checkpointer, que mantiene el pool de gRPC.
- El tokenizador de PII es uno por conversación: compartirlo entre hilos
  correlacionaría a la misma persona entre conversaciones distintas.
- `/health` no toca Firestore ni al proveedor. Una sonda que depende de un
  servicio externo reporta caído al servicio propio cuando el que falla es el
  otro, y Cloud Run reinicia contenedores sanos.
- 30 tests. Los que importan son los negativos —sin rol, rol inexistente, rol
  vacío, header ausente o mal formado— y los que verifican que la resolución esté
  **enchufada**: una identidad correcta que ningún endpoint invoca no protege
  nada, y es una falla que no se ve leyendo `auth.py`.
- `services/api/streaming.py`: `astream_events` traducido a Server-Sent Events.
  La traducción está separada del transporte, así que se verifica sin levantar un
  servidor ni parsear texto.
- El usuario ve **qué está haciendo** el agente antes de ver la respuesta. En
  este dominio no es solo experiencia: ver qué herramientas se ejecutaron es
  parte de poder auditar lo que se respondió.
- **Las citas se emiten antes que el pedido de aprobación**, aunque el gate llega
  a mitad del flujo y las citas solo se conocen al final. Pedirle a alguien que
  apruebe una parada antes de mostrarle el fundamento es pedirle que apruebe a
  ciegas.
- **El flujo termina siempre con exactamente un evento terminal**, `fin` o
  `error`. El 200 ya salió con la primera línea, así que una excepción no puede
  volverse un 500: dejaría a la consola con una respuesta truncada y ningún
  motivo. `CancelledError` no se atrapa —no es `Exception`— para que cortar la
  conexión libere el grafo en lugar de generar un evento que nadie va a leer.
- `/api/consultas` devuelve el `thread_id` en una cabecera. Cuando lo genera el
  servidor la consola no lo conoce, y sin él no puede aprobar el gate que ese
  mismo recorrido abre.
- 40 tests del streaming. El que sostiene a los demás corre un grafo real contra
  las formas grabadas a mano: sin él, una versión nueva de LangGraph podría
  cambiar la forma de un evento y la suite seguiría en verde traduciendo algo que
  ya no existe.

- `services/api/aprobaciones.py`: la bandeja y la decisión que reanuda el grafo.
- **Lo aprobado es lo ejecutado, y es una garantía estructural.** Al aprobar no
  se mandan argumentos: el grafo retoma la llamada que ya tenía en su
  checkpoint, así que no existe el lugar donde meter otros. No hay validación
  que lo asegure porque no hace falta ninguna. Los argumentos que mande la
  consola al aprobar se ignoran.
- `editar` es la excepción explícita y se audita como tal: cambia lo que se va a
  ejecutar y queda registrado quién lo cambió, con los argumentos finales.
- **Las dos validaciones que exige el plan**, aplicadas por `governance.rbac` y
  no por la API: el rol está entre los `approver_roles` de la acción, y el
  aprobador no es el proponente. La primera sin la segunda deja que un supervisor
  proponga una parada y la apruebe él mismo.
- La bandeja filtra por autoridad con **el mismo código que decide el POST**. Una
  consulta que filtrara por rol en Firestore sería una segunda regla, y el día
  que divergieran la bandeja ofrecería algo que el POST rechaza.
- Aprobar dos veces es **409, no 403**: el problema no es quién sos, es que
  llegaste tarde. Dos supervisores mirando la misma bandeja es el caso normal, y
  un 403 los mandaría a revisar permisos que están bien.
- El log de auditoría se escribe **antes** de reanudar el grafo. Al revés, una
  caída entre la ejecución y el registro dejaría una acción irreversible
  ejecutada y sin rastro de quién la aprobó.
- Una propuesta cuya acción ya no existe en el YAML no se aprueba: quedó huérfana
  de un cambio del dominio, y los aprobadores declarados pudieron cambiar con
  ella.
- Aprobar devuelve **otro flujo SSE**, no un `ok`: es el resto del recorrido. El
  supervisor ve ejecutarse la acción que aprobó por el mismo canal que ya conoce.
- Si registrar el pendiente falla, el flujo no se cae. El usuario ya vio la
  propuesta y el checkpoint ya está guardado; perder la fila de la bandeja es un
  problema de comodidad y romper la respuesta sería uno de verdad. Se anuncia
  como un `error` propio para que no pase inadvertido.
- 37 tests. Dos corren el gate de verdad: aprobar ejecuta exactamente lo
  propuesto, y rechazar no materializa nada.
- `tests/api/firestore_en_memoria.py`: un doble de Firestore con lo que este
  proyecto invoca y nada más. Los tests de autoridad son los que más importan y
  no deberían necesitar un emulador levantado para poder correr — uno que sí lo
  necesita es uno que alguien va a saltear. **No reemplaza al emulador**: los
  tests marcados `emulator` siguen contra Firestore de verdad, que es lo que
  descubre que falta un índice o que un tipo no serializa.

- `services/api/Dockerfile`: imagen multietapa sobre `python:3.11-slim`. **Las
  dependencias se instalan antes de copiar el código**, que es la propiedad que
  más fácil se pierde y que nada delata: con el código primero, cada cambio de
  una línea reinstala el árbol entero y el build sigue funcionando.
- La etapa de construcción no llega a la imagen final: se copia el virtualenv ya
  resuelto, así que el compilador y las cabeceras quedan afuera.
- **Sin credenciales adentro.** En Cloud Run la identidad sale del servicio
  (ADC). Las claves de proveedores se inyectan por Secret Manager, nunca con
  `--set-env-vars`, que las dejaría visibles en el manifiesto del servicio.
- El proceso corre como usuario sin privilegios, escucha en el `PORT` que Cloud
  Run inyecta —cablear 8080 funciona local y falla en el despliegue— y usa un
  solo worker: Cloud Run escala por instancias, y dos workers compiten por la
  misma CPU asignada.
- `.dockerignore` excluye `.venv/` y los secretos. Sin él, un `COPY` distraído
  mete `.env` en una capa, donde queda aunque un paso posterior lo borre.
- `docs/adr/0006-cloud-run-sobre-cloud-functions.md`: por qué Cloud Run y no
  Cloud Functions, App Engine o GKE. La razón es el control sobre el arranque en
  frío con un árbol de dependencias de este tamaño; Cloud Functions de 2ª
  generación corre **encima de Cloud Run** y solo quita el acceso a la palanca.
- 22 tests sobre la imagen, en la línea del CI de evals: un `Dockerfile` que se
  degrada no falla el build, solo tarda más, pesa más o expone más.

**Consola — fase F7, en curso**

- `apps/web/`: scaffold de React 19 + Vite 8 + TypeScript, con el build saliendo
  a `dist/` — que es lo que `firebase.json` ya declara como `public`.
- **El SDK de Firebase se usa solo para Auth.** No se importa
  `firebase/firestore` en ningún lado, y no es una omisión: las reglas cierran el
  acceso directo del cliente a todas las colecciones a propósito, y el RBAC lo
  aplica la API. Hay un test que lo detiene antes, con el motivo escrito, en
  lugar de dejar que se descubra como un error opaco de permisos en el navegador.
- **Un solo módulo importa el SDK y un solo módulo llama a `fetch`.** Es el mismo
  argumento que el gateway hace sobre los proveedores de LLM: una garantía que se
  sostiene por estructura no se puede olvidar. Un `fetch` suelto que se olvide del
  `Authorization` no falla en desarrollo —el proxy puede ser permisivo— y falla
  con 401 en producción.
- El rol se le pide a la API y no se deduce del token. La ontología es la que
  dice qué habilita cada rol; un cliente que decide por su cuenta termina
  ofreciendo acciones que el backend rechaza.
- Un usuario sin rol no ve una consola vacía: ve la explicación de que es un
  problema de aprovisionamiento de identidad y a quién pedírselo.
- Las rutas de la API son relativas: en desarrollo las reenvía el proxy de Vite y
  en producción el rewrite de Hosting. Una URL cableada funciona en una máquina y
  falla en las otras dos.
- El `build` corre `tsc` antes que `vite build`. Vite transpila sin chequear
  tipos, así que sin eso `strict` es decoración.
- 19 tests sobre la frontera del cliente y el scaffold. Corren en Python, dentro
  del CI que ya existe, sin instalar node.

### Pendiente

- **La imagen todavía no se construyó.** `docker build` necesita Docker local o
  una corrida de Cloud Build, y ninguno está disponible en el entorno donde se
  escribió este commit. Los tests verifican las propiedades del `Dockerfile`, no
  que produzca una imagen que arranque. Está declarado así también en el ADR.

### Verificado contra la librería instalada

- **`create_agent` invoca el modelo con `ainvoke`, no con `astream`** —está en
  `langchain/agents/factory.py`—, así que hoy no hay eventos
  `on_chat_model_stream` y el texto llega entero en `on_chat_model_end`. Un
  traductor que solo escuchara los trozos no mostraría nunca la respuesta, y nada
  fallaría. Se emiten las dos formas sin duplicar.
- **`langgraph_node` informa el nodo interno del subgrafo**, no el nuestro: cada
  especialista es un grafo compilado que corre dentro de un nodo, así que un
  evento de herramienta dice `node = tools`. El nodo propio está en el primer
  segmento de `langgraph_checkpoint_ns`.
- **El gate llega como `on_chain_stream` con `__interrupt__` en el chunk**, y su
  valor trae `action_requests` —nombre, argumentos y descripción— junto con
  `review_configs` y las decisiones permitidas.
- **Se reanuda con `Command(resume={"decisions": [...]})`**, una decisión por
  llamada interrumpida. `approve` no lleva argumentos y ejecuta la llamada del
  checkpoint; `edit` lleva `edited_action` con nombre y args nuevos; `reject`
  admite un `message` que vuelve al modelo. Verificado de punta a punta: aprobar
  ejecuta con los argumentos propuestos, rechazar no ejecuta y editar ejecuta con
  los nuevos.

### Corregido

- El índice de `llm_usage` en `firestore.indexes.json` declaraba el campo `ts` y
  la contabilidad de costo escribe `momento`. Ningún documento tenía `ts`, así
  que el índice era inútil y una consulta por fecha habría fallado en producción.
- El corredor de evals no importaba `synapseflow.domain`, así que el registro de
  `@implements` quedaba vacío y `compile_tools` fallaba con los archivos ahí.
- Una corrida de evals donde **ningún** caso se podía ejecutar salía con código 0
  si todavía no había línea base. Un CI con el proveedor mal configurado habría
  reportado fracaso total en verde. Ahora sale con código 2 y dice qué revisar.
- **`FakeChatModel.with_structured_output` reiniciaba la cola en cada llamada.**
  Tomaba una copia local de `estructurados`, así que un grafo que pide salida
  estructurada en varios nodos recibía siempre el primer objeto programado. El
  síntoma era un ruteo que repetía destino, y desde ahí todo se desalineaba sin
  que nada fallara ruidosamente.
- El contador que indexa la cola `respuestas` se separó de `llamadas`, que
  `with_structured_output` también incrementa. Mezclar ruteo estructurado con
  generación de texto —lo que hace el grafo— desalineaba el índice y cada agente
  recibía la respuesta del anterior.
- El verificador ruteaba al nodo `emitir` y el grafo lo llamaba `acciones`.
  **LangGraph ignora un destino desconocido con un warning y termina el grafo**:
  el recorrido nunca llegaba al gate y ninguna excepción lo delataba. Ahora el
  nombre se declara una sola vez y `graph.py` lo importa.
- `RedaccionDePII` descartaba en silencio el tokenizador que le inyectaban.
  `Tokenizador` define `__len__`, así que uno recién creado es *falsy* y
  `tokenizador or Tokenizador()` lo reemplazaba por otro: la redacción funcionaba
  y la auditoría guardaba un mapa de tokenización vacío.

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

### Herramientas

- `mypy` pasó a cubrir `services`, `scripts` y `evals`, no solo el paquete. La
  capa que traduce un token en permisos es justo donde un `Any` no lo detecta
  ningún test de dominio.
- `services/` es un paquete con `__init__.py`. Como namespace package, mypy
  resolvía el mismo archivo con dos nombres —`api.auth` y `services.api.auth`— y
  abortaba el análisis.
- Se declaró `known-first-party` en la configuración de isort. `src` hace que
  ruff busque paquetes *dentro* de esos directorios, así que `services.api`
  quedaba clasificado como dependencia externa y se ordenaba junto a pytest: el
  orden de los imports dependía de qué subdirectorios existieran.

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
