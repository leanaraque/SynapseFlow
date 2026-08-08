# LLMOps · cómo leer una corrida de evals

Este documento responde dos preguntas: **qué significa el reporte** y **qué hacer
cuando el CI bloquea un merge**.

- **Estado del documento:** vigente
- **Última revisión:** 2026-08-08 (F8 completa)

---

## 1 · Por qué existe la suite

Una vez que el grafo responde, empieza a cambiar seguido: se ajustan prompts, se
tocan umbrales de recuperación, se cambia de modelo. Sin una suite de evaluación
esos cambios se juzgan a ojo, con dos o tres consultas de prueba, y la calidad se
degrada sin que nadie lo note.

Por eso F8 se hizo **antes** que la API. Construir la API primero adelanta la
demo y deja el núcleo sin red de contención justo cuando más la necesita.

## 2 · Las cinco métricas

| Métrica | Qué mide | Cómo |
|---|---|---|
| `correccion_del_rechazo` | Que se niegue cuando corresponde, y que **no** se niegue cuando no | determinístico |
| `precision_de_citas` | Que documento y sección existan y estén entre lo recuperado | determinístico |
| `exactitud_del_calculo` | Que el número coincida con el esperado, dentro de su tolerancia | determinístico |
| `no_exposicion_de_datos` | Que no aparezca un legajo en la respuesta | determinístico |
| `fidelidad` | Que la respuesta no afirme lo que las fuentes no dicen | LLM-as-judge |

**Cuatro de cinco son determinísticas, y es deliberado.** Un juez que es otro
modelo introduce su propia varianza: una eval que empeora pasa a tener dos
explicaciones —el sistema empeoró o el juez tuvo un mal día— y con dos
explicaciones deja de servir para decidir.

### La que más importa

`correccion_del_rechazo`. Un asistente que siempre contesta algo es más peligroso
que uno que a veces dice que no sabe, y en este dominio la diferencia se mide en
paradas de planta. Tiene dos caras y las dos se penalizan:

- **Responder sin fundamento** → 0,0. Es el modo de falla peligroso.
- **Negarse a algo que sí está en el corpus** → 0,5. Es molesto y hace el sistema
  inútil, pero no es peligroso.

La asimetría permite leer en el reporte si el sistema se volvió **peligroso** o
solamente **tímido**. Sin ella, un sistema que rechaza todo puntuaría perfecto.

## 3 · Cómo se corre

```bash
# Una suite, sin dejar rastro en Firestore
python -m evals.run --suite normativa --sin-guardar

# Todo, comparando contra la última corrida de main
python -m evals.run --suite all --comparar-linea-base
```

Consume cuota real: usa el proveedor configurado en `SYNAPSEFLOW_PROVIDER`.

## 4 · Cómo se lee el reporte

```
Suite: normativa  ·  10 casos  ·  2 con al menos un fallo

MÉTRICAS
  correccion_del_rechazo       1.000  (base 1.000, Δ +0.000)
  fidelidad                    0.870  (base 0.910, Δ -0.040)
  precision_de_citas           0.900  (base 1.000, Δ -0.100)

CASOS QUE FALLARON
  [norm-003] precision_de_citas: cita a secciones que no existen: ['API-570-2016 §12.9']
  [norm-009] precision_de_citas: cita normativa derogada: ['PROC-INT-009 §3.2']
```

**Las métricas son para la comparación automática; los casos son para vos.**
Saber que la fidelidad bajó 0,04 no dice qué mirar. Saber que `norm-009` empezó a
citar el procedimiento derogado, sí.

## 5 · Los tres códigos de salida

| Código | Significa | Qué hacer |
|---|---|---|
| `0` | Sin regresión | Nada |
| `1` | Una métrica bajó más allá de su margen | Ver § 6 |
| `2` | **Ningún caso se pudo ejecutar** | No mires las métricas: revisá credenciales, proveedor y acceso a Firestore |

El `2` existe porque una corrida rota **no es una regresión** y el diagnóstico es
completamente distinto. Sin distinguirlo, un CI con el proveedor mal configurado
reportaría fracaso total y saldría en verde mientras no hubiera línea base.

## 6 · Qué hacer ante una regresión

**No subas el margen.** Es la reacción natural y es la que convierte la suite en
decoración. Los márgenes están en `MARGEN`, en `evals/run.py`: cero para las
determinísticas —si bajan, algo se rompió— y 0,05 para la fidelidad, que es lo
único que tiene varianza legítima.

El orden para diagnosticar:

1. **Mirá los casos, no el promedio.** El reporte los nombra con su motivo.
2. **Separá qué métrica cayó.** Si es `precision_de_citas` o
   `exactitud_del_calculo`, hay un bug: son determinísticas. Si es `fidelidad`,
   puede ser el prompt de síntesis o puede ser el retriever trayendo peor
   material — el detalle dice qué afirmación quedó sin respaldo.
3. **Si cayó `correccion_del_rechazo` hacia el lado peligroso**, o sea el sistema
   empezó a responder lo que antes rechazaba, eso frena el merge sin discusión.
   Es literalmente el compromiso 4.
4. **Reproducí el caso solo.** `--suite` acepta una sola suite, y el caso trae la
   pregunta textual.

## 7 · El CI

`.github/workflows/evals.yml` corre en los PR que tocan agentes, RAG,
gobernanza, el gateway, el dominio, las evals o el corpus. **Es el único job que
consume cuota real**, así que no corre por cambiar un README.

Se autentica por Workload Identity Federation, sin claves: la organización
prohíbe las claves descargables de cuenta de servicio, y hace bien —una clave en
los secretos de GitHub es una credencial de larga vida que hay que rotar y que
nadie rota—. Las instrucciones de configuración por única vez están en el
encabezado del propio workflow.

Mientras WIF no esté configurado, el job **se saltea** en lugar de fallar. Un CI
que falla por infraestructura que nadie configuró todavía enseña a ignorar el
rojo, y entonces deja de proteger de lo que sí importa.

## 8 · Sobre el golden dataset

Las citas esperadas se contrastan contra el corpus en
`tests/evals/test_datasets.py`. Un caso que espera una sección inexistente
produce una métrica que siempre da mal, y nadie sabe si el problema es el sistema
o el dataset: la suite dejaría de ser falsificable.

Lo mismo del otro lado: una cita **prohibida** que el corpus no puede producir
hace que el caso pase por vacío, y el día que el sistema empiece a citar el
documento derogado de verdad, nadie se entera.

Al agregar casos, respetar las dos direcciones — y mantener casos de rechazo. Sin
ellos, la suite premia a un modelo que siempre contesta algo.
