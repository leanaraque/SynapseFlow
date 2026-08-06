---
doc_id: PROC-SEG-003
titulo: Permisos de trabajo en áreas clasificadas
tipo_documento: procedimiento_interno
vigencia: vigente
---

## 1.1 · Objeto

Este procedimiento establece qué permisos de trabajo exige cada intervención
según el área clasificada de la instalación y la naturaleza del equipo
intervenido, y quién los autoriza.

Ninguna orden de trabajo se ejecuta sin los permisos que le correspondan
emitidos y vigentes. Una orden emitida a ejecución sin sus permisos es una orden
que no puede iniciarse en campo.

## 2.1 · Clasificación de áreas

La clasificación de áreas por riesgo de atmósfera explosiva sigue el criterio de
IEC 60079-10-1:

| Zona | Definición |
|---|---|
| `zona_0` | Atmósfera explosiva presente de forma continua o durante períodos prolongados |
| `zona_1` | Atmósfera explosiva probable en operación normal |
| `zona_2` | Atmósfera explosiva improbable en operación normal y, de producirse, de corta duración |
| `no_clasificada` | Sin riesgo de atmósfera explosiva |

La clasificación es un atributo de la instalación y condiciona qué permisos
aplican a cualquier trabajo ejecutado dentro de su perímetro, con independencia
del equipo intervenido.

## 3.1 · Permiso de trabajo en caliente

Exigido para toda actividad que genere llama abierta, chispa o temperatura
superficial capaz de encender una atmósfera inflamable: soldadura, corte,
amolado, y uso de herramienta eléctrica no certificada para el área.

**El trabajo en caliente está prohibido en `zona_0`.** No existe permiso que lo
habilite: la única vía es desclasificar temporalmente el área mediante purga e
inertización verificada por medición continua de atmósfera, lo que la convierte
en área no clasificada mientras dure la condición.

En `zona_1` requiere medición continua de atmósfera durante toda la ejecución y
vigía dedicado. En `zona_2` requiere medición previa al inicio y repetición ante
toda interrupción del trabajo.

## 3.2 · Permiso de espacio confinado

Exigido para el ingreso a todo recinto de acceso restringido y ventilación
natural insuficiente: interior de tanques de almacenamiento, recipientes a
presión, separadores y cámaras subterráneas.

Requiere, sin excepción:

- Aislamiento efectivo del equipo respecto del proceso, mediante doble bloqueo
  con purga intermedia o desconexión física. El cierre de una válvula no
  constituye aislamiento.
- Medición de oxígeno, gases inflamables y gases tóxicos previa al ingreso, y
  repetición periódica durante la permanencia.
- Vigía exterior en contacto permanente con el personal que ingresa.
- Plan de rescate disponible antes del ingreso, no convocable después.

## 3.3 · Bloqueo y etiquetado

Exigido para toda intervención sobre un equipo que pueda liberar energía
almacenada: eléctrica, mecánica, hidráulica, neumática, térmica o de presión
residual.

El bloqueo lo aplica quien ejecuta el trabajo y solo él lo retira. Un bloqueo
retirado por persona distinta de quien lo colocó invalida el permiso y detiene la
intervención.

Toda orden de trabajo correctiva sobre un equipo en servicio requiere este
permiso.

## 3.4 · Permiso de trabajo en altura

Exigido para toda tarea ejecutada a más de dos metros sobre el nivel de
referencia, incluyendo el acceso a techos de tanques de almacenamiento y a
plataformas de inspección.

Requiere punto de anclaje verificado, protección contra caídas y delimitación del
área inferior.

## 3.5 · Permiso de apertura de línea de proceso

Exigido para toda intervención que rompa la contención de un sistema que
contuvo hidrocarburo, agua de producción o vapor: desarme de bridas, corte de
cañería, desmontaje de válvulas e ingreso a intercambiadores de calor.

Requiere despresurización verificada por instrumento —no por la posición de un
manómetro de proceso—, drenaje, purga y confirmación de ausencia de presión
residual en el punto exacto de apertura.

## 4.2 · Concurrencia de permisos

Una misma intervención puede exigir varios permisos de forma simultánea. La
combinación más frecuente en instalaciones de producción es la reparación de una
cañería de proceso en servicio, que requiere bloqueo y etiquetado más apertura de
línea de proceso, y suma trabajo en caliente si la reparación es por soldadura.

El ingreso a un tanque de almacenamiento suma espacio confinado y trabajo en
altura al bloqueo y etiquetado, por el acceso al techo.

Cuando concurran varios permisos, la autorización es única y contempla la
interacción entre ellos. Autorizar cada permiso por separado deja sin evaluar los
riesgos que surgen de su simultaneidad, que son los que producen los eventos de
mayor consecuencia.

## 5.2 · Autorización

Los permisos de trabajo en caliente, espacio confinado y apertura de línea de
proceso los autoriza el supervisor de mantenimiento de la instalación. Los
permisos de bloqueo y etiquetado y de trabajo en altura los autoriza el
responsable del frente de trabajo.

Ningún permiso se autoriza a sí mismo: quien ejecuta la tarea no puede ser quien
autoriza su permiso.
