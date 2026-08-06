# F0 · Datos sintéticos y corpus normativo

**Depende de:** nada. Se puede empezar ya.
**Habilita:** F2 (las acciones necesitan datos) y F3 (el RAG necesita corpus).

## Por qué esta fase existe

Sin datos no hay nada que consultar y sin corpus no hay nada que citar. Pero hay
una razón más específica: **el recorrido completo del proyecto depende de que
exista al menos un activo en estado crítico**. Si todos los equipos están sanos,
nunca se llega al gate de aprobación y el compromiso 2 no se puede ejercitar.

## Regla legal, no negociable

El corpus **parafrasea** la estructura y el criterio técnico de los códigos de
inspección en servicio de API. **Nunca reproduce su texto**, que es material con
derechos de autor. Se escribe con la forma real del problema —cláusulas
numeradas, umbrales, referencias cruzadas— pero con redacción propia.

Lo mismo vale para los datos: instalaciones, activos e inspecciones son
inventados y no corresponden a ninguna operación real.

---

## F0.1 · Generador de datos estructurados

**Produce:** `scripts/generar_datos.py`

**Qué generar**, respetando el esquema de
`packages/synapseflow/ontology/definitions/oil_and_gas.yaml`:

```python
def generar_instalaciones(n: int, rng) -> list[dict]: ...
def generar_activos(instalaciones, n, rng) -> list[dict]: ...
def generar_inspecciones(activos, rng) -> list[dict]: ...
def generar_ordenes(activos, inspecciones, rng) -> list[dict]: ...
```

Escala sugerida: 6 instalaciones, ~60 activos, 4 a 8 inspecciones por activo
repartidas en varios años, ~40 órdenes de trabajo.

**Lo que hace que estos datos sirvan**

- **Semilla fija.** `--semilla 42` por defecto. Dos corridas producen exactamente
  lo mismo, así que los tests son reproducibles.
- **Coherencia temporal.** Las mediciones de espesor de un activo deben describir
  una curva de corrosión creíble: monótonamente decreciente, con una velocidad
  razonable para su fluido. El cálculo de vida remanente de F2.3 se apoya en
  esto; datos aleatorios producen velocidades absurdas.
- **Al menos un activo bajo `t_min`.** Reservar el TAG **`P-2101-A`** para el caso
  crítico, con `espesor_minimo_requerido_mm: 7.1` y una última medición de
  `6.8`. Es el caso que atraviesa toda la documentación del proyecto y el que
  ejercita el recorrido completo.
- **Algunos activos cerca del límite**, para que el sistema tenga casos
  intermedios y no solo blanco o negro.
- **Un activo con una sola medición**, que hace imposible calcular velocidad de
  corrosión: es un caso de borde que F2.3 tiene que manejar.

**Verificar:** `python -m scripts.generar_datos --salida data/generado --semilla 42`

---

## F0.2 · Corpus de normativa

**Produce:** `data/corpus/*.md`

Documentos mínimos:

| Archivo | Qué contiene |
|---|---|
| `API-570.md` | Inspección de cañerías en servicio: espesores, `t_min`, vida remanente, frecuencias |
| `API-510.md` | Recipientes a presión |
| `API-653.md` | Tanques de almacenamiento |
| `PROC-INT-014.md` | Procedimiento interno de medición de espesores |
| `PROC-SEG-003.md` | Permisos de trabajo y áreas clasificadas |
| `DEROGADO-PROC-INT-009.md` | Versión anterior de un procedimiento, marcada `derogado` |

**El documento derogado no es relleno.** F3.5 tiene un test que verifica que
nunca se cite como fundamento vigente. Sin un derogado en el corpus, ese test no
prueba nada.

**Estructura obligatoria de cada fragmento.** Cada sección lleva encabezado con
número de cláusula, porque la cita del sistema es `documento §sección` y sin eso
no hay cita posible:

```markdown
---
doc_id: API-570-2016
titulo: Inspección de cañerías en servicio
tipo_documento: norma_internacional
vigencia: vigente
---

## 7.4 · Espesor por debajo del mínimo requerido

Cuando el espesor medido de un componente resulte inferior al espesor mínimo
requerido por cálculo, el componente no podrá continuar en servicio...
```

**Contenido que el corpus tiene que cubrir**, porque son las preguntas que el
sistema debe poder responder:

- Qué hacer cuando el espesor cae bajo `t_min`.
- Cómo se calcula la velocidad de corrosión y la vida remanente.
- Frecuencias de inspección según criticidad.
- Qué permisos exige un trabajo en un área clasificada.
- Cuándo corresponde escalar un hallazgo.

**Verificar:** `ls data/corpus/*.md` y que cada uno tenga frontmatter válido.

---

## F0.3 · Carga idempotente a Firestore

**Produce:** `scripts/seed.py`

**Qué implementar**

```python
async def cargar(dry_run: bool = False) -> ResumenDeCarga: ...
```

- `--dry-run` reporta qué escribiría sin escribir nada. Es lo que permite
  revisar antes de tocar la base.
- **Idempotente**: correrlo dos veces deja la misma cantidad de documentos. Para
  el corpus, `FirestoreVectorStore.aadd_texts` ya deriva el id del contenido; para
  el dominio, usar la clave natural de cada entidad (`tag`, `id_ot`, etc.).
- Respeta `FIRESTORE_EMULATOR_HOST`: por defecto debe apuntar al emulador, no a
  producción. **Escribir en la base real por accidente es el modo de falla más
  caro de este script.**

**Verificar:** `python -m scripts.seed --dry-run`

Después, con el emulador corriendo, cargar dos veces y comprobar que el conteo no
cambia.

---

## F0.4 · Tests de coherencia

**Produce:** `tests/datos/test_coherencia.py`

Estos tests no prueban el generador: prueban que **los datos generados sirven
para lo que el proyecto necesita**.

```python
def test_hay_un_activo_bajo_t_min():
    """Sin esto no se puede ejercitar el recorrido hasta el gate de aprobación."""


def test_corrosion_es_monotona():
    """Los espesores de un activo deben decrecer en el tiempo.

    Un espesor creciente indicaría una medición mal tomada; puede existir como
    caso de borde deliberado, pero no como regla.
    """


def test_hay_un_activo_con_una_sola_medicion():
    """Caso de borde: no se puede calcular velocidad de corrosión."""


def test_toda_inspeccion_apunta_a_un_activo_existente():
    """Integridad referencial de los datos generados."""
```

**Verificar:** `pytest tests/datos -v`

---

## Al cerrar F0

- Agregar `data/generado/` a `.gitignore` si el generador escribe ahí; el corpus
  de `data/corpus/` **sí** se versiona.
- Actualizar ambos READMEs y el `CHANGELOG.md`.
