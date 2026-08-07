"""Extracción y validación de citas normativas.

## Qué problema resuelve

Un modelo puede escribir «API 570 §12.9 exige el reemplazo inmediato» con total
aplomo cuando la sección 12.9 no existe, o cuando existe pero el sistema nunca la
recuperó. Eso es una **alucinación con formato de rigor**, y es peor que no citar:
la cita es exactamente la señal que hace que un revisor no verifique.

`validar_citas` responde una sola pregunta, y es la que importa: *¿cada cita
corresponde a un fragmento que efectivamente se recuperó?* No valida que la
afirmación sea correcta —eso es el verificador de fundamento, F3.4— sino que la
referencia exista en el material que el modelo tuvo delante.

## Por qué la validación es sobre lo recuperado y no sobre el corpus

Podría contrastarse contra el corpus entero: la cita existiría si el documento y
la sección existen. Sería más permisivo y más falso. Un modelo que cita una
sección real que **no estaba en su contexto** no la leyó: la recordó de su
entrenamiento, o la infirió del número. En un dominio donde la respuesta puede
terminar en una parada de planta, esa distinción es toda la diferencia.

Ver docs/plan/fases/F3-rag.md § F3.3
"""

from __future__ import annotations

import re

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict

# Formatos que se aceptan como cita, en orden de preferencia:
#
#   [API-570-2016 §7.4]      el que emite el sistema
#   API-570-2016 §7.4        el mismo, sin corchetes
#   API-570-2016 sección 7.4 el que produce un modelo escribiendo en prosa
#
# Se admiten los tres porque el texto lo redacta un LLM: exigir uno solo haría
# que una cita legítima se contara como ausente, y el sistema se negaría a
# responder por un problema de formato. La versión canónica la emite el
# renderizador de F5.
CITA = re.compile(
    r"(?P<doc_id>[A-Z][\w\-]*\d[\w\-]*)\s*(?:§|secci[óo]n\s+|sec\.\s*)\s*(?P<seccion>\d+(?:\.\d+)*)",
    re.IGNORECASE,
)


class Cita(BaseModel):
    """Una referencia a documento y sección."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    seccion: str

    def __str__(self) -> str:
        return f"{self.doc_id} §{self.seccion}"


class ResultadoValidacion(BaseModel):
    """Qué citas del texto están respaldadas por el material recuperado."""

    model_config = ConfigDict(frozen=True)

    respaldadas: tuple[Cita, ...] = ()
    # Citas a documentos o secciones que el sistema no recuperó. Son el modo de
    # falla que este módulo existe para detectar.
    inventadas: tuple[Cita, ...] = ()
    # Fragmentos que se recuperaron y el texto no citó. No es un error —no todo
    # lo recuperado es pertinente— pero alimenta la métrica de precisión de F8.
    sin_citar: tuple[Cita, ...] = ()

    @property
    def hay_citas(self) -> bool:
        return bool(self.respaldadas or self.inventadas)

    @property
    def todas_respaldadas(self) -> bool:
        """Verdadero solo si hay al menos una cita y ninguna es inventada.

        Que no haya ninguna cita **no** cuenta como válido: una respuesta
        normativa sin citas es justamente lo que el compromiso 4 impide.
        """
        return bool(self.respaldadas) and not self.inventadas

    def explicacion(self) -> str:
        """Texto para el log de auditoría y para el mensaje de rechazo."""
        if not self.hay_citas:
            return "La respuesta no cita ninguna fuente normativa."
        if self.inventadas:
            listado = ", ".join(str(c) for c in self.inventadas)
            return (
                f"La respuesta cita fuentes que el sistema no recuperó: {listado}. "
                "Una cita a material que no se leyó no es una fuente: es una "
                "afirmación con formato de referencia."
            )
        return f"Las {len(self.respaldadas)} cita(s) corresponden a fragmentos recuperados."


def extraer_citas(texto: str) -> list[Cita]:
    """Citas presentes en un texto, sin repetir y en orden de aparición.

    El orden se conserva porque el log de auditoría reconstruye el razonamiento,
    y ahí importa qué fundamento se invocó primero.
    """
    vistas: dict[tuple[str, str], Cita] = {}

    for coincidencia in CITA.finditer(texto):
        # El doc_id se normaliza a mayúsculas porque el corpus los declara así y
        # un modelo escribe indistintamente `api-570-2016` o `API-570-2016`.
        clave = (coincidencia.group("doc_id").upper(), coincidencia.group("seccion"))
        if clave not in vistas:
            vistas[clave] = Cita(doc_id=clave[0], seccion=clave[1])

    return list(vistas.values())


def citas_de(recuperados: list[Document]) -> list[Cita]:
    """Las citas que los fragmentos recuperados hacen posibles."""
    vistas: dict[tuple[str, str], Cita] = {}

    for documento in recuperados:
        doc_id = str(documento.metadata.get("doc_id") or "").upper()
        seccion = str(documento.metadata.get("seccion") or "")
        if not doc_id or not seccion:
            # La ingesta se niega a producir fragmentos sin estos campos
            # (`IngestaError`). Si aparece uno acá, entró por otro camino y no se
            # puede citar: descartarlo es más seguro que respaldar con él.
            continue
        vistas.setdefault((doc_id, seccion), Cita(doc_id=doc_id, seccion=seccion))

    return list(vistas.values())


def validar_citas(citas: list[Cita], recuperados: list[Document]) -> ResultadoValidacion:
    """Contrasta las citas de un texto contra el material que se recuperó.

    Args:
        citas: lo que el modelo citó, normalmente vía `extraer_citas`.
        recuperados: los fragmentos que el retriever devolvió para esa consulta.
    """
    disponibles = {(c.doc_id, c.seccion): c for c in citas_de(recuperados)}
    citadas = {(c.doc_id, c.seccion) for c in citas}

    respaldadas = tuple(c for c in citas if (c.doc_id, c.seccion) in disponibles)
    inventadas = tuple(c for c in citas if (c.doc_id, c.seccion) not in disponibles)
    sin_citar = tuple(c for clave, c in disponibles.items() if clave not in citadas)

    return ResultadoValidacion(respaldadas=respaldadas, inventadas=inventadas, sin_citar=sin_citar)


def validar_texto(texto: str, recuperados: list[Document]) -> ResultadoValidacion:
    """`extraer_citas` + `validar_citas`, que es como se usa casi siempre."""
    return validar_citas(extraer_citas(texto), recuperados)
