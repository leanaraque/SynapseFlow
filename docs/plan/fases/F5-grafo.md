# F5 · Grafo de agentes

**Depende de:** F1, F2, F3 y F4. Es la fase que las junta.
**Cierra el compromiso 2** en ejecución real.

> ## Este es el hito principal del proyecto
>
> Al terminar F5 el sistema deja de ser piezas sueltas y hace el recorrido
> completo: pregunta sobre un activo → cálculo determinístico → fundamento
> normativo con citas → propuesta de acción irreversible → freno esperando a un
> humano.
>
> F6 y F7 lo hacen usable por otros. **F5 lo hace verdadero.** Si hay que mostrar
> el proyecto antes de terminarlo todo, este es el punto donde ya sostiene lo que
> promete.

---

## F5.1 · Estado del agente

**Produce:** `packages/synapseflow/agents/state.py`

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    recuperados: list[Document]  # fragmentos para el verificador
    citas: list[Cita]
    calculos: dict[str, Any]  # números determinísticos ya computados
    veredicto: str | None  # salida del verificador de F3.4
```

**Mantenerlo estrecho.** Todo lo que viaja por el estado se serializa en cada
checkpoint: cuanto más grande, más caro y más lento. Y sobre todo:

> **Nada de clientes, conexiones ni objetos vivos en el estado.** Tiene que ser
> serializable. Es la restricción que impone el checkpointer y no es negociable.

**Verificar:** `pytest tests/agents/test_state.py -v`

---

## F5.2 · Agentes especialistas

**Produce:** `packages/synapseflow/agents/especialistas.py`

Tres, cada uno con su subconjunto de herramientas:

| Agente | Herramientas | Perfil de modelo |
|---|---|---|
| `agente_normativa` | `buscar_normativa` | `synthesis` |
| `agente_datos` | `consultar_activo`, `listar_activos`, `historial_inspecciones` | `router` |
| `agente_calculo` | `calcular_vida_remanente` | `router` |

**El de cálculo casi no usa el modelo.** Su trabajo es invocar la función de
Python y presentar el resultado. Si el modelo empieza a reinterpretar los
números, el compromiso 3 se rompe: el prompt tiene que ser explícito en que
reporta, no estima.

Las herramientas de cada uno salen de `compile_tools(onto, rol)` filtradas por
las que le corresponden — **no se instancian a mano**.

**Verificar:** `pytest tests/agents/test_especialistas.py -v`

---

## F5.3 · Nodo verificador

**Produce:** `packages/synapseflow/agents/verificador.py`

```python
async def nodo_verificador(estado: AgentState) -> dict: ...
```

Envuelve el `VerificadorDeFundamento` de F3.4 como nodo del grafo. Corre
**después** de que se redacta la respuesta y **antes** de emitirla.

Según el veredicto:

- `fundamentada` → sigue al gate o al final
- `parcial` → sigue, marcando lo no respaldado
- `sin_fundamento` → **vuelve al agente de normativa** para recuperar más
  contexto, o corta y responde que no sabe

Ese retorno al agente de normativa es un **ciclo**, y es una de las dos razones
por las que el proyecto usa LangGraph y no una cadena lineal. Limitar el número
de reintentos: dos vueltas sin fundamento significan que no está en el corpus.

**Verificar:** `pytest tests/agents/test_verificador.py -v`

---

## F5.4 · Supervisor

**Produce:** `packages/synapseflow/agents/supervisor.py`

```python
async def nodo_supervisor(estado: AgentState) -> Command: ...
```

Decide a qué especialista mandar cada consulta. Devuelve un `Command` con `goto`.

**Usa el perfil `router`**, el más barato. Es la llamada más frecuente del
sistema: se ejecuta en cada turno y varias veces por consulta compleja. Usar el
modelo caro acá es el error de costos más común en agentes multi-nodo.

Para el caso de referencia —«el P-2101-A midió 6,8 mm, ¿sigue apto?»— el
supervisor tiene que orquestar: datos → cálculo → normativa → verificador.

**Verificar:** `pytest tests/agents/test_supervisor.py -v`

---

## F5.5 · Ensamblado del grafo

**Produce:** `packages/synapseflow/agents/graph.py`
**Cierra el compromiso 2.**

```python
def construir_grafo(onto: Ontology, ctx: ExecutionContext) -> CompiledStateGraph:
    return create_agent(
        model=gateway.chat("synthesis"),
        tools=compile_tools(onto, ctx.rol, context=ctx),
        checkpointer=FirestoreSaver(),
        # los gates NO se escriben a mano:
        middleware=[
            *construir_middleware(onto, ctx),
            HumanInTheLoopMiddleware(interrupt_on=interrupt_config(onto, ctx.rol)),
        ],
    )
