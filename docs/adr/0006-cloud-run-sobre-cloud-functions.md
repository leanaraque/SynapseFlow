# ADR-0006 · La API se despliega en Cloud Run, no en Cloud Functions

- **Estado:** aceptada
- **Fecha:** 2026-08-08
- **Decide:** Leandro Araque

## Contexto

La API expone el grafo de agentes por HTTP: identidad de Firebase, streaming por
SSE y los endpoints de aprobación. Tiene que correr en algún lado dentro del
proyecto de Firebase, detrás del rewrite de `/api/**` que ya está declarado en
`firebase.json`.

Tres características del servicio condicionan la elección, y ninguna es sobre
volumen de tráfico:

**El árbol de dependencias es grande.** LangChain, LangGraph, `langchain-classic`,
los SDK de tres proveedores, Firestore, FastAPI. Instalado son cientos de megas.
El tiempo de arranque en frío depende de eso más que de cualquier otra cosa.

**Las respuestas son largas y en streaming.** Un recorrido completo —consulta,
cálculo, normativa, verificación, propuesta— puede tardar decenas de segundos, y
el usuario tiene que ver eventos durante todo ese tiempo. El transporte no puede
ser un request corto que devuelve un JSON.

**El estado no vive en el proceso.** Un gate abierto espera horas a que un
supervisor lo apruebe, y la instancia que corría el grafo ya no existe cuando
llega la aprobación (ver [ADR-0005](0005-hitl-con-interrupt-de-langgraph.md)).
El servicio es, para este efecto, sin estado: el estado está en Firestore.

## Alternativas consideradas

**A. Cloud Functions (2ª generación).** Es lo natural en un proyecto de Firebase:
se despliega con la misma CLI, el rewrite de Hosting lo soporta igual y no hay
que pensar en imágenes.

Descartada por control sobre el arranque. Cloud Functions construye la imagen por
buildpacks: no se elige la base, no se controla el orden de las capas y no se
puede separar la instalación de dependencias del copiado del código. Con un árbol
de este tamaño, esa es exactamente la palanca que hace falta, y quedarse sin ella
significa aceptar el arranque en frío que salga.

Se suma que 2ª generación corre **encima de Cloud Run**: la abstracción no ahorra
la infraestructura, solo el acceso a ella. Se paga la misma complejidad sin poder
ajustarla.

**B. App Engine estándar.** Descartada rápido: los runtimes son fijos, el control
de la imagen es todavía menor que en Cloud Functions y el modelo de escalado no
aporta nada acá.

**C. GKE Autopilot.** Da control total, y bastante más del necesario. Un servicio
HTTP sin estado que escala a cero no justifica un clúster: agrega un plano de
control, su configuración y su factura mínima para resolver un problema que no
tenemos.

**D. Cloud Run.**

## Decisión

Se toma la alternativa **D**. El servicio se llama `synapseflow-api` y corre en
`southamerica-east1`, como ya declara el rewrite de `firebase.json`.

La imagen es multi-etapa sobre `python:3.11-slim` (`services/api/Dockerfile`):

- Las **dependencias se instalan antes de copiar el código**, en su propia capa.
  Es la decisión que más impacta el ciclo de trabajo: un cambio de una línea
  reconstruye segundos en lugar de minutos.
- La etapa de construcción no llega a la imagen final. Lo que se copia es el
  virtualenv ya resuelto, así que el compilador y las cabeceras quedan afuera:
  menos superficie que parchear.
- **No hay credenciales en la imagen.** En Cloud Run la autenticación con
  Firestore sale de la identidad del servicio (ADC). Las claves de proveedores se
  inyectan por **Secret Manager**, nunca con `--set-env-vars`, que las dejaría
  visibles en el manifiesto del servicio.
- El proceso corre como usuario sin privilegios y escucha en el `PORT` que Cloud
  Run inyecta, que no siempre es 8080.
- **Un worker de uvicorn por contenedor.** Cloud Run escala por instancias; dos
  workers compitiendo por la misma CPU asignada empeoran la latencia en lugar de
  mejorarla. La concurrencia la da el event loop, que es lo correcto para un
  servicio que pasa el tiempo esperando I/O de un LLM.

## Consecuencias

**A favor**

- Control total sobre la imagen: base, capas, orden y tamaño. Es lo que permite
  atacar el arranque en frío, que con este árbol de dependencias es el problema
  de rendimiento dominante.
- SSE funciona sin trucos: Cloud Run soporta respuestas de larga duración y
  streaming HTTP/1.1 sin buffering intermedio.
- El mismo `Dockerfile` corre igual en la máquina de quien desarrolla, en CI y en
  producción. Un buildpack solo existe en el servicio que lo implementa.
- Escala a cero, así que un proyecto de demostración no cuesta nada en reposo.
- La ruta de despliegue ya está declarada en `firebase.json` desde F0: no hay que
  cambiar el frontend cuando el backend pase a existir.

**En contra**

- **Hay que mantener un `Dockerfile`.** Es trabajo real: actualizar la base
  cuando salgan parches, revisar que las capas sigan teniendo sentido. Un
  buildpack se actualiza solo. Se acepta a cambio del control.
- La imagen tiene que construirse en algún lado. Sin Docker local hace falta
  Cloud Build, que es un servicio más en el circuito.
- El arranque en frío sigue existiendo: se lo reduce, no se lo elimina. Con este
  árbol, la primera consulta después de un período de inactividad va a ser
  perceptiblemente más lenta. Se mitiga con `--min-instances=1` cuando el costo
  lo justifique, que es una decisión de operación y no de arquitectura.
- Requiere **plan Blaze** en el proyecto de Firebase. Es un bloqueo externo que
  el plan del proyecto declara desde el inicio.

## Verificación

- `tests/api/test_imagen.py` verifica las propiedades del `Dockerfile` que se
  rompen en silencio: que la base sea la declarada, que las dependencias se
  instalen **antes** de copiar el código, que no se copien credenciales, que el
  puerto salga de la variable de entorno y que el proceso no corra como root. Un
  `Dockerfile` que se degrada no falla el build: solo tarda más o expone más.
- `.dockerignore` excluye `.env` y las claves. Hay un test que lo comprueba,
  porque el modo de falla —una clave dentro de una capa de la imagen— no se ve
  mirando la imagen que arranca.
- `docker build -f services/api/Dockerfile -t synapseflow-api .` desde la raíz
  del repositorio, y una prueba de arranque en frío con el árbol completo, siguen
  pendientes de una máquina con Docker o de una corrida de Cloud Build. Está
  declarado como tal en el estado del proyecto: nada de este ADR afirma que la
  imagen ya se construyó.
