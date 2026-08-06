# F7 · Consola web

**Depende de:** F6 (necesita la API).
**Bloqueo externo:** plan Blaze para desplegar.

## Qué tiene que lograr

La pantalla donde una inspectora pregunta y un supervisor aprueba. Cuatro
capacidades, en orden de importancia:

1. **Chat con citas verificables.** Cada afirmación normativa muestra su fuente y
   permite abrir el fragmento exacto. Una cita que no se puede inspeccionar no
   cumple su función.
2. **Bandeja de aprobaciones.** Lo que el supervisor ve cuando el agente propone
   una acción irreversible.
3. **Explorador de la ontología.** El dominio, navegable. Es lo que convierte el
   YAML en algo que un ingeniero de integridad puede revisar sin leer código.
4. **Panel de costos.** Consumo por hilo, desde la colección `llm_usage`.

---

## F7.1 · Scaffold

**Produce:** `apps/web/package.json`, `apps/web/index.html`

React + Vite + TypeScript. El build sale a `apps/web/dist`, que es lo que
`firebase.json` ya declara como `public`.

**El SDK de Firebase acá se usa solo para Auth**, para obtener el token que va a
la API. **No** para leer Firestore: las reglas cierran el acceso directo del
cliente a todas las colecciones, a propósito. Todo el dominio se sirve por la
API, que es la que aplica el RBAC.

Si alguien agrega una llamada al SDK de Firestore desde el frontend «para
resolver algo rápido», va a fallar por reglas. Eso es el diseño funcionando, no
un bug.

**Verificar:** `cd apps/web && npm install && npm run build`

---

## F7.2 · Chat con streaming y citas

**Produce:** `apps/web/src/Chat.tsx`

Consume los SSE de F6.2. Los eventos de herramienta se muestran **mientras
ocurren**, no al final: el usuario ve «consultando el activo P-2101-A», «calculando
vida remanente», y después la respuesta.

Las citas se renderizan como referencias desplegables que muestran el fragmento
recuperado, con su documento, sección y estado de vigencia.

**Verificar:** `cd apps/web && npm run build` y prueba manual contra la API.

---

## F7.3 · Bandeja de aprobaciones

**Produce:** `apps/web/src/Aprobaciones.tsx`

Cada pendiente muestra:

- La acción propuesta y sus argumentos **exactos**.
- El texto de aprobación que viene de `approval_prompt` en la ontología, ya
  formateado con los valores reales por el descriptor de `interrupt_config`.
- **El razonamiento que llevó a la propuesta**, reconstruido desde el historial
  de checkpoints. Un supervisor no puede decidir sobre una parada de equipo
  viendo solo la conclusión.
- Los botones según `allowed_decisions`: aprobar, rechazar, editar, responder.

Mostrar también las **vencidas**: un `thread_id` interrumpido que nadie resolvió
queda ocupando estado, y alguien tiene que verlo.

**Verificar:** `cd apps/web && npm run build` y el circuito completo de aprobación
desde el navegador.

---

## F7.4 · Despliegue

**Produce:** el sitio publicado

```bash
cd apps/web && npm run build
firebase deploy --only hosting
```

**Antes de desplegar, revisar los headers de `firebase.json`.** Hay una lección
ya aprendida en este proyecto: una regla de caché escrita para `/index.html`
**nunca coincide** cuando `cleanUrls` está activo, porque la ruta servida es `/`.
Verificar los headers realmente servidos con una petición, no la configuración
escrita.

**Verificar:** el circuito completo en el sitio publicado, y comprobar los
headers con `curl -I`.

---

## Al cerrar F7

- Actualizar ambos READMEs y el `CHANGELOG.md`.
- **El proyecto queda completo.** Actualizar el manual de estudio: los módulos que
  hoy están marcados «parcial» o «todavía no construido» pasan a implementados.
