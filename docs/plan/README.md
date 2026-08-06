# Plan de trabajo · punto de entrada

**Si vas a escribir código en este repositorio, empezá acá.** Este directorio
contiene el plan completo, commit por commit, hasta terminar el proyecto.

---

## Los tres pasos, en orden

### 1 · Preguntá en qué estado está el proyecto

```bash
python -m scripts.estado
```

No supongas, no leas una checklist, no confíes en lo que diga un commit
anterior. **Corré el comando.** Inspecciona el repositorio y te dice:

- en qué fase está el proyecto,
- cuál es el commit siguiente y qué falta exactamente para darlo por hecho,
- qué archivo leer con las instrucciones,
- con qué comando verificarlo,
- qué compromisos de diseño están cumplidos y cuáles no.

El estado no se mantiene a mano en ningún archivo: se deriva del código. Por eso
no se puede desincronizar.

### 2 · Leé las convenciones

[`00-convenciones.md`](00-convenciones.md) — obligatorio antes del primer commit.

Contiene reglas que **no se negocian** y, sobre todo, una sección de *API
verificada* con hallazgos sobre LangChain 1.x que contradicen lo que hay en
internet. Escribir código sin leerla es garantía de trabajo perdido.

### 3 · Hacé un solo commit

Abrí el documento de la fase que te indicó el paso 1, buscá el id del commit y
seguí sus instrucciones. Un commit por vez.

Al terminar, volvé al paso 1.

---

## Estructura

```
docs/plan/
  README.md            este archivo
  00-convenciones.md   reglas obligatorias y API verificada
  fases/
    F0-datos.md        datos sintéticos y corpus
    F1-gateway.md      gateway de LLM
    F2-dominio.md      las nueve acciones
    F3-rag.md          recuperación con citas
    F4-gobernanza.md   PII, auditoría, política
    F5-grafo.md        el agente completo
    F6-api.md          API en Cloud Run
    F7-consola.md      consola web
    F8-evals.md        evaluación y CI
scripts/estado.py      el detector de estado
```

## El orden de las fases y por qué

```
F1 Gateway  ─┐ independientes entre sí: cualquiera de las dos primero
F0 Datos    ─┘
F2 Dominio      (necesita F0)
F3 RAG          (necesita F0 y F1)
F4 Gobernanza   (necesita F1)
F5 Grafo        (necesita F1, F2, F3, F4)   ← HITO PRINCIPAL
F8 Evals        (necesita F5)
F6 API          (necesita F5)
F7 Consola      (necesita F6)
```

**F5 es el hito que importa.** Ahí el sistema deja de ser piezas sueltas y hace
el recorrido completo: pregunta sobre un activo, cálculo determinístico,
fundamento normativo con citas, propuesta de acción irreversible y freno
esperando a un humano. F6 y F7 lo hacen usable por otros; **F5 lo hace
verdadero**.

**Las evals van antes que la API** a propósito. Una vez que el grafo responde, lo
que protege la calidad es la suite de evaluación. Construir la API primero
adelanta la demo pero deja el núcleo sin red de contención justo cuando empieza a
cambiar seguido.

## Dos bloqueos externos

Ninguno impide escribir ni testear código: para eso están el modelo falso
(`F1.2`) y el emulador de Firestore.

| Bloqueo | Impide | Se resuelve |
|---|---|---|
| `GOOGLE_API_KEY` | Verificar contra un modelo real | [aistudio.google.com/apikey](https://aistudio.google.com/apikey), va en `.env` |
| Plan Blaze en Firebase | Desplegar F6 y F7 | Consola de Firebase → facturación |

Si te falta la clave, **seguí igual**: escribí el código y testeálo con
`FakeChatModel`. No bloquees el avance esperando una credencial.

## Qué hacer si algo no encaja

El plan puede estar equivocado: se escribió antes de implementar. Si al abordar
un commit descubrís que la descomposición no cierra —falta un paso, sobra otro,
el orden no funciona— **corregí el plan en el mismo commit** y explicá por qué en
el cuerpo del mensaje.

Lo que no hay que hacer es seguir un plan que ya sabés que está mal, ni
abandonarlo sin dejar registro. Un plan desactualizado es peor que no tener plan,
porque el siguiente que llegue le va a creer.
