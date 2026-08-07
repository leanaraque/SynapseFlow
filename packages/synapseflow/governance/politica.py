"""Política de proveedores: qué dato puede cruzar hacia afuera, y hacia quién.

## Por qué este módulo no importa nada de `synapseflow.llm`

Podría preguntarle al registry si el proveedor declara zero-training, y sería
más cómodo. También sería un import circular: el gateway —que está en `llm`—
tiene que llamar a esta política, y `llm.__init__` importa el gateway.

Así que la política **recibe el hecho y aplica la regla**, en lugar de ir a
buscarlo. La inversión no es solo para esquivar el ciclo: hace que la regla se
pueda testear sin catálogo, y deja explícito que este módulo decide y no
averigua.

## Qué es una declaración y qué es una verificación

`zero_training` en `models.yaml` es lo que el **catálogo declara**, no algo que
se compruebe contra el proveedor. No hay forma programática de verificar que una
empresa no entrena con los datos que recibe: para un cliente regulado el
respaldo es el contrato, y el catálogo es dónde queda anotado quién lo firmó.

Este módulo es honesto sobre eso en su mensaje de error. Un sistema que dijera
«verificado que no entrena» estaría mintiendo sobre la naturaleza de la garantía.

Ver docs/plan/fases/F4-gobernanza.md § F4.4
"""

from __future__ import annotations

from synapseflow.ontology import Ontology, get_ontology

# Clasificación que nunca sale del perímetro en claro, cualquiera sea el
# proveedor y cualquiera sea su política. Es el piso del compromiso 5: un dato
# `restricted` identifica a una persona o expone un hallazgo atribuible, y para
# eso no hay contrato que alcance.
NUNCA_SALE = "restricted"


class PoliticaVioladaError(RuntimeError):
    """Un proveedor no cumple una política que está exigida.

    Tipo propio porque no es un error de configuración que el usuario pueda
    corregir con una variable de entorno: es una decisión de gobernanza. Quien
    la reciba tiene que poder distinguirla y reportarla como tal.
    """


def exigir_zero_training(proveedor: str, *, declarado: bool, exigido: bool = True) -> None:
    """Falla si la política está activa y el proveedor no la declara.

    Args:
        proveedor: nombre del proveedor, solo para el mensaje.
        declarado: lo que el catálogo declara. Lo aporta quien llama, porque
            este módulo no consulta el catálogo (ver el docstring del módulo).
        exigido: si `SYNAPSEFLOW_ENFORCE_ZERO_TRAINING` está activo.

    Raises:
        PoliticaVioladaError: con el motivo y las salidas posibles.
    """
    if not exigido or declarado:
        return

    raise PoliticaVioladaError(
        f"SYNAPSEFLOW_ENFORCE_ZERO_TRAINING está activo y el catálogo no declara "
        f"zero_training para '{proveedor}'.\n"
        "  Mandar datos del cliente a un proveedor que entrena con ellos es "
        "justamente lo que la política impide.\n"
        "  Lo que el catálogo registra es una DECLARACIÓN respaldada por "
        "contrato, no una verificación técnica: no hay forma de comprobar desde "
        "el código que una empresa no entrena con lo que recibe.\n"
        "  Opciones: cambiar SYNAPSEFLOW_PROVIDER, o desactivar la política de "
        "forma explícita si el contrato lo respalda."
    )


def puede_salir(clasificacion: str, ontologia: Ontology | None = None) -> bool:
    """Si un dato de esta clasificación puede cruzar el perímetro en claro.

    Todo lo que llegue al rango de `restricted` se tokeniza antes de salir. Lo
    de abajo sale tal cual: la normativa es pública, y los datos operativos son
    internos pero no identifican a nadie.
    """
    onto = ontologia or get_ontology()
    return onto.classification_rank(clasificacion) < onto.classification_rank(NUNCA_SALE)


def clasificaciones_que_nunca_salen(ontologia: Ontology | None = None) -> tuple[str, ...]:
    """Niveles cuyo dato se tokeniza siempre, en orden de rango.

    Se derivan de la ontología y no se listan a mano: agregar un nivel por
    encima de `restricted` lo incluye automáticamente, que es lo contrario de
    tener que acordarse.
    """
    onto = ontologia or get_ontology()
    umbral = onto.classification_rank(NUNCA_SALE)

    return tuple(
        nivel.id
        for nivel in sorted(onto.classification_levels, key=lambda n: n.rank)
        if nivel.rank >= umbral
    )
