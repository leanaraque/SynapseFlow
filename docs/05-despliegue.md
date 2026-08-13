# Despliegue

Dos artefactos, dos servicios: la **API** va a Cloud Run y la **consola** a
Firebase Hosting, con `/api/**` reescrito hacia la primera. Ese rewrite ya está
en `firebase.json` desde el inicio del proyecto, así que el frontend nunca
conoció una URL de backend.

> **Ejecutado el 2026-08-12.** Este procedimiento ya corrió de punta a punta
> contra `synapseflow-5fc52`. Lo que sigue es lo que se hizo, no lo que se
> planea, y las secciones llevan las correcciones que la ejecución obligó a
> hacer — estaban mal escritas y solo desplegando se supo.
>
> - API: <https://synapseflow-api-kizmckhcuq-rj.a.run.app>
> - Consola: <https://synapseflow-5fc52.web.app>
>
> **Antes de intentarlo en un proyecto nuevo, comprobá la facturación.** Que el
> proyecto figure con `billingEnabled: true` **no** alcanza: puede estar
> vinculado a una cuenta cerrada, y entonces toda API facturable responde
> `BILLING_DISABLED` con un `PERMISSION_DENIED` que no menciona la palabra
> facturación hasta el final del mensaje.
>
> ```bash
> gcloud beta billing accounts list   # mirá la columna OPEN, no solo que exista
> ```

---

## Antes de empezar

```bash
gcloud config set project synapseflow-5fc52
firebase use synapseflow-5fc52
```

**Verificá el proyecto activo antes de cada comando.** `gcloud` recuerda el
último que se usó, y desplegar en el proyecto equivocado es fácil de hacer y
molesto de deshacer.

---

## 1 · Los secretos

Las claves de proveedores van a **Secret Manager**, nunca al manifiesto del
servicio:

```bash
printf '%s' "$GOOGLE_API_KEY" | gcloud secrets create synapseflow-google-api-key \
  --data-file=- --replication-policy=automatic
```

`printf` y no `echo`: `echo` agrega un salto de línea, el secreto queda con un
`\n` al final y el proveedor devuelve 401 con un mensaje que no lo explica.

Después, permitir que la cuenta del servicio lo lea:

```bash
gcloud secrets add-iam-policy-binding synapseflow-google-api-key \
  --member="serviceAccount:synapseflow-api@synapseflow-5fc52.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## 2 · La cuenta de servicio

**El agente hereda los permisos del usuario, no los de esta cuenta** — eso lo
aplica la API (ver [ADR-0006](adr/0006-cloud-run-sobre-cloud-functions.md) y
`services/api/auth.py`). Pero la cuenta necesita lo suyo para llegar a Firestore:

```bash
gcloud iam service-accounts create synapseflow-api \
  --display-name="API de SynapseFlow en Cloud Run"

gcloud projects add-iam-policy-binding synapseflow-5fc52 \
  --member="serviceAccount:synapseflow-api@synapseflow-5fc52.iam.gserviceaccount.com" \
  --role="roles/datastore.user" --condition=None
```

**`--condition=None` no es opcional**: sin él, `gcloud` abre un prompt
interactivo pidiendo la condición IAM y el comando se cuelga en cualquier
script.

Y hace falta una más, que no es del servicio sino de Cloud Build: desde 2024 la
cuenta por defecto de compute ya no trae permisos, así que **el primer build de
todo proyecto nuevo falla** al leer su propio tarball de origen:

```
ERROR: could not resolve source: 723018546496-compute@developer.gserviceaccount.com
       does not have storage.objects.get access
```

```bash
gcloud projects add-iam-policy-binding synapseflow-5fc52 \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder" --condition=None
```

`roles/datastore.user`, no `roles/owner`. Una cuenta con más permisos de los que
usa convierte cualquier falla de la API en una falla con alcance ilimitado.

**No se crean claves descargables.** La organización lo prohíbe por política
(`iam.disableServiceAccountKeyCreation`) y hace bien: una clave de larga vida es
algo que hay que rotar y que nadie rota. En Cloud Run la identidad sale del
servicio (ADC) y en el CI, de Workload Identity Federation.

---

## 3 · La API

El `Dockerfile` no está en la raíz, así que `--source .` **no sirve**: `gcloud`
busca un `Dockerfile` en la raíz del contexto y no lo encuentra. Se construye
primero con `cloudbuild.yaml` y se despliega la imagen ya construida.

```bash
gcloud builds submit --config cloudbuild.yaml --region=southamerica-east1 .

