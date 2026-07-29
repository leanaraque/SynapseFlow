# ADR-0004 · Gateway de modelos con frontera de datos explícita

- **Estado:** aceptada
- **Fecha:** 2026-07-29
- **Decide:** Leandro Araque

## Contexto

Dos presiones distintas empujan hacia la misma pieza.

La primera es **de cumplimiento**. En una empresa regulada, qué proveedor recibe
qué dato no es un detalle de implementación: es una decisión con dueño. La
plataforma tiene que poder afirmar que ningún campo clasificado como `restricted`
salió hacia un modelo externo, y demostrarlo con un log.

La segunda es **de costo**. Un supervisor que rutea entre cuatro agentes hace una
clasificación corta y estructurada. Sintetizar una respuesta fundamentada sobre
seis fragmentos de normativa es otra tarea. Usar el mismo modelo para ambas
paga el modelo caro en la tarea barata, que además es la más frecuente.

Y hay una tercera, menos técnica: el proveedor por defecto del demo es Gemini,
pero el diseño no debe presuponerlo.

## Decisión

Todas las llamadas a modelos pasan por `synapseflow.llm.gateway`. Ningún agente
instancia un `ChatModel` directamente.

**Adapters.** Gemini, OpenAI, Azure OpenAI y Anthropic detrás de la interfaz
`BaseChatModel` de LangChain. El gateway devuelve un `BaseChatModel`, así que
para el resto del código el proveedor es invisible. Se selecciona por
`SYNAPSEFLOW_PROVIDER`.

**Perfiles de tarea, no nombres de modelo.** El código pide un perfil y el
gateway resuelve a un modelo concreto según el proveedor activo:

| Perfil | Uso | Criterio |
|---|---|---|
| `router` | clasificación y ruteo del supervisor | el más barato que sostenga tool calling confiable |
| `synthesis` | redacción fundamentada con citas | el más capaz |
| `verifier` | chequeo de groundedness | capacidad media, salida estructurada |
| `embedding` | indexación y recuperación | modelo de embeddings del proveedor |

Un agente pide `gateway.chat("synthesis")`. Nunca `gemini-2.5-pro`. Cambiar la
política de costos es cambiar el mapa de perfiles, en un archivo.

**Frontera de datos.** El gateway es el único punto donde el texto cruza hacia
afuera, así que es donde se aplica la política:

1. Se redactan los campos marcados `pii` o `restricted` en la ontología,
   reemplazándolos por tokens estables (`«INSPECTOR_1»`).
2. Se verifica que el proveedor activo esté declarado como zero-training. Si
   `SYNAPSEFLOW_ENFORCE_ZERO_TRAINING` está activo y el proveedor no lo
   garantiza, la llamada falla en lugar de degradar silenciosamente.
3. Se rehidratan los tokens en la respuesta, hacia adentro del perímetro.

Poner esto en el gateway y no en cada agente es lo que hace la garantía
verificable: hay un solo camino de salida, y un test puede afirmar que no existe
otro.

**Contabilidad.** Un `BaseCallbackHandler` propio registra tokens de entrada y
salida, modelo, perfil, latencia y costo derivado del catálogo de precios, y lo
escribe en Firestore correlacionado por `thread_id` y `run_id`. El costo por
consulta es una métrica de la plataforma, no una estimación de la factura.

**Resiliencia.** `with_fallbacks()` de LangChain para degradar a un proveedor
alternativo, y caché de LLM para las evals, donde el mismo prompt se ejecuta
muchas veces.

## Consecuencias

**A favor**

- Un único punto donde se aplica la política de datos, y por lo tanto un único
  punto que auditar.
- Cambiar de proveedor es cambiar una variable de entorno. Cubre el escenario
  real de una empresa que estandariza en Azure OpenAI.
- El ruteo por costo es una decisión declarada y medible, no una elección
  dispersa en el código.
- La contabilidad de costos es exacta por llamada.

**En contra**

- Una indirección más entre el agente y el modelo. Cuesta cuando se quiere usar
  una capacidad específica de un proveedor que la interfaz común no expone: el
  gateway permite pedir el cliente crudo, pero ese camino queda explícitamente
  marcado y fuera de la garantía de redacción.
- El catálogo de precios se desactualiza. Vive en un archivo aparte con la fecha
  de última verificación, y un test avisa cuando pasa de noventa días.
- La redacción por tokens degrada la calidad cuando el nombre de la persona era
  relevante para la respuesta. En este dominio no lo es: al agente le importa el
  hallazgo, no quién lo firmó. Queda documentado como límite del diseño.

## Verificación

- `tests/llm/test_data_boundary.py` verifica que un payload con un campo
  `restricted` no llegue nunca al adapter con el valor original.
- Test estructural: ningún módulo fuera de `synapseflow.llm` importa un
  `ChatModel` de un proveedor. Se verifica recorriendo los imports del paquete.
- `tests/llm/test_pricing_freshness.py` falla si el catálogo de precios tiene más
  de noventa días sin verificar.
