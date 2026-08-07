"""Contrato de la tokenización de datos personales. Cierra el compromiso 5.

Tres propiedades, y la tercera es la que se pasa por alto:

1. El token es **estable** dentro de una conversación: el modelo tiene que poder
   razonar sobre «el inspector» a lo largo de varios turnos.
2. La rehidratación devuelve el valor original, así que el usuario nunca ve el
   token.
3. **El token NO es estable entre conversaciones.** Un hash sería más simple y
   sería una fuga: el espacio de legajos es de cien mil valores, así que
   cualquiera con el token y la función recupera el legajo probando todos.

Ver docs/plan/fases/F4-gobernanza.md § F4.2
"""

from __future__ import annotations

import pytest

from synapseflow.governance.pii import (
    LEGAJO,
    Tokenizador,
    campos_pii,
    contiene_pii,
    detectar_legajos,
)

TEXTO = "El inspector LEG-00042 registró el hallazgo y LEG-00099 lo validó."


# ─────────────────────────────────────────────────────────────────────────────
# Detección
# ─────────────────────────────────────────────────────────────────────────────


def test_se_detectan_los_legajos_del_dominio() -> None:
    assert detectar_legajos(TEXTO) == ["LEG-00042", "LEG-00099"]


def test_los_legajos_repetidos_se_reportan_una_vez() -> None:
    assert detectar_legajos("LEG-00042 y otra vez LEG-00042") == ["LEG-00042"]


def test_un_texto_sin_legajos_no_reporta_nada() -> None:
    assert detectar_legajos("El espesor medido es 6,8 mm.") == []
    assert contiene_pii("El espesor medido es 6,8 mm.") is False


@pytest.mark.parametrize("falso_positivo", ["LEG-42", "LEG-000420", "XLEG-00042", "leg-00042"])
def test_no_se_detecta_lo_que_no_es_un_legajo(falso_positivo: str) -> None:
    """El formato es `LEG-` y cinco dígitos.

    Tokenizar de más degrada la respuesta sin proteger nada: el modelo pierde un
    dato que sí podía usar.
    """
    assert LEGAJO.fullmatch(falso_positivo) is None


# ─────────────────────────────────────────────────────────────────────────────
# Tokenización
# ─────────────────────────────────────────────────────────────────────────────


def test_el_legajo_no_sobrevive_a_la_tokenizacion() -> None:
    """Es literalmente el compromiso 5."""
    seguro = Tokenizador().tokenizar(TEXTO)

    assert "LEG-00042" not in seguro
    assert "LEG-00099" not in seguro
    assert detectar_legajos(seguro) == []


def test_el_mismo_legajo_produce_siempre_el_mismo_token() -> None:
    """Dentro de una conversación, «INSPECTOR_1» es siempre la misma persona.

    Sin esto, el modelo no puede razonar sobre el referente: dos menciones del
    mismo inspector le parecerían dos personas distintas.
    """
    tok = Tokenizador()
    primera = tok.tokenizar("Lo firmó LEG-00042.")
    segunda = tok.tokenizar("Y LEG-00042 también validó el anterior.")

    assert "«INSPECTOR_1»" in primera
    assert "«INSPECTOR_1»" in segunda


def test_legajos_distintos_producen_tokens_distintos() -> None:
    tok = Tokenizador()
    seguro = tok.tokenizar(TEXTO)

    assert "«INSPECTOR_1»" in seguro
    assert "«INSPECTOR_2»" in seguro
    assert len(tok) == 2


def test_el_token_conserva_el_referente_del_dominio() -> None:
    """Se eligió «INSPECTOR» y no «PII_1» a propósito.

    «El inspector» es una entidad que aparece en la normativa; «PII_1» no
    significa nada y degrada la respuesta más de lo necesario.
    """
    assert "INSPECTOR" in Tokenizador().tokenizar("LEG-00042")


def test_el_resto_del_texto_no_se_toca() -> None:
    seguro = Tokenizador().tokenizar(TEXTO)
    assert "registró el hallazgo" in seguro
    assert "lo validó" in seguro


# ─────────────────────────────────────────────────────────────────────────────
# La propiedad que se pasa por alto
# ─────────────────────────────────────────────────────────────────────────────


