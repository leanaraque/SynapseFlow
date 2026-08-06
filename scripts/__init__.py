"""Utilidades de línea de comandos del repositorio.

No forma parte del paquete distribuible: `pyproject.toml` solo empaqueta
`packages/synapseflow`. Estos módulos se corren desde la raíz del repositorio
con `python -m scripts.<nombre>`.

El archivo existe para que `scripts` sea un paquete declarado y no uno implícito.
Sin él, `mypy scripts` ve el mismo archivo bajo dos nombres —`generar_datos` y
`scripts.generar_datos`— en cuanto un script importa a otro, y aborta el análisis
antes de revisar nada.
"""