```

> **`middleware` es parámetro de `create_agent`, no de `StateGraph.compile()`.**
> Una versión anterior de este documento lo pasaba a `compile()`, que acepta
> `checkpointer`, `cache`, `store`, `interrupt_before`, `interrupt_after`,
> `debug`, `name` y `transformers` — y nada más: escrito así, lanza `TypeError`.
> Es el Hallazgo 3 de [las convenciones](../00-convenciones.md), verificado por
> introspección contra `langchain` 1.3.14.
>
> Si el supervisor necesita control de flujo que `create_agent` no expresa, el
> camino es un `StateGraph` que tenga a los agentes creados con `create_agent`
> como nodos: el middleware vive en cada agente, no en la compilación del grafo.

**La línea que cierra el compromiso 2** es la del `HumanInTheLoopMiddleware`. Su
configuración sale de `interrupt_config(ontology, role)`, que **ya está
implementada** en `packages/synapseflow/ontology/compiler.py` y produce el mapeo
`{herramienta: InterruptOnConfig}` derivado del YAML.

Un desarrollador no puede agregar una acción irreversible y olvidarse del freno,
**porque no es él quien lo escribe**.

**Verificar:** `pytest tests/agents/test_graph.py -v`

---

## F5.6 · Test estructural de gates y recorrido completo

**Produce:** `tests/agents/test_gates_estructural.py` y
`tests/agents/test_recorrido_completo.py`

### El test estructural

```python
def test_ninguna_accion_irreversible_es_alcanzable_sin_gate():
    """Verifica una propiedad del sistema, no un comportamiento.

    Recorre el grafo COMPILADO y comprueba que toda acción con
    reversible: false esté cubierta por el HumanInTheLoopMiddleware. No
    invoca ninguna acción: inspecciona la estructura.

    Es la clase de test que justifica haber elegido un motor de grafos: con
    control de flujo disperso en ifs, esta propiedad solo se podría revisar
    leyendo el código y confiando.
    """
    onto = get_ontology()
    for rol in [r.id for r in onto.roles]:
        cfg = interrupt_config(onto, rol)
        irreversibles = {a.id for a in onto.actions_for_role(rol) if not a.reversible}
        assert irreversibles <= set(cfg), f"sin gate para el rol {rol}: {irreversibles - set(cfg)}"
```

### El recorrido completo

```python
def test_caso_p2101a_llega_al_gate_de_parada():
    """El recorrido que atraviesa toda la documentación del proyecto.

    Con FakeChatModel programado, el grafo debe: consultar el activo,
    calcular vida remanente en Python, recuperar el fundamento normativo,
    proponer solicitar_parada_equipo, y DETENERSE en el gate.
    """
```

Verificar en el resultado: que `__interrupt__` esté presente, que la acción
propuesta sea `solicitar_parada_equipo`, que el `tag` sea `P-2101-A`, y que el
número de vida remanente venga de `calculos` y no del texto del modelo.

### Extender el test de supervivencia

El test `test_hitl_sobrevive_a_la_muerte_del_proceso` de
`tests/persistence/test_checkpointer.py` usa hoy un grafo de prueba de tres
nodos. **Portarlo al grafo real**: interrumpir en el gate, destruir grafo y
saver, reconstruir y reanudar con `Command(resume=...)`, verificando que los
argumentos ejecutados sean idénticos a los propuestos.

**Verificar:** `pytest tests/agents -v` y `pytest tests/persistence -v`

---

## Al cerrar F5

- El compromiso 2 pasa a ✅. **Los cinco compromisos quedan cumplidos.**
- Actualizar la tabla de estado de ambos READMEs: grafo de agentes, RAG y
  gobernanza pasan a ✅.
- Anotar en el `CHANGELOG.md` como el hito que es.
- Actualizar los módulos § 13, § 15 y § 20 del manual de estudio, que hoy están
  marcados «todavía no construido» y pasan a implementados.
