"""Contrato de la validación de citas.

El test que da sentido al módulo es el de la **cita inventada**: un modelo que
escribe «API 570 §12.9 exige el reemplazo inmediato» con total aplomo sobre una
sección que el sistema nunca recuperó. Eso es una alucinación con formato de
rigor, y la cita es exactamente la señal que hace que un revisor no verifique.

Ver docs/plan/fases/F3-rag.md § F3.3
"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from synapseflow.rag.citas import (
    Cita,
    citas_de,
    extraer_citas,
    validar_citas,
    validar_texto,
)


def fragmento(doc_id: str, seccion: str, texto: str = "contenido") -> Document:
    return Document(
        page_content=texto,
        metadata={"doc_id": doc_id, "seccion": seccion, "vigencia": "vigente"},
    )


RECUPERADOS = [
    fragmento("API-570-2016", "7.4"),
    fragmento("API-570-2016", "5.6"),
    fragmento("PROC-INT-014", "3.2"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Extracción
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "texto",
    [
        "Según [API-570-2016 §7.4] corresponde retirarlo.",
        "Según API-570-2016 §7.4 corresponde retirarlo.",
        "Según API-570-2016 sección 7.4 corresponde retirarlo.",
        "Según API-570-2016 Sección 7.4 corresponde retirarlo.",
    ],
)
def test_se_reconocen_los_formatos_que_produce_un_modelo(texto: str) -> None:
    """El texto lo redacta un LLM, no un renderizador.

    Exigir un solo formato haría que una cita legítima se contara como ausente y
    el sistema se negara a responder por un problema de tipografía.
    """
    assert extraer_citas(texto) == [Cita(doc_id="API-570-2016", seccion="7.4")]


def test_las_citas_no_se_repiten_y_conservan_el_orden() -> None:
    """El log de auditoría reconstruye el razonamiento: importa qué se invocó primero."""
    texto = "[PROC-INT-014 §3.2] y [API-570-2016 §7.4], y de nuevo [PROC-INT-014 §3.2]."

    assert [str(c) for c in extraer_citas(texto)] == ["PROC-INT-014 §3.2", "API-570-2016 §7.4"]


def test_el_doc_id_se_normaliza_a_mayusculas() -> None:
    """Un modelo escribe indistintamente `api-570-2016` y `API-570-2016`."""
    assert extraer_citas("ver api-570-2016 §7.4")[0].doc_id == "API-570-2016"


def test_una_seccion_con_varios_niveles_se_extrae_entera() -> None:
    assert extraer_citas("[PROC-INT-014 §3.2.1]")[0].seccion == "3.2.1"


def test_un_texto_sin_citas_no_produce_ninguna() -> None:
    assert extraer_citas("El activo no está apto para continuar en servicio.") == []


def test_no_se_confunde_una_medicion_con_una_cita() -> None:
    """«6,8 mm» y «7.1 mm» no son referencias normativas."""
    assert extraer_citas("El espesor medido es 6.8 mm contra un mínimo de 7.1 mm.") == []


# ─────────────────────────────────────────────────────────────────────────────
# Validación · el test que da sentido al módulo
# ─────────────────────────────────────────────────────────────────────────────


def test_una_cita_inventada_se_detecta() -> None:
    """La sección 12.9 no está entre lo recuperado.

    Puede incluso existir en el corpus: da igual. Si no estaba en el contexto, el
    modelo no la leyó — la recordó de su entrenamiento o la infirió del número.
    """
    resultado = validar_texto("API-570-2016 §12.9 exige el reemplazo inmediato.", RECUPERADOS)

    assert [str(c) for c in resultado.inventadas] == ["API-570-2016 §12.9"]
    assert not resultado.respaldadas
    assert resultado.todas_respaldadas is False
    assert "no recuperó" in resultado.explicacion()


def test_una_cita_a_un_documento_que_no_se_recupero_se_detecta() -> None:
    """No solo la sección: el documento entero puede ser inventado."""
    resultado = validar_texto("Según API-653-2018 §4.4 corresponde reparar.", RECUPERADOS)

    assert [str(c) for c in resultado.inventadas] == ["API-653-2018 §4.4"]


def test_una_cita_respaldada_se_acepta() -> None:
    resultado = validar_texto(
        "El espesor está por debajo del mínimo: [API-570-2016 §7.4].", RECUPERADOS
    )

    assert [str(c) for c in resultado.respaldadas] == ["API-570-2016 §7.4"]
    assert not resultado.inventadas
    assert resultado.todas_respaldadas is True


def test_una_respuesta_sin_citas_no_cuenta_como_valida() -> None:
    """Es justamente lo que el compromiso 4 impide.

    `todas_respaldadas` en verdadero sobre una respuesta sin citas dejaría pasar
    exactamente el caso que el proyecto se compromete a bloquear.
    """
    resultado = validar_texto("El activo no está apto para continuar en servicio.", RECUPERADOS)

    assert resultado.hay_citas is False
    assert resultado.todas_respaldadas is False
    assert "no cita ninguna fuente" in resultado.explicacion()


def test_una_cita_respaldada_y_otra_inventada_no_se_compensan() -> None:
    """Basta una inventada para que la respuesta no sea emitible tal cual."""
    resultado = validar_texto(
        "Ver [API-570-2016 §7.4] y también [API-570-2016 §12.9].", RECUPERADOS
    )

    assert len(resultado.respaldadas) == 1
    assert len(resultado.inventadas) == 1
    assert resultado.todas_respaldadas is False


def test_lo_recuperado_y_no_citado_se_reporta_sin_ser_un_error() -> None:
    """No todo lo recuperado es pertinente. Alimenta la métrica de F8."""
    resultado = validar_texto("Ver [API-570-2016 §7.4].", RECUPERADOS)

    assert {str(c) for c in resultado.sin_citar} == {"API-570-2016 §5.6", "PROC-INT-014 §3.2"}
    assert resultado.todas_respaldadas is True


# ─────────────────────────────────────────────────────────────────────────────
# Robustez
# ─────────────────────────────────────────────────────────────────────────────


def test_sin_material_recuperado_toda_cita_es_inventada() -> None:
    """El caso del agente que responde sin haber buscado."""
    resultado = validar_texto("Según [API-570-2016 §7.4]...", [])

    assert len(resultado.inventadas) == 1
    assert resultado.todas_respaldadas is False


def test_un_fragmento_sin_seccion_no_respalda_nada() -> None:
    """La ingesta se niega a producirlos, pero si uno entra por otro camino
    descartarlo es más seguro que respaldar con él."""
    huerfano = Document(page_content="texto", metadata={"doc_id": "X-1", "seccion": ""})

    assert citas_de([huerfano]) == []
    assert validar_texto("ver X-1 §1.1", [huerfano]).todas_respaldadas is False


def test_validar_citas_acepta_una_lista_ya_extraida() -> None:
    """Es la firma que declara el plan; `validar_texto` es el atajo."""
    citas = [Cita(doc_id="API-570-2016", seccion="7.4")]
    assert validar_citas(citas, RECUPERADOS).todas_respaldadas is True
