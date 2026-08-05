## Qué cambia y por qué

<!-- El *qué* ya está en el diff. Contá el *por qué*: qué problema resuelve. -->

## Cómo se verifica

<!-- Qué test falla sin este cambio. Si no hay ninguno, explicá por qué. -->

## Checklist

- [ ] `ruff check .` y `ruff format --check .` pasan
- [ ] `mypy packages/synapseflow` pasa
- [ ] `pytest -m "not emulator"` pasa
- [ ] `pytest` completo pasa con el emulador de Firestore corriendo
- [ ] Si toqué la ontología: `synapseflow ontology validate` pasa y revisé el
      RBAC resultante con `synapseflow ontology tools --role ...`
- [ ] Si cambié una decisión estructural: agregué o reemplacé un ADR en `docs/adr/`
- [ ] Si moví un componente de estado: actualicé la tabla del README
