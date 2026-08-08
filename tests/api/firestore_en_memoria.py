"""Un Firestore mínimo en memoria, para los tests que no son sobre Firestore.

Existe porque lo que se verifica en `test_aprobaciones.py` es **quién puede
aprobar qué**, y esa lógica no debería necesitar un emulador corriendo para
poder ejecutarse: los tests que más importan de este proyecto son los negativos
de autoridad, y un test que solo corre con infraestructura es un test que
alguien va a saltear.

Implementa exactamente lo que usan `services/api/aprobaciones.py` y
`governance/auditoria.py` — `set`, `get`, `update`, `where`, `order_by`, `limit`,
`stream` y `batch` — y nada más. Un doble que implementa de más invita a
escribir tests que dependen de comportamiento que nadie verificó contra el
servicio real.

**No reemplaza al emulador.** Los tests marcados `emulator` siguen corriendo
contra Firestore de verdad: son los que descubren que un índice falta o que un
tipo no serializa.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from typing import Any


class Instantanea:
    """Lo que devuelve un `get()`."""

    def __init__(self, id_: str, datos: dict[str, Any] | None, referencia: Documento) -> None:
        self.id = id_
        self._datos = datos
        self.reference = referencia

    @property
    def exists(self) -> bool:
        return self._datos is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._datos) if self._datos is not None else None


class Documento:
    def __init__(self, coleccion: Coleccion, id_: str) -> None:
        self._coleccion = coleccion
        self.id = id_

    async def set(self, datos: dict[str, Any]) -> None:
        self._coleccion.datos[self.id] = copy.deepcopy(datos)

    async def get(self) -> Instantanea:
        return Instantanea(self.id, self._coleccion.datos.get(self.id), self)

    async def update(self, cambios: dict[str, Any]) -> None:
        if self.id not in self._coleccion.datos:
            raise KeyError(f"no existe el documento '{self.id}'")
        self._coleccion.datos[self.id].update(copy.deepcopy(cambios))

    async def delete(self) -> None:
        self._coleccion.datos.pop(self.id, None)


class Consulta:
    """Filtros y orden, aplicados en memoria al recorrer."""

    def __init__(self, coleccion: Coleccion) -> None:
        self._coleccion = coleccion
        self._filtros: list[tuple[str, str, Any]] = []
        self._orden: tuple[str, bool] | None = None
        self._tope: int | None = None

    def where(self, *, filter: Any) -> Consulta:
        campo = getattr(filter, "field_path", None)
        operador = getattr(filter, "op_string", None)
        valor = getattr(filter, "value", None)
        self._filtros.append((str(campo), str(operador), valor))
        return self

    def order_by(self, campo: str, direction: str = "ASCENDING") -> Consulta:
        self._orden = (campo, direction.upper().startswith("DESC"))
        return self

    def limit(self, cuantos: int) -> Consulta:
        self._tope = cuantos
        return self

    async def stream(self) -> AsyncIterator[Instantanea]:
        filas = [
            (id_, datos)
            for id_, datos in self._coleccion.datos.items()
            if all(_cumple(datos, campo, op, valor) for campo, op, valor in self._filtros)
        ]

        if self._orden is not None:
            campo, descendente = self._orden
            filas.sort(key=lambda par: str(par[1].get(campo) or ""), reverse=descendente)

        if self._tope is not None:
            filas = filas[: self._tope]

        for id_, datos in filas:
            yield Instantanea(id_, datos, Documento(self._coleccion, id_))


class Coleccion(Consulta):
    def __init__(self, datos: dict[str, dict[str, Any]]) -> None:
        self.datos = datos
        super().__init__(self)

    def document(self, id_: str) -> Documento:
        return Documento(self, id_)


class Lote:
    """El `batch()` que usa `registrar_lote`."""

    def __init__(self) -> None:
        self._pendientes: list[tuple[Documento, dict[str, Any]]] = []

    def set(self, referencia: Documento, datos: dict[str, Any]) -> None:
        self._pendientes.append((referencia, datos))

    async def commit(self) -> None:
        for referencia, datos in self._pendientes:
            await referencia.set(datos)
        self._pendientes.clear()


class ClienteEnMemoria:
    """Lo mínimo de `firestore.AsyncClient` que este proyecto invoca."""

    def __init__(self) -> None:
        self._colecciones: dict[str, dict[str, dict[str, Any]]] = {}

    def collection(self, nombre: str) -> Coleccion:
        return Coleccion(self._colecciones.setdefault(nombre, {}))

    def batch(self) -> Lote:
        return Lote()

    # ── Ayudas para los tests ────────────────────────────────────────────────

    def contenido(self, nombre: str) -> dict[str, dict[str, Any]]:
        return self._colecciones.setdefault(nombre, {})


def _cumple(datos: dict[str, Any], campo: str, operador: str, valor: Any) -> bool:
    actual = datos.get(campo)
    if operador == "==":
        return bool(actual == valor)
    if operador == "in":
        return actual in valor
    if operador == "array_contains":
        return valor in (actual or [])
    raise NotImplementedError(
        f"el doble en memoria no implementa el operador '{operador}'. "
        "Agregalo acá si una consulta nueva lo necesita."
    )
