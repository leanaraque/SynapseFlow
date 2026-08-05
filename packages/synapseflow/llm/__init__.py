"""Gateway de modelos.

En construcción. Por ahora este paquete solo contiene `models.yaml`, el catálogo
que resuelve *perfil de tarea + proveedor → modelo concreto*, junto con los
precios que alimentan la contabilidad de costos.

La decisión de diseño —por qué el código pide un perfil y nunca un nombre de
modelo, y por qué el gateway es el único punto donde el texto cruza el perímetro
de datos— está en docs/adr/0004-gateway-provider-agnostic.md
"""