def test_dos_conversaciones_no_producen_el_mismo_token_para_la_misma_persona() -> None:
    """**Un hash sería más simple y sería una fuga.**

    El espacio de legajos es de cien mil valores: con el token y la función de
    hash, cualquiera recupera el legajo probando todos. El contador por
    conversación da la estabilidad que hace falta sin permitir correlacionar a la
    misma persona entre hilos distintos, que es algo que un proveedor externo no
    necesita poder hacer.
    """
    hilo_a = Tokenizador()
    hilo_b = Tokenizador()

    hilo_a.tokenizar("LEG-00042 firmó.")
    hilo_b.tokenizar("LEG-00777 firmó. Después LEG-00042 revisó.")

    assert hilo_a.mapa["«INSPECTOR_1»"] == "LEG-00042"
    assert hilo_b.mapa["«INSPECTOR_1»"] == "LEG-00777"
    assert hilo_b.mapa["«INSPECTOR_2»"] == "LEG-00042"


# ─────────────────────────────────────────────────────────────────────────────
# Rehidratación
# ─────────────────────────────────────────────────────────────────────────────


def test_la_rehidratacion_devuelve_el_valor_original() -> None:
    """El usuario nunca ve el token."""
    tok = Tokenizador()
    tok.tokenizar(TEXTO)

    assert tok.rehidratar("«INSPECTOR_1» debe recalibrar el equipo.") == (
        "LEG-00042 debe recalibrar el equipo."
    )


def test_el_ciclo_completo_es_reversible() -> None:
    tok = Tokenizador()
    assert tok.rehidratar(tok.tokenizar(TEXTO)) == TEXTO


def test_un_token_que_el_sistema_no_emitio_se_deja_como_esta() -> None:
    """El modelo puede inventar «INSPECTOR_9».

    Reemplazarlo por un legajo cualquiera sería atribuirle un hallazgo a una
    persona que no lo firmó: el peor resultado posible de una capa que existe
    para proteger a esa persona.
    """
    tok = Tokenizador()
    tok.tokenizar("LEG-00042 firmó.")

    assert tok.rehidratar("Según «INSPECTOR_9»...") == "Según «INSPECTOR_9»..."


# ─────────────────────────────────────────────────────────────────────────────
# Estructuras y campos derivados de la ontología
# ─────────────────────────────────────────────────────────────────────────────


def test_los_campos_pii_salen_de_la_ontologia() -> None:
    """Marcar un campo en el YAML alcanza para que se redacte."""
    assert "inspector_legajo" in campos_pii()


def test_una_estructura_anidada_se_tokeniza_por_campo() -> None:
    """Es el camino para valores sin forma reconocible por patrón."""
    tok = Tokenizador()
    datos = {
        "inspecciones": [
            {"id_inspeccion": "INS-1", "inspector_legajo": "LEG-00042", "espesor_medido_mm": 6.8},
            {"id_inspeccion": "INS-2", "inspector_legajo": "LEG-00099"},
        ]
    }

    seguro = tok.tokenizar_estructura(datos, {"inspector_legajo"})

    legajos = [i["inspector_legajo"] for i in seguro["inspecciones"]]
    assert legajos == ["«INSPECTOR_1»", "«INSPECTOR_2»"]
    assert seguro["inspecciones"][0]["espesor_medido_mm"] == 6.8, "se tocó un dato que no es PII"
    assert seguro["inspecciones"][0]["id_inspeccion"] == "INS-1"


def test_un_campo_ausente_no_rompe_la_tokenizacion() -> None:
    tok = Tokenizador()
    assert tok.tokenizar_estructura({"otro": "valor"}, {"inspector_legajo"}) == {"otro": "valor"}


def test_un_legajo_en_texto_libre_dentro_de_una_estructura_tambien_se_tokeniza() -> None:
    """Las dos capas de detección se aplican juntas.

    Un legajo puede venir en el `hallazgo` redactado por una persona, no solo en
    el campo que la ontología marca.
    """
    tok = Tokenizador()
    seguro = tok.tokenizar_estructura(
        {"hallazgo": "Verificado con LEG-00042 en campo."}, {"inspector_legajo"}
    )

    assert "LEG-00042" not in seguro["hallazgo"]
