# ADR-0003 · La ontología del dominio es declarativa y vive fuera del código

- **Estado:** aceptada
- **Fecha:** 2026-07-29
- **Decide:** Leandro Araque

## Contexto

Un agente que opera sobre un dominio empresarial necesita cinco cosas que
tienden a escribirse por separado y a desincronizarse:

1. **Herramientas** que el modelo pueda invocar, con descripciones lo bastante
   buenas como para que elija bien.
2. **Validación** de los argumentos que el modelo produce, porque un LLM
   propone strings, no tipos.
3. **Permisos**: qué rol puede ejecutar qué.
4. **Clasificación de datos**: qué campos no pueden salir hacia un proveedor
   externo.
5. **Reversibilidad**: qué acciones exigen aprobación humana.

El patrón habitual es un archivo de Python por herramienta, con el decorador
`@tool`, un modelo Pydantic al lado, un `if rol not in (...)` adentro y la
clasificación de datos documentada en un wiki. Cinco fuentes de verdad para el
mismo dominio.

El costo aparece cuando el dominio cambia. Se agrega un campo con el legajo de
una persona a una entidad y hay que acordarse de: sumarlo al modelo Pydantic,
marcarlo en el redactor de PII, revisar qué roles ven esa entidad, y actualizar
la descripción de tres herramientas. Olvidarse del segundo paso no rompe ningún
test: solamente filtra datos personales hacia un proveedor externo, en silencio.

## Decisión

El dominio se declara en un único archivo YAML
—[`oil_and_gas.yaml`](../../packages/synapseflow/ontology/definitions/oil_and_gas.yaml)—
con tres secciones: `entities`, `relations` y `actions`, más los catálogos de
`roles` y `classification_levels`.

En el arranque, `synapseflow.ontology` compila ese archivo y produce:

```
oil_and_gas.yaml
      │
      ├──► modelos Pydantic por entidad          (validación de argumentos)
      ├──► StructuredTool de LangChain por acción (catálogo del agente)
      ├──► matriz rol × acción                    (mínimo privilegio)
      ├──► conjunto de campos PII/restricted      (redacción)
      └──► envoltura de interrupt() en LangGraph  (gates de aprobación)
```

Las propiedades que gobiernan el comportamiento son campos del YAML, no
convenciones:

```yaml
- id: solicitar_parada_equipo
  effect: write
  reversible: false          # ─┐ el compilador lee esto y envuelve
  requires_approval: true    # ─┘ la acción en un interrupt()
  approver_roles: [supervisor_mantenimiento]
  allowed_roles: [inspector, supervisor_mantenimiento]
```

## Por qué esto, y no un decorador más expresivo

Se consideró resolverlo en Python con decoradores enriquecidos
(`@action(reversible=False, roles=[...])`). Es menos código de infraestructura y
mantiene el tipado estático de punta a punta.

Se descartó por dos razones.

La primera es **quién escribe el dominio**. El modelo de activos de una
operación de petróleo y gas lo conoce un ingeniero de integridad, no un
desarrollador de Python. Un YAML comentado es un artefacto que ese ingeniero
puede leer, corregir y aprobar en un pull request. Un decorador no.

La segunda es **la auditoría**. Cuando un auditor pregunta qué acciones
irreversibles existen y quién las puede aprobar, la respuesta es un archivo de
600 líneas, versionado en git, con el historial de quién cambió cada permiso y
cuándo. No hay que leer el código para saber qué hace el sistema.

Como efecto lateral, cambiar de dominio deja de ser un refactor. Es escribir
otro YAML.

## Consecuencias

**A favor**

- Una sola fuente de verdad. Un campo nuevo marcado `pii: true` queda redactado
  en todas las rutas de salida sin tocar el redactor.
- Es imposible declarar una acción irreversible y olvidarse del gate de
  aprobación: el gate lo pone el compilador, no el autor de la herramienta.
- El dominio es revisable por gente de negocio y auditable sin leer Python.
- La plataforma queda genuinamente multi-dominio.

**En contra**

- Se pierde el tipado estático sobre las herramientas: los modelos se construyen
  en runtime, así que `mypy` no ve los argumentos de cada acción. Se mitiga con
  validación estricta del propio YAML contra un meta-esquema Pydantic al
  arrancar, que falla ruidosamente y temprano ante una definición inválida.
- Hay un compilador que mantener y entender: es infraestructura que no existiría
  con decoradores.
- Un error de tipeo en un `enum` del YAML se descubre al arrancar, no al
  compilar.

## Verificación

- `tests/ontology/test_meta_schema.py` valida que el YAML del dominio cumpla el
  meta-esquema, y que toda acción con `reversible: false` declare
  `approver_roles` no vacío.
- `tests/ontology/test_rbac.py` recorre la matriz rol × acción y verifica que
  ningún rol pueda ejecutar una acción por encima de su `max_classification`.
- `tests/ontology/test_toolgen.py` verifica que cada acción produzca una tool
  invocable y que las irreversibles queden envueltas en el gate.
