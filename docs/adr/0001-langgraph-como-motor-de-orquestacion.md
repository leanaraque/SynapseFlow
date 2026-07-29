# ADR-0001 · LangGraph como motor de orquestación de agentes

- **Estado:** aceptada
- **Fecha:** 2026-07-29
- **Decide:** Leandro Araque

## Contexto

La plataforma necesita orquestar varios agentes especializados —normativa,
datos, cálculo, verificación— con tres requisitos que no son negociables:

1. **Pausar la ejecución** a mitad de camino para esperar la aprobación de un
   humano, y sobrevivir al reinicio del proceso mientras espera.
2. **Ciclos**: el verificador puede rechazar una respuesta por falta de
   fundamento y devolverla al agente de normativa para que recupere más contexto.
3. **Streaming de pasos intermedios** hacia la consola, para que el usuario vea
   qué herramienta se está ejecutando y no una barra de progreso opaca.

## Alternativas consideradas

**A. Orquestación propia con LCEL.** Componer los agentes con Runnables de
LangChain y manejar el control de flujo en Python. Descartada: LCEL compone bien
cadenas acíclicas, pero los ciclos y la pausa durable hay que construirlos a
mano, y eso es exactamente reescribir un motor de grafos con checkpointing.

**B. Un framework multi-agente conversacional** del estilo de los que orquestan
por intercambio de mensajes entre roles. Descartada: el control de flujo emerge
de la conversación en lugar de estar declarado. Para un dominio donde hay que
poder afirmar "ninguna acción irreversible se ejecuta sin pasar por este nodo",
el flujo tiene que ser inspeccionable, no emergente.

**C. LangGraph.**

## Decisión

Se toma la alternativa **C**.

Las razones concretas, en orden de peso:

- **`interrupt()` con checkpointer durable** resuelve el requisito 1 de raíz. Es
  el motivo principal de la elección y está desarrollado en
  [ADR-0005](0005-hitl-con-interrupt-de-langgraph.md).
- **El grafo es un objeto inspeccionable.** Se puede recorrer el grafo compilado
  y verificar propiedades estructurales en un test: que toda acción irreversible
  esté precedida por un gate, sin necesidad de invocarla.
- **`astream_events`** entrega los pasos intermedios con granularidad de nodo y
  de token, que es lo que la consola necesita.
- **Los subgrafos** permiten que cada agente especializado sea un grafo con su
  propio estado, componible sin que el supervisor conozca su interior.
- Continuidad con el resto del ecosistema: mismos modelos, mismas herramientas y
  el tracing de LangSmith sin trabajo adicional.

Versión: `langgraph` 1.2.10 sobre `langchain-core` 1.5.2.

## Consecuencias

**A favor**

- Los tres requisitos quedan cubiertos por el motor, no por código propio.
- Propiedades de seguridad verificables estructuralmente.

**En contra**

- Acoplamiento al modelo de ejecución de LangGraph, asumido explícitamente.
- Todo lo que atraviesa el estado del grafo debe ser serializable.
- La curva de aprendizaje del modelo de estado y reducers es real, y los errores
  tempranos tienden a ser de estado compartido mal reducido. Se mitiga con un
  `AgentState` tipado y estrecho, definido en un solo lugar.
