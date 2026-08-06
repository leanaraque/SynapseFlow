"""El catálogo de precios se desactualiza en silencio.

Los proveedores cambian precios sin aviso. Un catálogo viejo no rompe nada
visible: sigue calculando, solo que mal. Y ese número no es decorativo — es el
que sostiene la decisión de qué modelo va en qué perfil y lo que muestra el panel
de costos.

Este test es un interruptor a plazo: falla cuando pasan más de noventa días
desde la última verificación contra la página de precios del proveedor. La forma
de arreglarlo no es tocar el test, sino contrastar los precios y actualizar
`verified_on` en `models.yaml`.

Que un día vaya a fallar solo es intencional. Ver
docs/adr/0004-gateway-provider-agnostic.md § Verificación
"""

from __future__ import annotations

import datetime as dt

from synapseflow.llm import registry


def test_los_precios_se_verificaron_hace_menos_de_noventa_dias() -> None:
    dias = registry.dias_desde_verificacion()
    assert dias <= registry.DIAS_DE_VIGENCIA_DE_PRECIOS, (
        f"los precios de models.yaml se verificaron hace {dias} días "
        f"({registry.verificado_el()}), y el límite es {registry.DIAS_DE_VIGENCIA_DE_PRECIOS}.\n"
        "  Contrastalos contra la página de precios de cada proveedor y actualizá "
        "`verified_on`. No subas el límite: el punto del test es que la "
        "contabilidad de costos no mienta."
    )


def test_el_plazo_se_cuenta_de_verdad() -> None:
    """El test de arriba tiene que poder fallar.

    Un interruptor a plazo que no se dispara nunca es peor que no tenerlo: da la
    impresión de que alguien está mirando los precios.
    """
    verificado = registry.verificado_el()
    vencido = verificado + dt.timedelta(days=registry.DIAS_DE_VIGENCIA_DE_PRECIOS + 1)
    assert registry.dias_desde_verificacion(vencido) > registry.DIAS_DE_VIGENCIA_DE_PRECIOS

    al_limite = verificado + dt.timedelta(days=registry.DIAS_DE_VIGENCIA_DE_PRECIOS)
    assert registry.dias_desde_verificacion(al_limite) == registry.DIAS_DE_VIGENCIA_DE_PRECIOS
