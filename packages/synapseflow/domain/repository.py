"""Acceso a las colecciones del dominio.

Las implementaciones de las acciones no hablan con Firestore: hablan con esta
clase. La razón práctica es que si mañana cambia el backend, cambia acá y nada
más. La razón que importa más es otra: concentrar las consultas en un módulo
hace posible saber **qué índices compuestos hacen falta** leyendo un archivo, en
lugar de descubrirlo cuando Firestore rechaza una consulta en producción.

Los índices que necesitan estas consultas ya están declarados en
`firestore.indexes.json`. Una consulta nueva con otra combinación de filtros
necesita su índice ahí también, o falla al ejecutarse.

## Sobre los ids de documento

`scripts/seed.py` guarda cada documento con la **clave natural** que declara la
ontología —`tag` para un activo, `id_ot` para una orden— como id del documento.
Por eso buscar un activo por TAG es un `get` directo y no una consulta: es la
diferencia entre una lectura y un escaneo con índice.
"""

from __future__ import annotations

from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from synapseflow.persistence.client import Collections, get_client

# Tope duro de resultados por consulta. El modelo paga cada fila en ventana de
# contexto, y una lista de doscientos activos no es más útil que una de veinte:
# es más cara y más difícil de leer.
LIMITE_MAXIMO = 50
LIMITE_POR_DEFECTO = 20


class RepositorioDominio:
    """Acceso tipado a las colecciones del dominio.

    No cachea nada. El cliente de Firestore ya mantiene el pool de conexiones y
    los datos del dominio cambian: un activo cuya criticidad se reclasificó tiene
    que leerse actualizado en el turno siguiente de la misma conversación.
    """

    def __init__(self, cliente: Any = None) -> None:
        # Inyectable para los tests; por defecto, el cliente compartido del
        # proceso (ver persistence/client.py).
        self._cliente = cliente or get_client()

    # ── Activos ──────────────────────────────────────────────────────────────

    async def activo_por_tag(self, tag: str) -> dict[str, Any] | None:
        """Ficha de un activo, o `None` si el TAG no existe.

        Devolver `None` y no lanzar es deliberado: que un TAG no exista es una
        respuesta legítima a una pregunta del usuario —se equivocó al tipearlo—
        y la acción tiene que poder decirlo en lenguaje natural en lugar de
        propagar una excepción hasta el agente.
        """
        documento = await self._cliente.collection(Collections.ASSETS).document(tag).get()
        return documento.to_dict() if documento.exists else None

    async def listar_activos(
        self,
        *,
        instalacion: str | None = None,
        clase: str | None = None,
        criticidad: str | None = None,
        estado: str | None = None,
        limite: int = LIMITE_POR_DEFECTO,
    ) -> list[dict[str, Any]]:
        """Activos que cumplen todos los filtros dados.

        Los filtros se aplican en Firestore y no en Python. Traer la colección
        entera para filtrarla acá funcionaría con los 60 activos sintéticos y
        dejaría de funcionar con los de una operación real, que es exactamente la
        clase de error que no aparece en los tests.
        """
        consulta: Any = self._cliente.collection(Collections.ASSETS)

        for campo, valor in (
            ("instalacion", instalacion),
            ("clase", clase),
            ("criticidad", criticidad),
            ("estado", estado),
        ):
            if valor is not None:
                consulta = consulta.where(filter=FieldFilter(campo, "==", valor))

        return await _recolectar(consulta.limit(_acotar(limite)))

    async def actualizar_activo(self, tag: str, cambios: dict[str, Any]) -> None:
        """Aplica cambios parciales sobre un activo existente."""
        await self._cliente.collection(Collections.ASSETS).document(tag).update(cambios)

    # ── Inspecciones ─────────────────────────────────────────────────────────

    async def inspecciones_de(
        self, tag: str, *, limite: int = LIMITE_POR_DEFECTO
    ) -> list[dict[str, Any]]:
        """Inspecciones de un activo, de la más reciente a la más antigua.

        El orden importa más de lo que parece: el cálculo de velocidad de
        corrosión de F2.3 se apoya en la secuencia temporal, y una lista
        desordenada produciría una velocidad con el signo invertido sin que nada
        falle.
        """
        return await _recolectar(
            self._cliente.collection(Collections.INSPECTIONS)
            .where(filter=FieldFilter("activo", "==", tag))
            .order_by("fecha", direction="DESCENDING")
            .limit(_acotar(limite))
        )

    async def inspeccion_por_id(self, id_inspeccion: str) -> dict[str, Any] | None:
        documento = (
            await self._cliente.collection(Collections.INSPECTIONS).document(id_inspeccion).get()
        )
        return documento.to_dict() if documento.exists else None

    # ── Órdenes de trabajo ───────────────────────────────────────────────────

    async def guardar_orden(self, orden: dict[str, Any]) -> str:
        """Escribe una orden y devuelve su id.

        El id es la clave natural `id_ot`, no uno generado por Firestore: así una
        reescritura de la misma orden —un reintento tras un timeout— sobreescribe
        en lugar de duplicar. Una orden de trabajo duplicada moviliza una
        cuadrilla dos veces.
        """
        id_ot = str(orden["id_ot"])
        await self._cliente.collection(Collections.WORK_ORDERS).document(id_ot).set(orden)
        return id_ot

    async def orden_por_id(self, id_ot: str) -> dict[str, Any] | None:
        documento = await self._cliente.collection(Collections.WORK_ORDERS).document(id_ot).get()
        return documento.to_dict() if documento.exists else None

    async def actualizar_orden(self, id_ot: str, cambios: dict[str, Any]) -> None:
        await self._cliente.collection(Collections.WORK_ORDERS).document(id_ot).update(cambios)

    async def ordenes_de(
        self, tag: str, *, limite: int = LIMITE_POR_DEFECTO
    ) -> list[dict[str, Any]]:
        return await _recolectar(
            self._cliente.collection(Collections.WORK_ORDERS)
            .where(filter=FieldFilter("activo", "==", tag))
            .limit(_acotar(limite))
        )


async def _recolectar(consulta: Any) -> list[dict[str, Any]]:
    """Materializa una consulta, descartando documentos sin cuerpo.

    `to_dict()` devuelve `None` para un snapshot que no existe. En el resultado
    de un `stream()` no debería pasar nunca, pero filtrarlo cuesta una línea y
    evita que un `None` se cuele hasta una implementación de acción, donde
    fallaría con un `TypeError` sin relación aparente con su causa.
    """
    return [datos async for doc in consulta.stream() if (datos := doc.to_dict()) is not None]


def _acotar(limite: int) -> int:
    """Encierra el límite pedido entre 1 y el tope duro.

    El límite llega desde un parámetro de herramienta, o sea que lo elige el
    modelo. Un `limite=10000` alucinado no puede convertirse en una lectura de
    diez mil documentos facturados.
    """
    return max(1, min(limite, LIMITE_MAXIMO))
