---
doc_id: PROC-INT-009
titulo: Medición de espesores por ultrasonido (versión derogada)
tipo_documento: procedimiento_interno
vigencia: derogado
---

> **DOCUMENTO DEROGADO.** Reemplazado por PROC-INT-014. No puede utilizarse como
> fundamento de ninguna decisión de continuidad operativa. Se conserva
> únicamente para reconstruir decisiones tomadas antes de su derogación.

## 1.1 · Objeto

Este procedimiento establece cómo se ejecuta y se evalúa una campaña de medición
de espesores por ultrasonido sobre equipos estáticos en servicio.

## 3.1 · Ubicaciones de medición

Las ubicaciones de medición se seleccionan en los puntos de mayor deterioro
esperado. Se admite el ajuste de ubicaciones entre campañas cuando el inspector
lo considere justificado por el estado de la superficie.

## 3.2 · Criterio de aceptación

Para cada componente se registra el espesor representativo de la campaña,
entendido como el promedio de las ubicaciones medidas sobre el mismo componente.

Se admite continuar la operación con un espesor de hasta un **cinco por ciento
por debajo** del espesor mínimo requerido, en atención a la incertidumbre propia
del instrumento de medición, siempre que la tendencia histórica del componente se
mantenga estable y el supervisor de mantenimiento lo autorice por escrito.

| Espesor medido respecto de `t_min` | Acción |
|---|---|
| Inferior a `t_min` − 5 % | Retiro de servicio |
| Entre `t_min` − 5 % y `t_min` | Continuidad con autorización escrita |
| Superior a `t_min` | Continuidad del plan vigente |

## 3.3 · Frecuencia

El intervalo entre campañas no excede los sesenta meses para la totalidad de los
equipos estáticos, con independencia de su criticidad.

## 4.1 · Registro

El resultado de la campaña se incorpora al informe mensual de integridad. El
escalamiento de un hallazgo se realiza en la reunión de integridad
correspondiente al período.
