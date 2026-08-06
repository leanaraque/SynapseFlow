# Contribuir a SynapseFlow

Gracias por el interés. Este documento explica cómo trabajar sobre el proyecto y
qué se espera de un cambio antes de que se integre.

## Preparar el entorno

Requiere **Python 3.11 o superior**. Para los tests de integración hacen falta
además la [CLI de Firebase](https://firebase.google.com/docs/cli) y un JDK, que
es lo que usa el emulador de Firestore.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Verificá que quedó bien:

```bash
synapseflow ontology validate
pytest -m "not emulator"
```

## Correr los tests

```bash
# Unitarios: sin dependencias externas
pytest -m "not emulator"

# Todo, incluyendo integración contra Firestore
firebase emulators:start --only firestore --project synapseflow-5fc52
pytest
```

Los tests marcados `emulator` se saltean solos con un mensaje claro si el
emulador no está corriendo, en lugar de fallar con un timeout de gRPC.

Los marcados `live_llm` consumen cuota real de un proveedor y no corren en CI.

## Estilo

```bash
ruff check .
ruff format .
mypy packages/synapseflow
```

Las tres cosas corren en CI. Un cambio que no pasa `ruff check` no se integra.

Convenciones del proyecto:

- **El código y los comentarios están en español.** Es deliberado: el dominio es
  normativa técnica argentina e internacional traducida, y mezclar idiomas entre
  el YAML del dominio y el código que lo interpreta genera fricción real al leer.
- Nombres de símbolos públicos de LangChain y LangGraph se dejan como están
  (`BaseCheckpointSaver`, `interrupt`, `AgentMiddleware`).
- Los comentarios explican *por qué*, no *qué*. Si un comentario parafrasea la
  línea que tiene debajo, sobra.
- Type hints en todo lo público. `from __future__ import annotations` arriba.

## README bilingüe

El README existe en dos idiomas: [`README.md`](README.md) en inglés, que es el
que GitHub muestra por defecto, y [`README.es.md`](README.es.md) en español.

- **`README.md` es la fuente de verdad.** Ante una discrepancia, vale el inglés.
- **Los dos se actualizan en el mismo PR.** Hay un job de CI que falla si tocás
  uno solo.
- **Los comandos y las salidas no se traducen nunca.** Un `pytest -m "not
  emulator"` o la salida de la CLI son idénticos en los dos archivos. Si alguna
  vez difieren, uno de los dos está mintiendo sobre lo que hace el proyecto.
- **Sí se traduce la prosa que vive dentro de los bloques**: comentarios,
  etiquetas del diagrama Mermaid y las descripciones del árbol de directorios.
  Un diagrama con etiquetas en español dentro del README en inglés se lee mal.
  Lo que no cambia son las rutas, los nombres de archivo y los comandos.
- **La estructura de secciones y las tablas se mantienen en el mismo orden.** Es
  lo que hace que la desincronización se vea a simple vista al comparar los dos
  archivos.
- Los enlaces internos usan anclas propias de cada archivo, porque los títulos
  difieren entre idiomas.

El resto de la documentación —ADRs, este archivo, `SECURITY.md`— está solo en
español a propósito. Traducir todo se desincroniza al segundo cambio; el README
es la puerta de entrada y es lo único que justifica el costo.

## Modificar la ontología

La ontología del dominio
([`oil_and_gas.yaml`](packages/synapseflow/ontology/definitions/oil_and_gas.yaml))
no es un archivo de configuración cualquiera: de ahí se derivan los permisos, la
clasificación de datos y los gates de aprobación humana.

El meta-esquema fuerza invariantes que **no se pueden relajar**:

- Toda acción con `reversible: false` tiene que declarar `requires_approval: true`.
- Toda acción con `requires_approval: true` tiene que declarar `approver_roles`
  no vacío.
- Ninguna acción de lectura puede marcarse como irreversible.
- Toda referencia a un rol, una entidad o una clasificación tiene que resolver.

Si tu cambio choca contra una de estas reglas, el problema casi siempre está en
el cambio, no en la regla. Si de verdad hace falta relajar una invariante, eso
es una decisión de arquitectura: abrí un issue y discutámosla antes.

Después de editar el YAML:

```bash
synapseflow ontology validate
synapseflow ontology tools --role inspector    # revisá que el RBAC quedó como esperabas
pytest -m "not emulator"
```

## Decisiones de arquitectura

Todo cambio que altere una decisión estructural necesita un ADR en
[`docs/adr/`](docs/adr). El formato está en los cinco existentes; lo que se pide
de cada uno:

- **Contexto**: qué problema real fuerza la decisión.
- **Alternativas consideradas**: cuáles se evaluaron y por qué se descartaron.
  Un ADR sin alternativas descartadas no documenta una decisión, documenta un
  hecho consumado.
- **Consecuencias**, incluyendo las que juegan en contra. Un ADR que solo lista
  ventajas no es honesto y no sirve para revisarlo dentro de seis meses.
- **Verificación**: qué test o qué evidencia sostiene que la decisión funciona.

## Pull requests

- Ramas desde `main`, con nombre descriptivo: `rag/retriever-hibrido`,
  `fix/checkpointer-borrado-huerfano`.
- Un PR por cambio conceptual. Un PR que toca la ontología, el gateway y el
  frontend es tres PRs.
- Mensajes de commit en imperativo, explicando el *por qué* en el cuerpo. El
  *qué* ya está en el diff.
- Si el cambio afecta comportamiento, tiene que venir con un test que falle sin
  el cambio.
- Actualizá la tabla de [estado del proyecto](README.md#estado-del-proyecto) si
  tu cambio mueve un componente de 📋 a 🚧 o a ✅.

## Reportar problemas

Antes de abrir un issue, incluí la versión de Python, el sistema operativo y la
salida de `synapseflow ontology validate`. Si es un fallo de los tests de
integración, aclaró si el emulador estaba corriendo.

Para vulnerabilidades de seguridad **no abras un issue público**: seguí el
procedimiento de [SECURITY.md](SECURITY.md).
