# ADR-0005 · Los gates de aprobación humana usan `interrupt()` de LangGraph, no un flujo aparte

- **Estado:** aceptada
- **Fecha:** 2026-07-29
- **Decide:** Leandro Araque

## Contexto

Tres acciones del dominio son irreversibles desde la plataforma:
`emitir_orden_trabajo`, `solicitar_parada_equipo` y `reclasificar_criticidad`.
Emitir una orden moviliza una cuadrilla. Pedir la parada de un equipo impacta
producción. Ninguna puede quedar a criterio del modelo.

El requisito es que un humano con el rol adecuado apruebe explícitamente antes
de que la acción se materialice, y que quede registro de quién aprobó qué.

La dificultad es de arquitectura, no de producto: la aprobación es asincrónica.
Entre que el agente propone la parada y que el supervisor la aprueba pueden
pasar horas. La instancia de Cloud Run que corría el grafo ya no existe.

## Alternativas consideradas

**A. Terminar el turno y arrancar otro.** El agente responde "propongo esta
acción, confirmá", se cierra la ejecución, y la aprobación llega como un mensaje
nuevo que el modelo interpreta. Es lo más simple y es lo que hace la mayoría de
los chatbots.

Descartada. El agente reconstruye su intención a partir de texto, y el texto es
reinterpretable: nada garantiza que la acción ejecutada después del "sí" sea la
misma que se describió antes. Para una parada de equipo, "casi la misma acción"
no alcanza. Tampoco hay un lugar natural donde registrar el vínculo entre la
propuesta y la aprobación.

**B. Máquina de estados propia por fuera del grafo.** Una colección de
aprobaciones pendientes en Firestore, con el payload de la acción serializado, y
un endpoint que al aprobar ejecuta la herramienta directamente.

Descartada. Funciona, pero parte el sistema en dos motores de ejecución: el
grafo de LangGraph y esta máquina paralela. El estado de la conversación queda en
uno y la acción se ejecuta en el otro, así que la acción se materializa sin que
el agente sepa que ocurrió, y el razonamiento posterior pierde el hilo. Además
duplica lo que el checkpointer ya hace.

**C. `interrupt()` de LangGraph con checkpointer durable.**

## Decisión

Se toma la alternativa **C**.

El compilador de herramientas de la ontología (ver
[ADR-0003](0003-ontologia-declarativa-en-yaml.md)) envuelve toda acción con
`requires_approval: true` en un nodo que llama a `interrupt()` antes de ejecutar
el efecto. El grafo se detiene ahí y su estado completo —mensajes, argumentos
resueltos de la acción, plan pendiente— se persiste vía `FirestoreSaver`
(ver [ADR-0002](0002-integraciones-firestore-propias.md)).

```
propuesta ──► interrupt() ──► [estado persistido en Firestore]
                                          │
                          el proceso puede morir acá
                                          │
aprobación del supervisor ──► Command(resume=...) ──► ejecución ──► continúa
```

La reanudación entra por `Command(resume=...)` sobre el mismo `thread_id`. El
grafo retoma exactamente donde estaba, con los argumentos que ya había resuelto:
lo que se aprueba es lo que se ejecuta, no una reinterpretación.

El endpoint de aprobación valida contra la ontología que el usuario que aprueba
tenga uno de los `approver_roles` de esa acción, y que no sea el mismo que la
propuso. Ambos hechos van al log de auditoría junto al `thread_id` y al
`checkpoint_id`.

## Consecuencias

**A favor**

- Un único motor de ejecución y un único lugar donde vive el estado.
- Integridad de la propuesta: la acción ejecutada es literalmente la que se
  mostró, porque los argumentos nunca se re-derivan.
- La traza de auditoría es completa por construcción: `thread_id` +
  `checkpoint_id` reconstruyen el razonamiento que llevó a la propuesta, no solo
  el hecho de la aprobación.
- Sobrevive al reinicio del proceso, que es el requisito que descartó la
  alternativa A.
- El mismo mecanismo sirve para cualquier otra pausa futura sin diseño nuevo.

**En contra**

- Acopla la plataforma al modelo de ejecución de LangGraph. Es un acoplamiento
  aceptado: LangGraph es el motor de orquestación elegido en
  [ADR-0001](0001-langgraph-como-motor-de-orquestacion.md), y `interrupt()` es
  precisamente la razón por la que se eligió.
- Exige que todo lo que atraviesa el estado del grafo sea serializable. Restringe
  qué se puede poner en el estado: nada de clientes ni conexiones abiertas.
- Un `thread_id` interrumpido queda ocupando estado en Firestore hasta que se
  resuelve o expira. Se mitiga con TTL sobre las aprobaciones pendientes y una
  vista de vencidas en la consola.

## Verificación

- `tests/agents/test_hitl.py` ejercita el escenario completo: interrumpir en el
  gate, descartar el objeto del grafo en memoria, reconstruirlo desde el
  checkpointer y reanudar con `Command(resume=...)`, verificando que los
  argumentos ejecutados sean idénticos a los propuestos.
- Test negativo: un usuario sin `approver_roles` no puede reanudar.
- Test negativo: el proponente no puede aprobar su propia acción.
- Test negativo: ninguna acción con `reversible: false` puede ejecutarse sin
  pasar por un `interrupt()`. Se verifica recorriendo el grafo compilado, no
  invocando cada acción.
