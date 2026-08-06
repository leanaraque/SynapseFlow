---
doc_id: PROC-INT-014
titulo: Medición de espesores por ultrasonido en equipos en servicio
tipo_documento: procedimiento_interno
vigencia: vigente
---

## 1.1 · Objeto

Este procedimiento establece cómo se ejecuta, se registra y se evalúa una
campaña de medición de espesores por ultrasonido sobre equipos estáticos en
servicio, y qué acción corresponde ante cada resultado.

Aplica a cañerías de proceso, recipientes a presión, separadores,
intercambiadores de calor y tanques de almacenamiento de todas las
instalaciones. No aplica a equipos rotativos, cuyo seguimiento se realiza por
análisis de vibraciones conforme a PROC-INT-021.

## 1.3 · Documentos de referencia

- API 570, para cañerías de proceso.
- API 510, para recipientes a presión.
- API 653, para tanques de almacenamiento.
- API 579, para evaluación de aptitud para el servicio.

Ante discrepancia entre este procedimiento y el código internacional aplicable,
**prevalece el código**. Este procedimiento detalla la ejecución; no relaja
criterios de aceptación.

## 2.3 · Calificación del personal y del equipo

La medición la ejecuta personal certificado en ensayo ultrasónico de nivel II o
superior, vigente a la fecha de la campaña.

El instrumento se calibra al inicio de cada jornada contra un bloque patrón de
espesor conocido y del mismo material que el componente a medir. La calibración
se repite ante todo cambio de palpador, de material o de temperatura de
superficie superior a veinte grados respecto de la calibración vigente.

Una campaña ejecutada con instrumento sin calibración vigente se considera no
válida y se repite.

## 3.1 · Ubicaciones de medición

Cada componente tiene ubicaciones de medición permanentes, identificadas de
manera unívoca y marcadas físicamente sobre el equipo. Las ubicaciones se
seleccionan sobre los puntos de mayor deterioro esperado: codos por el lado
externo de la curva, reducciones, aguas abajo de válvulas de control, zonas de
acumulación de agua libre y puntos bajos del sistema.

Toda campaña mide las mismas ubicaciones que la anterior. Agregar ubicaciones es
admisible; sustituirlas no, porque interrumpe la serie histórica y con ella la
posibilidad de calcular la velocidad de corrosión del punto.

## 3.2 · Criterio de aceptación

Para cada componente se registra el **menor** espesor obtenido en la campaña. Ese
valor, y ningún otro —ni el promedio de las ubicaciones, ni el valor típico— es
el que se compara contra el espesor mínimo requerido del componente.

El criterio de aceptación es directo y no admite tolerancia:

| Espesor medido respecto de `t_min` | Severidad | Acción |
|---|---|---|
| Inferior a `t_min` | crítico | Escalamiento inmediato y retiro de servicio o evaluación de aptitud |
| Entre `t_min` y `t_min` + 5 % | desvío mayor | Escalamiento en la jornada y reducción del intervalo de inspección |
| Entre `t_min` + 5 % y `t_min` + 15 % | desvío menor | Registro y revisión del intervalo de inspección |
| Superior a `t_min` + 15 % | observación o sin desvío | Registro y continuidad del plan vigente |

**Un componente con espesor por debajo de `t_min` no continúa en servicio**, con
independencia de la magnitud de la diferencia y de la incertidumbre declarada del
instrumento. La incertidumbre de medición no constituye margen de diseño: el
margen de diseño ya está contenido en el cálculo de `t_min`.

Este criterio reemplaza expresamente la tolerancia del cinco por ciento que
admitía la versión anterior de este procedimiento. Ver la sección 5.1.

## 3.4 · Mediciones anómalas

Una medición que resulte mayor que la de la campaña anterior sobre la misma
ubicación se repite antes de registrarse. Un componente en servicio no recupera
espesor: un valor creciente indica error de calibración, cambio de ubicación,
presencia de depósito interno computado como material, o acoplamiento
defectuoso del palpador.

Si la repetición confirma el valor, se documenta la causa antes de incorporarlo a
la serie. Un valor creciente aceptado sin justificación produce una velocidad de
corrosión negativa y una vida remanente sin sentido físico.

## 4.2 · Escalamiento

Un espesor por debajo del mínimo requerido se comunica al supervisor de
mantenimiento de la instalación dentro de la jornada en que se obtiene, sin
esperar el cierre del informe de campaña.

La comunicación incluye el TAG del equipo, el espesor medido, el espesor mínimo
requerido, la velocidad de corrosión calculada y la identificación de la
inspección que la sustenta. Una solicitud de intervención sin la inspección que
la respalda no es trazable y no se procesa.

## 5.1 · Reemplazo de la versión anterior

Este procedimiento reemplaza y deroga a **PROC-INT-009**, cuya versión admitía
continuar la operación con espesores de hasta un cinco por ciento por debajo del
mínimo requerido, apelando a la incertidumbre del instrumento.

Ese criterio quedó sin efecto. **PROC-INT-009 no debe utilizarse como fundamento
de ninguna decisión de continuidad operativa.** Se conserva en el repositorio
documental únicamente para reconstruir decisiones tomadas antes de su
derogación.
