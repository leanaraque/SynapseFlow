# F8 · Evaluación y CI de regresión

**Depende de:** F5 (hace falta un grafo que responda).
**Va antes que F6 a propósito.**

## Por qué antes que la API

Una vez que el grafo responde, empieza a cambiar seguido: se ajustan prompts, se
tocan umbrales de recuperación, se cambia de modelo. Sin una suite de evaluación,
esos cambios se juzgan a ojo, con dos o tres consultas de prueba, y la calidad se
degrada sin que nadie lo note.

Construir la API primero adelanta la demo pero deja el núcleo sin red de
contención justo cuando más la necesita.

---

## F8.1 · Golden dataset

**Produce:** `evals/datasets/*.jsonl`

Casos con la pregunta, la respuesta esperada y las fuentes que deberían citarse.

| Archivo | Qué cubre |
|---|---|
| `normativa.jsonl` | Preguntas sobre los códigos de inspección |
| `datos.jsonl` | Consultas sobre activos e historial |
| `calculos.jsonl` | Vida remanente, con el número correcto calculado a mano |
| `rechazo.jsonl` | **Preguntas sin respuesta en el corpus** |

**El cuarto archivo es el que más valor aporta.** Sin casos que el sistema deba
rechazar, la suite premia a un modelo que siempre contesta algo, que es
exactamente el comportamiento peligroso.

Incluir también casos donde la respuesta correcta está en un documento
**derogado**: el sistema debe responder desde el vigente, no desde aquel.

Formato:

```json
{"pregunta": "...", "respuesta_esperada": "...", "fuentes": ["API-570-2016 §7.4"], "debe_rechazar": false}
```

**Verificar:** `wc -l evals/datasets/*.jsonl`

---

## F8.2 · Evaluadores

**Produce:** `evals/evaluadores/*.py`

| Evaluador | Qué mide | Cómo |
|---|---|---|
| `fidelidad.py` | Que la respuesta no afirme lo que las fuentes no dicen | LLM-as-judge con el perfil `verifier` |
| `citas.py` | Que documento y sección existan y sean pertinentes | Determinístico, contra el corpus |
| `rechazo.py` | Que se niegue cuando corresponde | Determinístico |
| `calculos.py` | Que el número coincida con el esperado | Determinístico |

**Preferir determinístico sobre LLM-as-judge** donde se pueda. Un juez que es
otro modelo introduce su propia varianza, y entonces una eval que empeora puede
significar que el sistema empeoró o que el juez tuvo un mal día. Para citas,
rechazo y números no hace falta un juez: se comprueban.

**Verificar:** `pytest tests/evals -v`

---

## F8.3 · Corredor con línea base

**Produce:** `evals/run.py`

```bash
python -m evals.run --suite normativa
python -m evals.run --suite all --comparar-linea-base
```

- Guarda cada corrida en la colección `eval_runs` de Firestore.
- `--comparar-linea-base` contrasta contra la última corrida de `main` y sale con
  código distinto de cero ante regresión.
- Usa caché de LLM: la misma pregunta se evalúa muchas veces y no tiene sentido
  pagarla cada vez.

Reportar por métrica y **por caso**: saber que la fidelidad bajó de 0,91 a 0,87
no sirve; saber qué tres casos se rompieron, sí.

**Verificar:** `python -m evals.run --suite normativa`

---

## F8.4 · CI que bloquea el merge

**Produce:** `.github/workflows/evals.yml`

Corre en cada pull request y falla ante regresión respecto de la línea base.

**Requiere una API key en los secretos del repositorio.** Es el único job del CI
que consume cuota real, así que conviene:

- limitarlo a los PR que tocan prompts, RAG o agentes,
- publicar el resumen en el `GITHUB_STEP_SUMMARY`, con la tabla de métricas y los
  casos que cambiaron.

**Verificar:** abrir un PR de prueba y comprobar que el job corre y reporta.

---

## Al cerrar F8

- Actualizar ambos READMEs y el `CHANGELOG.md`.
- Documentar en `docs/04-llmops.md` cómo se lee un reporte de evals y qué hacer
  ante una regresión.