gcloud run deploy synapseflow-api \
  --image southamerica-east1-docker.pkg.dev/synapseflow-5fc52/synapseflow/synapseflow-api:v1 \
  --region southamerica-east1 \
  --service-account synapseflow-api@synapseflow-5fc52.iam.gserviceaccount.com \
  --set-secrets GOOGLE_API_KEY=synapseflow-google-api-key:latest \
  --set-env-vars SYNAPSEFLOW_PROVIDER=gemini,GOOGLE_CLOUD_PROJECT=synapseflow-5fc52 \
  --memory 2Gi --cpu 1 --timeout 600 \
  --min-instances 0
```

Cuatro cosas que no son ajustables sin pensarlo:

- **`--set-secrets`, nunca `--set-env-vars`, para las claves.** Con la segunda
  quedan en el manifiesto del servicio, visibles para cualquiera con permiso de
  lectura en Cloud Run.
- **`--region southamerica-east1`** tiene que coincidir con `firebase.json`. Si
  no, el rewrite apunta a un servicio que no existe y la consola recibe 404 en
  cada llamada — con el servicio corriendo perfecto en otra región.
- **`SYNAPSEFLOW_PROVIDER=gemini`**, no `google`. Es un valor del enum `Provider`
  y ningún otro nombre vale. El contenedor **arranca igual** con un valor
  inválido, porque el gateway se construye perezosamente: falla en la primera
  consulta, con el usuario esperando. Pasó en el despliegue real.
- **`--timeout 600`**: un recorrido completo con RAG y varios especialistas pasa
  cómodo del minuto por defecto, y el corte llega justo cuando la consulta era
  difícil.

El nombre `synapseflow-api` no es libre: es el `serviceId` que declara
`firebase.json`, y hay un test que compara las dos declaraciones.

### Por qué el servicio acepta invocaciones sin autenticar

**Este documento decía `--no-allow-unauthenticated` y estaba mal.** El argumento
era que el tráfico legítimo entra por el rewrite de Hosting, «que sí está
autorizado». No lo está: Firebase Hosting **no tiene una identidad de servicio**
que se le pueda dar `roles/run.invoker`.

```
ERROR: Service account service-PROJECT@gcp-sa-firebasehosting.iam.gserviceaccount.com
       does not exist.
ERROR: (gcloud.beta.services.identity.create) INVALID_ARGUMENT:
       Invalid service producer: firebasehosting.googleapis.com
```

Con el servicio privado, el rewrite devuelve **403 con un cuerpo HTML** — no un
error de la API, sino de Cloud Run rechazando a Hosting. Así que:

```bash
gcloud run services add-iam-policy-binding synapseflow-api \
  --region southamerica-east1 --member=allUsers --role=roles/run.invoker
```

**Lo que protege la API es su propia validación de token, no la red.** Sin
`Authorization: Bearer` devuelve 401, y con un token inválido también; `/health`
y `/api/roles` son públicos a propósito. Lo que se pierde al abrirla es la
barrera *previa*: tráfico no autenticado llega al contenedor y puede provocar
arranques en frío. Para un piloto real se pone Cloud Armor delante, o se cambia
el rewrite por un balanceador. Está anotado como deuda, no resuelto.

### Arranque en frío

Con `--min-instances 0` la primera consulta después de un rato de inactividad
paga el arranque del contenedor con todo el árbol de LangChain. Es aceptable en
una demostración y no en un piloto: ahí conviene `--min-instances 1`, que **tiene
costo aunque nadie pregunte**. Es una decisión de operación, no de arquitectura.

---

## 4 · La consola

```bash
cd apps/web
cp .env.example .env.production   # completar con los valores del proyecto
npm ci
npm run build
cd ../..
firebase deploy --only hosting
```

`npm ci` y no `npm install`: instala exactamente lo que dice el lock. Con
`install`, lo que se despliega puede no ser lo que se probó.

---

## 5 · Verificar lo servido, no lo escrito

**Esta sección existe por un error ya cometido en este repositorio.** Había una
regla de caché escrita para `/index.html` que **nunca coincidía**, porque con
`cleanUrls` la ruta servida es `/`. La configuración se veía bien y el
comportamiento era otro.

La lección general: los headers se comprueban con una petición.

```bash
# El HTML no se cachea: si se cacheara, un despliegue nuevo no llegaría a
# quien ya visitó el sitio hasta que venza el TTL.
curl -sI https://synapseflow-5fc52.web.app/ | grep -i cache-control

# Los assets sí, y para siempre: llevan hash en el nombre.
curl -sI https://synapseflow-5fc52.web.app/assets/index-*.js | grep -i cache-control

