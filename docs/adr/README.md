# Decisiones de arquitectura

Un ADR (*Architecture Decision Record*) registra una decisión estructural: qué
problema la forzó, qué alternativas se evaluaron, por qué se descartaron y qué
consecuencias trae —incluidas las que juegan en contra.

El valor de escribirlos aparece meses después, cuando alguien —incluido el autor
original— se pregunta por qué el sistema está hecho así y la respuesta no es
"porque sí" ni una arqueología del historial de git.

## Índice

| ADR | Decisión | Estado |
|---|---|---|
| [0001](0001-langgraph-como-motor-de-orquestacion.md) | LangGraph como motor de orquestación de agentes | aceptada |
| [0002](0002-integraciones-firestore-propias.md) | Implementar las integraciones de Firestore contra las interfaces de LangChain, en lugar de usar el paquete oficial | aceptada |
| [0003](0003-ontologia-declarativa-en-yaml.md) | La ontología del dominio es declarativa y vive fuera del código | aceptada |
| [0004](0004-gateway-provider-agnostic.md) | Gateway de modelos con frontera de datos explícita | aceptada |
| [0005](0005-hitl-con-interrupt-de-langgraph.md) | Los gates de aprobación humana usan `interrupt()` de LangGraph, no un flujo aparte | aceptada |

## Escribir uno nuevo

Numeración correlativa, nombre en `NNNN-titulo-en-kebab-case.md`, y esta
estructura:

```markdown
# ADR-NNNN · Título en una línea, en presente

- **Estado:** propuesta | aceptada | reemplazada por ADR-XXXX
- **Fecha:** AAAA-MM-DD
- **Decide:** nombre

## Contexto
Qué problema real fuerza la decisión. Sin esto, el resto no se entiende.

## Alternativas consideradas
Cada una con por qué se descartó. Un ADR sin alternativas descartadas no
documenta una decisión: documenta un hecho consumado.

## Decisión
Qué se hace, concretamente.

## Consecuencias
**A favor** y **en contra**. La sección en contra es obligatoria: un ADR que
solo lista ventajas no sirve para revisarlo dentro de seis meses.

## Verificación
Qué test o qué evidencia sostiene que la decisión funciona.
```

Un ADR no se edita para cambiar de opinión: se escribe uno nuevo que lo
reemplaza, y el viejo pasa a estado *reemplazada*. El historial de decisiones es
parte de la documentación.
