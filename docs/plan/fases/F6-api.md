# F6 · API en Cloud Run

**Depende de:** F5 (hace falta un grafo que ejecutar).
**Bloqueo externo:** plan Blaze en Firebase para desplegar. El código y los tests
no lo necesitan.

## Por qué Cloud Run y no Cloud Functions

El árbol de dependencias del proyecto es grande —LangChain, LangGraph, los SDK de
proveedores, Firestore— y Cloud Run da control total sobre la imagen: versión de
Python, capas de caché, tiempo de arranque en frío. Cloud Functions abstrae eso y
lo hace más difícil de ajustar.

**Este razonamiento hay que escribirlo como ADR-0006 en el commit F6.4**, con las
alternativas que se descartaron.

---

## F6.1 · FastAPI con identidad de Firebase

**Produce:** `services/api/main.py` y `services/api/auth.py`

```python
async def resolver_usuario(token: str) -> ExecutionContext:
    """Del token de Firebase Auth al contexto de ejecución.

    El rol sale de los custom claims y se valida contra la ontología: un rol
    que no existe en el dominio es un rechazo, no un valor por defecto.
    """
```

> **La regla que no se puede relajar:** el agente hereda los permisos **del
> usuario**, nunca los de la cuenta de servicio. La cuenta de servicio pasa por
> encima de las reglas de Firestore por diseño, y por eso la API tiene que
> aplicar los permisos ella misma.

Un usuario sin rol válido no obtiene un rol por defecto: obtiene un 403.

**Verificar:** `pytest tests/api/test_auth.py -v`

---

## F6.2 · Streaming por SSE

**Produce:** `services/api/streaming.py`

Usar `astream_events` del grafo compilado y traducirlo a Server-Sent Events.

**Qué emitir, y por qué importa el orden:**

| Evento | Cuándo |
|---|---|
| `herramienta_inicio` | El agente invoca una acción |
| `herramienta_fin` | Con el `content` del `ToolResult`, no el `artifact` |
| `token` | Texto del modelo, token a token |
| `citas` | Al cerrar la respuesta |
| `aprobacion_requerida` | El grafo se detuvo en un gate |

El usuario tiene que ver **qué está haciendo** el agente antes de ver la
respuesta. Un spinner opaco durante veinte segundos es peor experiencia que ver
«consultando el activo P-2101-A».

**El `artifact` no viaja al modelo pero sí a la consola**: es lo que permite
mostrar las citas y el detalle del cálculo.

**Verificar:** `pytest tests/api/test_streaming.py -v`

---

## F6.3 · Endpoints de aprobación

**Produce:** `services/api/aprobaciones.py`

```
GET  /api/aprobaciones            pendientes que este usuario puede aprobar
POST /api/aprobaciones/{thread_id}   {decision: approve|reject|edit, ...}
```

La aprobación reanuda el grafo con `Command(resume=...)` sobre el mismo
`thread_id`. Eso es lo que garantiza que **lo aprobado es lo ejecutado**: los
argumentos no se re-derivan.

**Dos validaciones obligatorias:**

1. El usuario tiene alguno de los `approver_roles` de esa acción, según la
   ontología.
2. **El aprobador no es el proponente.** Separación de funciones.

Ambas van al log de auditoría junto con el `thread_id` y el `checkpoint_id`.

**Verificar:** `pytest tests/api/test_aprobaciones.py -v`

Incluir los dos tests negativos: rol sin permiso, y proponente intentando
aprobarse a sí mismo.

---

## F6.4 · Dockerfile y despliegue

**Produce:** `services/api/Dockerfile` y `docs/adr/0006-cloud-run-sobre-cloud-functions.md`

- Imagen base `python:3.11-slim`.
- Instalar dependencias en una capa separada del código, para que un cambio de
  código no invalide la caché de dependencias.
- Sin archivo de credenciales: en Cloud Run la autenticación sale de la identidad
  del servicio (ADC).
- Las claves de proveedores se inyectan por **Secret Manager**, nunca como
  variable de entorno en el manifiesto.

El rewrite de `/api/**` a Cloud Run **ya está** en `firebase.json`, con
`serviceId: synapseflow-api` y región `southamerica-east1`. Si cambiás el nombre
del servicio, actualizá ese archivo.

**Verificar:** `docker build -t synapseflow-api services/api` y, con Blaze
habilitado, desplegar y probar el arranque en frío.

---

## Al cerrar F6

- Escribir el ADR-0006 si no se hizo en F6.4.
- Actualizar ambos READMEs y el `CHANGELOG.md`.