# El rewrite llega a Cloud Run y no al index.html del hosting.
curl -s https://synapseflow-5fc52.web.app/api/roles | head -c 200
```

`/api/roles` es el endpoint bueno para esta prueba: no pide identidad —los roles
salen del YAML, no son de nadie— así que un 200 con JSON confirma el rewrite sin
mezclarlo con un problema de token. Si devuelve HTML, el rewrite no está tomando
y `firebase.json` es lo primero que hay que mirar.

### El circuito completo

Con la consola publicada, la prueba que vale es la del proyecto entero:

1. Entrar con una cuenta que tenga el custom claim `synapseflow_rol`.
   Una sin el claim tiene que ver la explicación, **no** una consola vacía y
   tampoco un rol por defecto.
2. Preguntar por `P-2101-A`. Tienen que verse los eventos de herramienta
   **mientras ocurren**, la respuesta con citas, y las citas tienen que abrirse.
3. Confirmar que el agente **propone** la parada y no la ejecuta.
4. Entrar con un supervisor distinto y aprobarla desde la bandeja. La propuesta
   del propio proponente **no** puede aparecerle a él.
5. Comprobar en Firestore que el activo cambió de estado recién después de la
   aprobación, y que el log de auditoría tiene los dos eventos —propuesta y
   aprobación— con el mismo `thread_id`.

El paso 5 es el que hace verdadero todo lo demás.

---

## Sembrar el corpus

La ingesta del corpus contra la base real es un paso aparte, y necesita
credenciales de aplicación de una cuenta con acceso al proyecto:

```bash
gcloud auth application-default login

# Datos estructurados del dominio.
python -m scripts.seed --permitir-produccion
```

`--permitir-produccion` es obligatorio a propósito: sin esa bandera el script se
niega a tocar nada que no sea el emulador.

**El corpus se carga aparte y no tiene CLI**: trocear y vectorizar necesita un
modelo de embeddings, así que la ingesta vive en la biblioteca y se invoca con el
gateway ya construido.

```bash
python -c "
import asyncio
from synapseflow.llm.gateway import Gateway
from synapseflow.persistence.vectorstore import FirestoreVectorStore
from synapseflow.rag.ingesta import ingestar_corpus

async def main():
    almacen = FirestoreVectorStore(Gateway().embeddings())
    print(await ingestar_corpus(almacen))

asyncio.run(main())
"
```

Es idempotente: el id de cada fragmento sale de su documento y su sección, así
que una segunda corrida sobreescribe en lugar de duplicar.

> **Trampa conocida:** las credenciales de aplicación (ADC) y la cuenta de
> `gcloud` son **cosas distintas**. Se puede tener `gcloud` autenticado como una
> cuenta con permisos y el ADC de otra sin ellos; el síntoma es un 403 de
> Firestore que no se explica mirando `gcloud config list`. Se diagnostica
> comparando una llamada REST con el token del usuario contra la misma con el
> ADC.

---

## Rollback

```bash
# Consola: Firebase guarda las versiones anteriores.
firebase hosting:rollback

# API: cada despliegue es una revisión y el tráfico se puede mover entera.
gcloud run services update-traffic synapseflow-api \
  --region southamerica-east1 --to-revisions REVISION_ANTERIOR=100
```

Lo que **no** vuelve atrás con esto es Firestore: los índices, las reglas y los
datos. Un cambio de esquema hay que pensarlo compatible hacia atrás antes de
desplegarlo, porque el rollback del código no lo acompaña.

---

## Estado · verificado el 2026-08-12

Ejecutado contra `synapseflow-5fc52`. Lo comprobado con `curl` sobre lo servido,
no sobre la configuración escrita:

| Prueba | Resultado |
|---|---|
| `GET /` | `200` · `cache-control: no-cache` ✅ |
| `GET /assets/index-*.js` | `200` · `public,max-age=31536000,immutable` ✅ |
| `GET /api/roles` por el rewrite | `200` · los cinco roles del YAML ✅ |
| `GET /api/yo` sin token | `401` · «Falta el header 'Authorization: Bearer'» ✅ |
| `GET /api/yo` con token inválido | `401`, **no 500** ✅ |
| `GET /api/aprobaciones` sin token | `401` ✅ |

La regla de caché sobre `/` es la que se agregó al corregir el error registrado
en las convenciones. **Sin ella el shell de la SPA se habría cacheado**, y esta
tabla lo confirma sobre la respuesta real.

El 401 con token inválido importa más de lo que parece: prueba que
`firebase_admin` se inicializó con la identidad del servicio y que rechaza un
token falso — la capa de identidad, verificada contra Firebase de verdad.

### Lo que falta

**Las colecciones del dominio están vacías.** Sembrarlas necesita
`gcloud auth application-default login` en una máquina con los datos generados:
el ADC de la máquina de desarrollo pertenece a otra cuenta y Firestore devuelve
`403 Missing or insufficient permissions`. Es el bloqueo que el mapa de acción ya
declaraba, y el único paso de este documento que no se pudo ejecutar.

Hasta que se siembre, la API responde y aplica identidad, pero el agente no tiene
sobre qué contestar.
