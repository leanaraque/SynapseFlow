# Despliegue

Dos artefactos, dos servicios: la **API** va a Cloud Run y la **consola** a
Firebase Hosting, con `/api/**` reescrito hacia la primera. Ese rewrite ya está
en `firebase.json` desde el inicio del proyecto, así que el frontend nunca
conoció una URL de backend.

> **Estado.** El plan Blaze ya está habilitado en `synapseflow-5fc52`, así que
> nada de esto está bloqueado por facturación. Lo que falta es material: la
> imagen de la API todavía no se construyó —hace falta Docker o Cloud Build— y
> nadie ejecutó estos comandos. El código, los tests y el build de la consola no
> necesitan ninguna de las dos cosas.

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
  --role="roles/datastore.user"
```

`roles/datastore.user`, no `roles/owner`. Una cuenta con más permisos de los que
usa convierte cualquier falla de la API en una falla con alcance ilimitado.

**No se crean claves descargables.** La organización lo prohíbe por política
(`iam.disableServiceAccountKeyCreation`) y hace bien: una clave de larga vida es
algo que hay que rotar y que nadie rota. En Cloud Run la identidad sale del
servicio (ADC) y en el CI, de Workload Identity Federation.

---

## 3 · La API

```bash
gcloud run deploy synapseflow-api \
  --source . \
  --region southamerica-east1 \
  --service-account synapseflow-api@synapseflow-5fc52.iam.gserviceaccount.com \
  --set-secrets GOOGLE_API_KEY=synapseflow-google-api-key:latest \
  --set-env-vars SYNAPSEFLOW_PROVIDER=google,GOOGLE_CLOUD_PROJECT=synapseflow-5fc52 \
  --no-allow-unauthenticated \
  --min-instances 0
```

Tres cosas que no son ajustables sin pensarlo:

- **`--set-secrets`, nunca `--set-env-vars`, para las claves.** Con la segunda
  quedan en el manifiesto del servicio, visibles para cualquiera con permiso de
  lectura en Cloud Run.
- **`--region southamerica-east1`** tiene que coincidir con `firebase.json`. Si
  no, el rewrite apunta a un servicio que no existe y la consola recibe 404 en
  cada llamada — con el servicio corriendo perfecto en otra región.
- **`--no-allow-unauthenticated`**: el tráfico legítimo entra por el rewrite de
  Hosting, que sí está autorizado. Abrirlo al público expondría la API sin más
  defensa que su propia validación de token.

El nombre `synapseflow-api` no es libre: es el `serviceId` que declara
`firebase.json`, y hay un test que compara las dos declaraciones.

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

## Estado

Nada de este documento se ejecutó todavía: el despliegue necesita plan Blaze, y
la construcción de la imagen necesita Docker o Cloud Build. Lo que sí está
verificado es todo lo anterior — la imagen, sus propiedades, la API y la consola
tienen tests, y `python -m scripts.estado` reporta qué falta.

El estado de este commit se deriva de que exista **este procedimiento**, no de un
artefacto de build: `apps/web/dist/` está en `.gitignore`, y un detector que
dependiera de él daría el despliegue por hecho apenas alguien corriera
`npm run build`.
