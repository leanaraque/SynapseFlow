"""Contrato del verificador de fundamento. Cierra el compromiso 4.

Los tres veredictos se ejercitan con `FakeChatModel`, así que no hay red ni
cuota: el dictamen del modelo se programa y lo que se verifica es **qué hace el
sistema con él**, que es la parte que el proyecto se compromete a sostener.

La propiedad que más importa está en la capa determinística: una cita inventada
rechaza la respuesta **sin llamar al modelo**. Es más rápido, más barato y más
confiable que un juicio, y es el modo de falla más común.

Ver docs/plan/fases/F3-rag.md § F3.4
"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from synapseflow.config import Provider, Settings
from synapseflow.llm.fake import FakeChatModel
from synapseflow.llm.gateway import Gateway
from synapseflow.rag.fundamento import (
    SIN_FUNDAMENTO,
    Afirmacion,
    Dictamen,
    Resultado,
    VerificadorDeFundamento,
)


def fragmento(doc_id: str, seccion: str, texto: str = "contenido normativo") -> Document:
    return Document(
        page_content=texto,
        metadata={"doc_id": doc_id, "seccion": seccion, "vigencia": "vigente"},
    )


RECUPERADOS = [fragmento("API-570-2016", "7.4"), fragmento("API-570-2016", "5.6")]

RESPUESTA = (
    "El activo no está apto para continuar en servicio: el espesor medido "
    "(6,8 mm) está por debajo del mínimo requerido (7,1 mm) [API-570-2016 §7.4]."
)


def verificador(*dictamenes: Dictamen) -> VerificadorDeFundamento:
    """Verificador con el dictamen del modelo ya programado."""
    falso = FakeChatModel(estructurados=list(dictamenes))
    gateway = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)
    return VerificadorDeFundamento(gateway)


def dictamen(*pares: tuple[str, bool]) -> Dictamen:
    return Dictamen(
        afirmaciones=[
            Afirmacion(texto=texto, respaldada=ok, fragmento="API-570-2016 §7.4" if ok else None)
            for texto, ok in pares
        ]
    )


# ─────────────────────────────────────────────────────────────────────────────
# La capa determinística, que corre primero
# ─────────────────────────────────────────────────────────────────────────────


async def test_una_cita_inventada_rechaza_sin_llamar_al_modelo() -> None:
    """El modo de falla más común se detecta gratis.

    Preguntarle primero al modelo costaría una llamada en el caso que la capa
    determinística resuelve sin ninguna.
    """
    falso = FakeChatModel(estructurados=[])
    gateway = Gateway(settings=Settings(SYNAPSEFLOW_PROVIDER=Provider.FAKE), falso=falso)

    veredicto = await VerificadorDeFundamento(gateway).verificar(
        "Corresponde el reemplazo inmediato [API-570-2016 §12.9].", RECUPERADOS
    )

    assert veredicto.resultado is Resultado.SIN_FUNDAMENTO
    assert falso.llamadas == 0, "se llamó al modelo para algo que ya estaba decidido"


async def test_sin_material_recuperado_no_se_emite() -> None:
    """El caso del agente que responde sin haber buscado."""
    veredicto = await verificador().verificar(RESPUESTA, [])

    assert veredicto.resultado is Resultado.SIN_FUNDAMENTO
    assert "No se recuperó ningún fragmento" in veredicto.explicacion


async def test_una_respuesta_sin_citas_no_se_emite() -> None:
    """Es literalmente el compromiso 4."""
    veredicto = await verificador().verificar(
        "El activo no está apto para continuar en servicio.", RECUPERADOS
    )

    assert veredicto.resultado is Resultado.SIN_FUNDAMENTO
    assert "no cita ninguna fuente" in veredicto.explicacion


# ─────────────────────────────────────────────────────────────────────────────
# Los tres veredictos
# ─────────────────────────────────────────────────────────────────────────────


async def test_veredicto_fundamentada_emite_la_respuesta() -> None:
    veredicto = await verificador(
        dictamen(("El espesor está por debajo del mínimo", True))
    ).verificar(RESPUESTA, RECUPERADOS)

    assert veredicto.resultado is Resultado.FUNDAMENTADA
    assert veredicto.se_emite is True
    assert veredicto.texto_emitible == RESPUESTA


async def test_veredicto_parcial_emite_marcando_lo_que_no_tiene_respaldo() -> None:
    """`parcial` es lo que hace utilizable al sistema.

    Con solo «emite / no emite», una respuesta de tres afirmaciones donde dos
    están respaldadas se descartaría entera y el usuario recibiría «no sé» sobre
    algo que el sistema sí sabía.
    """
    veredicto = await verificador(
        dictamen(
            ("El espesor está por debajo del mínimo", True),
            ("Corresponde parada inmediata de la instalación", False),
        )
    ).verificar(RESPUESTA, RECUPERADOS)

    assert veredicto.resultado is Resultado.PARCIAL
    assert veredicto.se_emite is True
    assert RESPUESTA in veredicto.texto_emitible
    assert "NO está respaldado" in veredicto.texto_emitible
    assert "parada inmediata" in veredicto.texto_emitible


async def test_la_afirmacion_sin_respaldo_no_se_borra_de_la_respuesta() -> None:
    """Quitarla dejaría un texto que parece completo y no lo es.

    El ingeniero no tendría cómo saber qué se omitió.
    """
    veredicto = await verificador(
        dictamen(("respaldada", True), ("Corresponde parada inmediata", False))
    ).verificar(RESPUESTA, RECUPERADOS)

    assert veredicto.texto_emitible.startswith(RESPUESTA)


async def test_veredicto_sin_fundamento_no_emite_la_respuesta_original() -> None:
    """Devolver ambas invitaría a que un call site emita la equivocada."""
    veredicto = await verificador(
        dictamen(("El activo debe pararse ya", False), ("Y reemplazarse", False))
    ).verificar(RESPUESTA, RECUPERADOS)

    assert veredicto.resultado is Resultado.SIN_FUNDAMENTO
    assert veredicto.se_emite is False
    assert veredicto.texto_emitible == SIN_FUNDAMENTO
    assert RESPUESTA not in veredicto.texto_emitible


async def test_la_negativa_es_texto_fijo_y_no_generado() -> None:
    """Pedirle al modelo que redacte su propia negativa lo deja improvisando
    justo cuando acabamos de establecer que no tiene con qué."""
    veredicto = await verificador().verificar("Sin citas.", RECUPERADOS)
    assert veredicto.texto_emitible == SIN_FUNDAMENTO


# ─────────────────────────────────────────────────────────────────────────────
# Detalle del dictamen
# ─────────────────────────────────────────────────────────────────────────────


async def test_las_afirmaciones_sin_respaldo_quedan_accesibles() -> None:
    """El log de auditoría necesita saber qué se marcó y por qué."""
    veredicto = await verificador(dictamen(("a", True), ("b", False), ("c", False))).verificar(
        RESPUESTA, RECUPERADOS
    )

    assert {a.texto for a in veredicto.sin_respaldo} == {"b", "c"}
    assert len(veredicto.afirmaciones) == 3


async def test_un_dictamen_vacio_no_se_lee_como_todo_bien() -> None:
    """Que el verificador no encuentre nada que evaluar no es aprobación.

    Se emite —las citas ya se validaron— pero queda como parcial para que la
    métrica de F8 lo separe de una verificación que sí dictaminó.
    """
    veredicto = await verificador(Dictamen(afirmaciones=[])).verificar(RESPUESTA, RECUPERADOS)

    assert veredicto.resultado is Resultado.PARCIAL
    assert "no identificó afirmaciones" in veredicto.explicacion


async def test_el_veredicto_conserva_la_validacion_de_citas() -> None:
    """Para que el log de auditoría no tenga que recalcularla."""
    veredicto = await verificador(dictamen(("a", True))).verificar(RESPUESTA, RECUPERADOS)

    assert [str(c) for c in veredicto.citas.respaldadas] == ["API-570-2016 §7.4"]
    assert [str(c) for c in veredicto.citas.sin_citar] == ["API-570-2016 §5.6"]


@pytest.mark.parametrize(
    ("dictamenes", "esperado"),
    [
        ((("a", True), ("b", True)), Resultado.FUNDAMENTADA),
        ((("a", True), ("b", False)), Resultado.PARCIAL),
        ((("a", False), ("b", False)), Resultado.SIN_FUNDAMENTO),
    ],
)
async def test_la_tabla_de_veredictos(
    dictamenes: tuple[tuple[str, bool], ...], esperado: Resultado
) -> None:
    """Todas respaldadas → emite. Algunas → emite marcando. Ninguna → no emite."""
    veredicto = await verificador(dictamen(*dictamenes)).verificar(RESPUESTA, RECUPERADOS)
    assert veredicto.resultado is esperado
