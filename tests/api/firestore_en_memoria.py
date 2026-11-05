"""Un Firestore mínimo en memoria, para los tests que no son sobre Firestore.

Existe porque lo que se verifica en `test_aprobaciones.py` es **quién puede
aprobar qué**, y esa lógica no debería necesitar un emulador corriendo para
poder ejecutarse: los tests que más importan de este proyecto son los negativos
de autoridad, y un test que solo corre con infraestructura es un test que
alguien va a saltear.

Implementa exactamente lo que usan `services/api/aprobaciones.py`,
`services/api/hilos.py` y `governance/auditoria.py` — `set`, `create`, `get`,
`update` con precondición, `where`, `order_by`, `limit`, `stream` y `batch` — y
nada más. Un doble que implementa de más invita a
escribir tests que dependen de comportamiento que nadie verificó contra el
servicio real.

**No reemplaza al emulador.** Los tests marcados `emulator` siguen corriendo
contra Firestore de verdad: son los que descubren que un índice falta o que un
tipo no serializa.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator
from typing import Any

from google.api_core.exceptions import Aborted, AlreadyExists, FailedPrecondition


class Instantanea:
    """Lo que devuelve un `get()`.

    `update_time` es un contador de versión y no una marca de tiempo: lo único
    que el código hace con él es pasarlo de vuelta como precondición, y para eso
    alcanza con que cambie en cada escritura.
    """

    def __init__(
        self,
        id_: str,
        datos: dict[str, Any] | None,
        referencia: Documento,
        update_time: int | None = None,
    ) -> None:
        self.id = id_
        self._datos = datos
        self.reference = referencia
        self.update_time = update_time

    @property
    def exists(self) -> bool:
        return self._datos is not None

    def to_dict(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._datos) if self._datos is not None else None


class OpcionDeEscritura:
    """Lo que devuelve `write_option(last_update_time=...)`."""

    def __init__(self, last_update_time: Any) -> None:
        self.last_update_time = last_update_time


class Documento:
    def __init__(self, coleccion: Coleccion, id_: str) -> None:
        self._coleccion = coleccion
        self.id = id_

    def _abortar_si_corresponde(self) -> None:
        # Firestore aborta una escritura con contención sin aplicarla: lo que
        # devolvió el emulador en CI como «Transaction lock timeout».
        if self._coleccion.abortos[0] > 0:
            self._coleccion.abortos[0] -= 1
            raise Aborted("Transaction lock timeout.")

    def _escribir(self, datos: dict[str, Any]) -> None:
        self._coleccion.datos[self.id] = datos
        self._coleccion.versiones[self.id] = self._coleccion.versiones.get(self.id, 0) + 1

    async def set(self, datos: dict[str, Any]) -> None:
        self._escribir(copy.deepcopy(datos))

    async def create(self, datos: dict[str, Any]) -> None:
        """Como en Firestore: falla si el documento ya existe."""
        self._abortar_si_corresponde()
        if self.id in self._coleccion.datos:
            raise AlreadyExists(f"el documento '{self.id}' ya existe")
        self._escribir(copy.deepcopy(datos))

    async def get(self) -> Instantanea:
        instantanea = Instantanea(
            self.id,
            copy.deepcopy(self._coleccion.datos.get(self.id)),
            self,
            self._coleccion.versiones.get(self.id),
        )
        if self._coleccion.ceder_al_leer:
            # Devuelve el control al event loop después de leer: es lo que
            # permite que dos corrutinas lean el mismo documento antes de que
            # cualquiera escriba, que es la carrera que hay que reproducir.
            await asyncio.sleep(0)
        return instantanea

    async def update(self, cambios: dict[str, Any], option: Any = None) -> None:
        self._abortar_si_corresponde()
        if self.id not in self._coleccion.datos:
            raise KeyError(f"no existe el documento '{self.id}'")
        if option is not None and option.last_update_time != self._coleccion.versiones.get(self.id):
            raise FailedPrecondition(f"el documento '{self.id}' cambió desde que se leyó")
        actualizado = {**self._coleccion.datos[self.id], **copy.deepcopy(cambios)}
        self._escribir(actualizado)

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
            yield Instantanea(
                id_, datos, Documento(self._coleccion, id_), self._coleccion.versiones.get(id_)
            )


class Coleccion(Consulta):
    def __init__(
        self,
        datos: dict[str, dict[str, Any]],
        versiones: dict[str, int] | None = None,
        *,
        ceder_al_leer: bool = False,
        abortos: list[int] | None = None,
    ) -> None:
        self.datos = datos
        self.versiones = versiones if versiones is not None else {}
        self.ceder_al_leer = ceder_al_leer
        self.abortos = abortos if abortos is not None else [0]
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

    def __init__(self, *, ceder_al_leer: bool = False) -> None:
        self._colecciones: dict[str, dict[str, dict[str, Any]]] = {}
        self._versiones: dict[str, dict[str, int]] = {}
        # Solo para los tests de concurrencia: ver `Documento.get`.
        self.ceder_al_leer = ceder_al_leer
        # Cuántas de las próximas escrituras abortan. Una lista para que todas
        # las colecciones compartan el mismo contador.
        self._abortos = [0]

    def collection(self, nombre: str) -> Coleccion:
        return Coleccion(
            self._colecciones.setdefault(nombre, {}),
            self._versiones.setdefault(nombre, {}),
            ceder_al_leer=self.ceder_al_leer,
            abortos=self._abortos,
        )

    def batch(self) -> Lote:
        return Lote()

    @staticmethod
    def write_option(*, last_update_time: Any) -> OpcionDeEscritura:
        return OpcionDeEscritura(last_update_time)

    # ── Ayudas para los tests ────────────────────────────────────────────────

    def abortar_proximas(self, cuantas: int) -> None:
        """Las próximas `cuantas` escrituras (`create`/`update`) lanzan `Aborted`."""
        self._abortos[0] = cuantas

    def contenido(self, nombre: str) -> dict[str, dict[str, Any]]:
        return self._colecciones.setdefault(nombre, {})


def _cumple(datos: dict[str, Any], campo: str, operador: str, valor: Any) -> bool:
    actual = datos.get(campo)
    if operador == "==":
        return bool(actual == valor)
    if operador == "in":
        return actual in valor
    if operador == ">=":
        return actual is not None and actual >= valor
    if operador == "array_contains":
        return valor in (actual or [])
    raise NotImplementedError(
        f"el doble en memoria no implementa el operador '{operador}'. "
        "Agregalo acá si una consulta nueva lo necesita."
    )
