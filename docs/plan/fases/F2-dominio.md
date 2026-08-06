# F2 · Acciones del dominio

**Depende de:** F0 (necesita datos que consultar).
**Cierra el compromiso 3:** el modelo no calcula números.

## Por qué esta fase existe

Las nueve acciones están **declaradas** en el YAML del dominio pero ninguna tiene
implementación. Hoy `compile_tools` lanza `CompilationError` justamente por eso:

```
hay acciones en la ontología sin implementación registrada: [...]
Registralas con @implements o quitalas del YAML.
```

Ese error es intencional y está bien: el proyecto se niega a compilar un catálogo
incompleto. Esta fase lo resuelve.

## El contrato de una implementación

El decorador y el tipo de retorno ya existen en
`packages/synapseflow/ontology/compiler.py`:

```python
from synapseflow.ontology import ToolResult, implements


@implements("consultar_activo")
async def consultar_activo(tag: str, *, ctx: ExecutionContext) -> ToolResult:
    ...
    return ToolResult(content="texto breve para el modelo", artifact={"registros": [...]})
```

**Reglas que no se negocian:**

- Son **async**. Toda la capa de datos lo es.
- Reciben `ctx` como keyword-only: es el contexto con el usuario y su rol.
- Devuelven `ToolResult`, nunca otra cosa. El compilador lo verifica y falla si no.
- `content` es lo que lee el modelo: **breve**, porque consume ventana de contexto
  en cada turno siguiente. `artifact` lleva el detalle completo y va a la consola
  y al log de auditoría sin pasar por el modelo.
- El nombre de la función es libre; lo que vincula es el id en `@implements`.

---

## F2.1 · Repositorio de acceso a datos

**Produce:** `packages/synapseflow/domain/repository.py`

```python
class RepositorioDominio:
    """Acceso tipado a las colecciones del dominio.

    Existe para que las implementaciones de las acciones no hablen con
    Firestore directamente: si mañana cambia el backend, cambia esta clase y
    nada más.
    """

    async def activo_por_tag(self, tag: str) -> dict | None: ...
    async def listar_activos(self, **filtros) -> list[dict]: ...
    async def inspecciones_de(self, tag: str, limite: int) -> list[dict]: ...
    async def guardar_orden(self, orden: dict) -> str: ...
```

Los índices compuestos que estas consultas necesitan **ya están declarados** en
`firestore.indexes.json`. Si agregás una consulta nueva con otra combinación de
filtros, hay que declarar su índice ahí también.

**Verificar:** `pytest tests/domain/test_repository.py -v`

---

## F2.2 · Las cuatro acciones de lectura

**Produce:** `packages/synapseflow/domain/lecturas.py`

| Acción | Qué hace |
|---|---|
| `consultar_activo` | Ficha técnica por TAG |
| `listar_activos` | Filtrado por instalación, clase, criticidad, estado |
| `historial_inspecciones` | Ordenado de más reciente a más antigua |
| `buscar_normativa` | Recuperación sobre el corpus |

**Sobre `buscar_normativa`:** el RAG llega en F3. Acá se deja la firma y el
contrato; puede devolver una recuperación simple con
`FirestoreVectorStore.asimilarity_search`, que ya existe. F3 la reemplaza por la
versión híbrida con citas verificadas.

**Detalle de PII.** Las inspecciones traen `inspector_legajo`, que la ontología
marca como `restricted`. En `content` **no debe aparecer**; en `artifact` sí,
porque el artifact no va al modelo. La redacción sistemática llega en F4, pero no
hay razón para exponerlo antes.

**Verificar:** `pytest tests/domain/test_lecturas.py -v`

---

## F2.3 · Cálculo determinístico de vida remanente

**Produce:** `packages/synapseflow/domain/calculos.py`
**Cierra el compromiso 3.**

Este es el commit conceptualmente más importante de la fase. El modelo **no
estima** velocidad de corrosión ni vida remanente: recibe el número ya calculado
como hecho, junto con las mediciones que lo sustentan.

```python
def velocidad_de_corrosion(mediciones: list[Medicion]) -> float | None:
    """Milímetros por año, según el método de API 570 sección 7.

    Devuelve None cuando no se puede calcular: hace falta al menos dos
    mediciones en fechas distintas.
    """


def vida_remanente(espesor_actual: float, t_min: float, velocidad: float) -> float:
    """Años hasta alcanzar el espesor mínimo. Negativo si ya se pasó."""
```

**Casos de borde que el test exige cubrir:**

| Caso | Comportamiento correcto |
|---|---|
| Una sola medición | Devolver `None`, no cero. Cero significaría «no se corroe». |
| Dos mediciones el mismo día | División por cero: devolver `None` |
| Espesor **creciente** | Medición sospechosa. Reportarlo explícitamente en vez de devolver una velocidad negativa que produciría una vida remanente absurda. |
| Velocidad cero | Vida remanente infinita: devolver `None` y decir por qué |
| Espesor actual bajo `t_min` | Vida remanente negativa. **Es un resultado válido y es el caso crítico**, no un error. |

**Lo que devuelve `ToolResult`:** en `content`, el número y la conclusión en una
línea. En `artifact`, las mediciones usadas, las fechas y los pasos intermedios,
para que un ingeniero pueda auditar el cálculo.

**Verificar:** `pytest tests/domain/test_calculos.py -v`

Los tests son de tabla, con valores calculados a mano. Este módulo no puede tener
un error silencioso: un número mal firma un informe.

---

## F2.4 · Las cuatro acciones de escritura

**Produce:** `packages/synapseflow/domain/escrituras.py`

| Acción | Reversible | Aprobación |
|---|---|---|
| `registrar_borrador_ot` | sí | no |
| `emitir_orden_trabajo` | **no** | sí |
| `solicitar_parada_equipo` | **no** | sí |
| `reclasificar_criticidad` | **no** | sí |

**Importante:** estas funciones **no implementan el gate**. Se escriben como si
la aprobación ya hubiera ocurrido. El freno lo pone el grafo en F5, a partir de
la ontología. Si acá agregás un chequeo de aprobación, estás duplicando la
lógica en un lugar donde se puede olvidar.

Lo que sí verifican: que `ctx` traiga un usuario, y que el rol esté entre los
`allowed_roles` de la acción. Es defensa en profundidad, no la barrera principal
—esa es el filtrado del catálogo.

**Verificar:** `pytest tests/domain/test_escrituras.py -v`

Incluir un test negativo: invocar sin `ctx` debe fallar.

---

## F2.5 · El catálogo completo compila

**Produce:** `tests/domain/test_catalogo.py`

```python
def test_las_nueve_acciones_compilan():
    """compile_tools ya no lanza CompilationError.

    Antes de F2 fallaba porque ninguna acción tenía implementación. Este test
    es el que certifica que la fase terminó.
    """
    onto = get_ontology()
    for rol in [r.id for r in onto.roles]:
        herramientas = compile_tools(onto, rol)
        assert len(herramientas) == len(onto.actions_for_role(rol))
```

Agregar también un test que verifique que **toda** implementación registrada
devuelve `ToolResult`, recorriendo el registro.

**Verificar:** `pytest tests/domain -v`

---

## Al cerrar F2

- El compromiso 3 pasa a ✅ en `docs/06-mapa-de-accion.md` y en `scripts/estado.py`
  lo detecta solo.
- Actualizar ambos READMEs y el `CHANGELOG.md`.
