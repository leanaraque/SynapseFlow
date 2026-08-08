"""Suite de evaluación y CI de regresión.

Una vez que el grafo responde, empieza a cambiar seguido: se ajustan prompts, se
tocan umbrales de recuperación, se cambia de modelo. Sin una suite de evaluación
esos cambios se juzgan a ojo, con dos o tres consultas de prueba, y la calidad se
degrada sin que nadie lo note.

Por eso F8 va antes que la API: construirla primero adelanta la demo y deja el
núcleo sin red de contención justo cuando más la necesita.

Ver docs/plan/fases/F8-evals.md
"""
