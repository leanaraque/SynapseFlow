"""Servicios desplegables. Hoy uno: la API sobre Cloud Run.

Es un paquete de verdad y no un directorio suelto porque, como namespace
package, mypy resuelve el mismo archivo con dos nombres —`api.auth` y
`services.api.auth`— y aborta el análisis.
"""
