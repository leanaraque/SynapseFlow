"""Contrato de la bandeja de aprobaciones.

**Es la pantalla donde alguien firma una acción irreversible.** Los tests de
autoridad de verdad están en `tests/api/test_aprobaciones.py`, porque la barrera
vive en el servidor: lo que la consola no muestre no es una medida de seguridad.

Lo que se verifica acá es lo otro, que igual importa: que lo que se muestra sea
lo que se va a ejecutar, que los botones salgan de la ontología y no estén
cableados, y que la consola no reimplemente la regla de quién puede aprobar.

Ver docs/plan/fases/F7-consola.md § F7.3
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
WEB = RAIZ / "apps" / "web" / "src"

BANDEJA = (WEB / "Aprobaciones.tsx").read_text(encoding="utf-8")
API_TS = (WEB / "api.ts").read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Lo mostrado es lo que se ejecuta
# ─────────────────────────────────────────────────────────────────────────────


def test_se_muestran_los_argumentos_uno_por_uno() -> None:
    """**Aprobar «una parada» no es aprobar nada.**

    Un resumen en una línea invita a no leerlos, y lo que se firma acá moviliza
    una cuadrilla o saca un equipo de servicio.
    """
    assert "Object.entries(pendiente.argumentos)" in BANDEJA


def test_el_texto_de_la_propuesta_viene_de_la_ontologia() -> None:
    """Es `approval_prompt`, ya formateado por la API con los valores reales.

    Si la consola lo redactara, lo que alguien lee al aprobar dejaría de ser lo
    que el dominio declara — y el YAML dejaría de ser la fuente de verdad.
    """
    assert "pendiente.descripcion" in BANDEJA
    assert not re.search(r"`Se va a \w+", BANDEJA), "la consola redacta el texto de aprobación"


def test_se_ve_quien_propuso() -> None:
    """Sin eso, la separación de funciones es una promesa que el supervisor no
    puede verificar mirando la pantalla."""
    assert "pendiente.propuesta_por" in BANDEJA


def test_se_ve_el_hilo() -> None:
    """Es la llave para reconstruir el razonamiento que llevó a la propuesta."""
    assert "pendiente.thread_id" in BANDEJA


# ─────────────────────────────────────────────────────────────────────────────
# Los botones salen del dominio
# ─────────────────────────────────────────────────────────────────────────────


def test_las_decisiones_ofrecidas_salen_de_la_ontologia() -> None:
    """**Ofrecer «editar» donde no se permite es prometer algo que el endpoint
    rechaza**, y el usuario descubre el límite después de decidir."""
    assert "pendiente.decisiones.includes" in BANDEJA


def test_cada_boton_esta_condicionado() -> None:
    """Los tres, no dos de tres: el que quede cableado es el que va a fallar."""
    for decision in ("approve", "reject", "edit"):
        assert f'puede("{decision}")' in BANDEJA


# ─────────────────────────────────────────────────────────────────────────────
# La garantía que sostiene todo
# ─────────────────────────────────────────────────────────────────────────────


def test_aprobar_no_manda_argumentos() -> None:
    """**Lo aprobado es lo ejecutado, y es estructural.**

    El grafo retoma la llamada que ya tenía en su checkpoint. Si la consola
    mandara argumentos al aprobar, abriría exactamente el agujero que el diseño
    del endpoint cierra.
    """
    llamada = re.search(r'onDecidir\(pendiente\.thread_id,\s*"aprobar",\s*(\{[^}]*\})', BANDEJA)

    assert llamada is not None, "no se encontró la llamada de aprobación"
    assert llamada.group(1).strip() == "{}", "aprobar está mandando algo además de la decisión"


def test_el_cliente_solo_manda_argumentos_al_editar() -> None:
    """`editar` es la excepción explícita, y la API la audita como tal."""
    assert 'if (decision === "editar" && extra.argumentos) cuerpo.argumentos' in API_TS


# ─────────────────────────────────────────────────────────────────────────────
# La consola no reimplementa la autoridad
# ─────────────────────────────────────────────────────────────────────────────


def test_la_consola_no_decide_quien_puede_aprobar() -> None:
    """Dos reglas de autoridad que dicen lo mismo son dos que se desincronizan,
    y la que corre en el navegador no protege de nada.

    La bandeja trae solo lo que este usuario puede resolver, filtrado por la API
    con el mismo código que valida el POST.
    """
    assert "aprobadores.includes" not in BANDEJA, "la consola filtra por rol por su cuenta"
    assert "propuesta_por ===" not in BANDEJA, "la consola verifica la separación de funciones"


def test_la_bandeja_explica_por_que_no_se_ve_lo_propio() -> None:
    """Un supervisor que no encuentra su propia propuesta tiene que entender que
    es el diseño y no un error."""
    assert "separación de funciones" in BANDEJA


# ─────────────────────────────────────────────────────────────────────────────
# Las vencidas
# ─────────────────────────────────────────────────────────────────────────────


def test_se_marcan_las_que_llevan_demasiado_esperando() -> None:
    """**No es solo estado ocupado en Firestore: es un equipo esperando.**

    El ADR-0005 declaró esta deuda al elegir `interrupt()`; esta es la parte que
    la mitiga.
    """
    assert "HORAS_PARA_VENCER" in BANDEJA
    assert "vencida" in BANDEJA


def test_una_fecha_ilegible_no_rompe_la_pantalla() -> None:
    """Un `creado_en` corrupto haría `NaN` y la tarjeta entera desaparecería —
    justo la que hay que mirar."""
    assert "Number.isNaN" in BANDEJA


# ─────────────────────────────────────────────────────────────────────────────
# El resultado
# ─────────────────────────────────────────────────────────────────────────────


def test_aprobar_muestra_el_resto_del_recorrido() -> None:
    """**Aprobar no es un endpoint que devuelve ok: es el resto del recorrido.**

    El supervisor ve ejecutarse lo que aprobó, por el mismo canal que ya conoce.
    """
    assert "for await (const evento of eventos(respuesta))" in BANDEJA


def test_la_bandeja_se_recarga_despues_de_decidir() -> None:
    """Si no, la propuesta resuelta sigue en pantalla y alguien la aprueba otra
    vez — para recibir un 409 que no esperaba."""
    assert "await recargar();" in BANDEJA
