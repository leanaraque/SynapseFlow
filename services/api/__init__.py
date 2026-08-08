"""La API que expone el grafo por HTTP.

No reimplementa ninguna garantía: los permisos salen de la ontología, los gates
del middleware y la redacción de PII del gateway. Lo único que agrega es
identidad —del token de Firebase al `ExecutionContext`— y transporte.

Ver docs/plan/fases/F6-api.md
"""
