"""Verificador de fundamento. **Cierra el compromiso 4: sin cita no hay respuesta.**

Un modelo puede producir una respuesta que suene técnica y correcta sin que
ningún fragmento recuperado la respalde. En un dominio donde la respuesta puede
terminar en una parada de planta, eso no se puede emitir.

## Dos capas, y la barata primero

1. **Determinística.** `citas.validar_citas` contrasta cada referencia contra el
   material recuperado. Una cita a algo que el sistema no leyó invalida la
   respuesta sin necesidad de preguntarle a nadie: es más rápido, más barato y
   más confiable que un juicio del modelo.
2. **Con modelo.** Solo si las citas pasan. El perfil `verifier` recibe la
   respuesta y los fragmentos, y dictamina por afirmación si está respaldada.

El orden importa. Preguntarle primero al modelo cuesta una llamada en el caso más
común de falla —la cita inventada— que la capa determinística detecta gratis.

## Por qué el veredicto tiene tres valores y no dos

`parcial` es el que hace utilizable al sistema. Con solo «emite / no emite», una
respuesta de cinco afirmaciones donde cuatro están respaldadas se descarta
entera, y el usuario recibe «no sé» sobre algo que el sistema sí sabía. Con
`parcial` se emite marcando qué parte no tiene respaldo, que es lo que un
ingeniero necesita para decidir dónde mirar.

Ver docs/plan/fases/F3-rag.md § F3.4
"""

from __future__ import annotations

from enum import StrEnum

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from synapseflow.llm.gateway import Gateway
from synapseflow.rag.citas import ResultadoValidacion, validar_texto


class Resultado(StrEnum):
    """Qué hace el sistema con la respuesta."""

    FUNDAMENTADA = "fundamentada"
    PARCIAL = "parcial"
    SIN_FUNDAMENTO = "sin_fundamento"


# Lo que se le responde al usuario cuando no hay con qué fundamentar. Es texto
# fijo y no generado: pedirle al modelo que redacte su propia negativa lo deja
# improvisando justo cuando acabamos de establecer que no tiene con qué.
SIN_FUNDAMENTO = (
    "No encontré fundamento en la normativa disponible para responder eso. "
    "Puedo decir qué documentos consulté, pero no voy a afirmar algo que las "
    "fuentes recuperadas no respaldan."
)

INSTRUCCION = """Sos un verificador de fundamento documental. NO respondés la \
pregunta del usuario: evaluás si una respuesta ya redactada está respaldada por \
los fragmentos de normativa que se recuperaron.

Reglas:
- Una afirmación está respaldada solo si algún fragmento la sostiene de forma \
explícita. Que sea plausible, o que coincida con tu conocimiento general, no \
cuenta.
- Los datos operativos del activo —espesores, fechas, resultados de cálculo— no \
necesitan respaldo normativo: vienen de las herramientas. Marcalos respaldados.
- Ante la duda, marcá la afirmación como NO respaldada. Un falso negativo hace \
que el sistema pida más contexto; un falso positivo emite algo sin fundamento."""


class Afirmacion(BaseModel):
    """Una proposición de la respuesta, con su dictamen."""

    model_config = ConfigDict(frozen=True)

    texto: str = Field(description="La afirmación, citada textualmente de la respuesta.")
    respaldada: bool = Field(description="Si algún fragmento recuperado la sostiene.")
    fragmento: str | None = Field(
        default=None, description="doc_id y sección del fragmento que la respalda."
    )
    motivo: str = Field(default="", description="Por qué se marcó así, en una línea.")


class Dictamen(BaseModel):
    """Salida estructurada que se le pide al modelo verificador."""

    afirmaciones: list[Afirmacion] = Field(
        default_factory=list, description="Una entrada por afirmación de la respuesta."
    )


class Veredicto(BaseModel):
    """Resultado de la verificación, y qué se puede emitir."""

    model_config = ConfigDict(frozen=True)

    resultado: Resultado
    explicacion: str
    # Texto que corresponde emitir. En `sin_fundamento` es la negativa, no la
    # respuesta original: devolver ambas invita a que un call site emita la
    # equivocada.
    texto_emitible: str
    citas: ResultadoValidacion
    afirmaciones: tuple[Afirmacion, ...] = ()

    @property
    def se_emite(self) -> bool:
        return self.resultado is not Resultado.SIN_FUNDAMENTO

    @property
    def sin_respaldo(self) -> tuple[Afirmacion, ...]:
        return tuple(a for a in self.afirmaciones if not a.respaldada)


