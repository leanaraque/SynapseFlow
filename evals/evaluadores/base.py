"""Tipos comunes de los evaluadores.

## Por qué el puntaje va de 0 a 1 y además hay un booleano

El puntaje sirve para agregar —promediar por suite, comparar contra la línea
base— y el booleano para el reporte por caso. Tener solo el promedio es el error
clásico de una suite de evals: saber que la fidelidad bajó de 0,91 a 0,87 no
sirve; saber **qué tres casos se rompieron**, sí.

Ver docs/plan/fases/F8-evals.md § F8.2
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Caso(BaseModel):
    """Un caso del golden dataset."""

    model_config = ConfigDict(frozen=True, extra="allow")

    id: str
    pregunta: str
    respuesta_esperada: str
    debe_rechazar: bool = False
    fuentes: tuple[str, ...] = ()
    # Citas que el sistema NO debe producir. Se usan para el documento derogado.
    prohibidas: tuple[str, ...] = ()
    # Cadenas que no pueden aparecer en la respuesta: legajos, por ejemplo.
    no_debe_contener: tuple[str, ...] = ()
    valor_esperado: dict[str, Any] | None = None
    espera_herramienta: str | None = None
    nota: str = ""


class Fragmento(BaseModel):
    """Un fragmento que el retriever devolvió, con su texto.

    Lleva el contenido y no solo la cita porque el evaluador de fidelidad juzga
    si cada afirmación está respaldada, y para eso necesita leer el fragmento.
    Con solo `DOC-ID §sección`, el juez estaría dictaminando sobre una etiqueta.
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str
    seccion: str
    contenido: str = ""

    @property
    def cita(self) -> str:
        return f"{self.doc_id} §{self.seccion}"


class RespuestaDelSistema(BaseModel):
    """Lo que el grafo produjo para un caso.

    Es lo que el corredor arma a partir del estado final. Se define como tipo y
    no como dict para que un evaluador nuevo no tenga que adivinar qué campos
    hay ni tolerar que falten.
    """

    model_config = ConfigDict(frozen=True)

    texto: str = ""
    citas: tuple[str, ...] = ()
    calculos: dict[str, Any] = Field(default_factory=dict)
    herramientas: tuple[str, ...] = ()
    veredicto: str | None = None
    fragmentos: tuple[Fragmento, ...] = ()

    @property
    def recuperados(self) -> tuple[str, ...]:
        """Las citas que los fragmentos recuperados hacen posibles."""
        return tuple(f.cita for f in self.fragmentos)

    @property
    def se_nego(self) -> bool:
        """Si el sistema declaró que no tenía fundamento para responder."""
        return self.veredicto == "sin_fundamento"


class Resultado(BaseModel):
    """El dictamen de un evaluador sobre un caso."""

    model_config = ConfigDict(frozen=True)

    caso_id: str
    metrica: str
    puntaje: float = Field(ge=0.0, le=1.0)
    aprobado: bool
    detalle: str = ""

    @classmethod
    def aprueba(cls, caso_id: str, metrica: str, detalle: str = "") -> Resultado:
        return cls(caso_id=caso_id, metrica=metrica, puntaje=1.0, aprobado=True, detalle=detalle)

    @classmethod
    def falla(cls, caso_id: str, metrica: str, detalle: str, puntaje: float = 0.0) -> Resultado:
        return cls(
            caso_id=caso_id, metrica=metrica, puntaje=puntaje, aprobado=False, detalle=detalle
        )

    @classmethod
    def no_aplica(cls, caso_id: str, metrica: str) -> Resultado | None:
        """Un evaluador que no corresponde a este caso devuelve `None`.

        Devolver un aprobado con puntaje 1 inflaría la métrica con casos que
        nunca se midieron, que es la forma más fácil de tener una suite que dice
        0,98 sin haber verificado nada.
        """
        return None