class VerificadorDeFundamento:
    """Comprueba que cada afirmación tenga respaldo en el contexto recuperado."""

    def __init__(self, gateway: Gateway | None = None) -> None:
        self._gateway = gateway or Gateway()

    async def verificar(self, respuesta: str, recuperados: list[Document]) -> Veredicto:
        """Dictamina si una respuesta redactada se puede emitir.

        Args:
            respuesta: el texto que el agente redactó.
            recuperados: los fragmentos que tuvo delante al redactarlo.
        """
        citas = validar_texto(respuesta, recuperados)

        # ── Capa 1: determinística ───────────────────────────────────────────
        if not recuperados:
            return self._rechazar(
                citas, "No se recuperó ningún fragmento de normativa sobre el que fundamentar."
            )

        if citas.inventadas:
            return self._rechazar(citas, citas.explicacion())

        if not citas.hay_citas:
            return self._rechazar(
                citas,
                "La respuesta no cita ninguna fuente. En este dominio una "
                "afirmación normativa sin documento y sección no se puede auditar.",
            )

        # ── Capa 2: juicio del modelo ────────────────────────────────────────
        dictamen = await self._dictaminar(respuesta, recuperados)

        if not dictamen.afirmaciones:
            # El verificador no encontró nada que evaluar. No es lo mismo que
            # «todo bien»: las citas ya se validaron, así que se emite, pero el
            # resultado queda como parcial para que la métrica de F8 lo separe.
            return Veredicto(
                resultado=Resultado.PARCIAL,
                explicacion="El verificador no identificó afirmaciones evaluables.",
                texto_emitible=respuesta,
                citas=citas,
            )

        sin_respaldo = [a for a in dictamen.afirmaciones if not a.respaldada]

        if not sin_respaldo:
            return Veredicto(
                resultado=Resultado.FUNDAMENTADA,
                explicacion=(
                    f"Las {len(dictamen.afirmaciones)} afirmaciones están respaldadas "
                    f"por los fragmentos recuperados."
                ),
                texto_emitible=respuesta,
                citas=citas,
                afirmaciones=tuple(dictamen.afirmaciones),
            )

        if len(sin_respaldo) == len(dictamen.afirmaciones):
            return self._rechazar(
                citas,
                "Ninguna afirmación de la respuesta está respaldada por los "
                "fragmentos recuperados.",
                afirmaciones=tuple(dictamen.afirmaciones),
            )

        return Veredicto(
            resultado=Resultado.PARCIAL,
            explicacion=(
                f"{len(sin_respaldo)} de {len(dictamen.afirmaciones)} afirmaciones no "
                "tienen respaldo en las fuentes recuperadas."
            ),
            texto_emitible=_con_advertencia(respuesta, sin_respaldo),
            citas=citas,
            afirmaciones=tuple(dictamen.afirmaciones),
        )

    async def _dictaminar(self, respuesta: str, recuperados: list[Document]) -> Dictamen:
        """Le pide al perfil `verifier` un dictamen por afirmación."""
        estructurado = self._gateway.estructurado("verifier", Dictamen)

        fragmentos = "\n\n".join(
            f"[{d.metadata.get('doc_id')} §{d.metadata.get('seccion')}]\n{d.page_content}"
            for d in recuperados
        )

        dictamen = await estructurado.ainvoke(
            [
                SystemMessage(content=INSTRUCCION),
                HumanMessage(
                    content=(
                        f"FRAGMENTOS RECUPERADOS:\n{fragmentos}\n\n"
                        f"RESPUESTA A VERIFICAR:\n{respuesta}"
                    )
                ),
            ]
        )
        return dictamen if isinstance(dictamen, Dictamen) else Dictamen.model_validate(dictamen)

    @staticmethod
    def _rechazar(
        citas: ResultadoValidacion, explicacion: str, afirmaciones: tuple[Afirmacion, ...] = ()
    ) -> Veredicto:
        return Veredicto(
            resultado=Resultado.SIN_FUNDAMENTO,
            explicacion=explicacion,
            texto_emitible=SIN_FUNDAMENTO,
            citas=citas,
            afirmaciones=afirmaciones,
        )


def _con_advertencia(respuesta: str, sin_respaldo: list[Afirmacion]) -> str:
    """Marca en la respuesta qué parte no tiene respaldo.

    Se anexa al final y no se borra la afirmación: quitarla dejaría un texto que
    parece completo y no lo es, y el ingeniero no tendría cómo saber qué se
    omitió.
    """
    listado = "\n".join(f"  · {a.texto}" for a in sin_respaldo)
    return (
        f"{respuesta}\n\n"
        f"⚠ Lo siguiente NO está respaldado por la normativa recuperada y "
        f"requiere verificación:\n{listado}"
    )
