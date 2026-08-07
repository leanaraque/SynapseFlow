"""Tokenización y rehidratación de datos personales.

**Cierra el compromiso 5: los datos sensibles no salen del perímetro.**

Un legajo como `LEG-00042` identifica a una persona. Antes de que el texto cruce
hacia un proveedor externo se reemplaza por un token estable —`«INSPECTOR_1»`— y
al volver la respuesta se rehidrata. El modelo externo nunca ve el legajo; el
usuario nunca ve el token.

## Por qué el token es un contador y no un hash

La tentación es derivar el token de un hash del legajo: sería estable entre
procesos y no habría que mantener estado. **Sería una fuga.** El espacio de
legajos es chico —cinco dígitos, cien mil posibilidades— así que cualquiera con
el token y la función de hash recupera el legajo probando todos. Un hash sobre un
identificador de espacio chico no anonimiza: ofusca.

El contador es por conversación. Eso da lo que hace falta —dentro de un hilo,
`«INSPECTOR_1»` es siempre la misma persona, así que el modelo puede razonar
sobre ella— sin dar lo que sería peligroso: correlacionar la misma persona entre
conversaciones distintas, que es exactamente lo que un proveedor externo no
necesita poder hacer.

## Qué se detecta

Dos fuentes, y hacen falta las dos:

1. **Por patrón**, sobre texto libre. Es lo que atrapa un legajo que el modelo
   escribió en su propio razonamiento, o que vino dentro de un `content` de
   herramienta.
2. **Por campo**, desde `ontology.pii_fields()`. Es lo que atrapa un valor que no
   tiene forma reconocible — un nombre propio, por ejemplo — cuando se redacta
   una estructura y no un texto.

Ver docs/plan/fases/F4-gobernanza.md § F4.2
"""

from __future__ import annotations

import re
from typing import Any

from synapseflow.ontology import Ontology, get_ontology

# Formato de legajo del dominio: `LEG-` y cinco dígitos. Sale de
# `scripts/generar_datos.py` y de la ontología, que lo declara en el `example`
# de `inspection.inspector_legajo`.
LEGAJO = re.compile(r"\bLEG-\d{5}\b")

# Prefijo del token. Se eligió una palabra del dominio y no `PII_1` porque el
# modelo tiene que poder razonar sobre el referente: «el inspector» es una
# entidad que aparece en la normativa, «PII_1» no significa nada y degrada la
# calidad de la respuesta más de lo necesario.
PREFIJO = "INSPECTOR"

# Delimitadores del token. Las comillas angulares hacen que sea visualmente
# obvio que es un marcador y no un dato, tanto en el prompt como en un log.
ABRE, CIERRA = "«", "»"

TOKEN = re.compile(rf"{ABRE}({PREFIJO}_\d+){CIERRA}")


class Tokenizador:
    """Sustituye datos personales por tokens estables, y los devuelve.

    Se instancia **una por conversación**. Compartir uno entre hilos
    correlacionaría a la misma persona entre conversaciones distintas, que es
    justo lo que el diseño evita.

        tok = Tokenizador()
        seguro = tok.tokenizar("El inspector LEG-00042 firmó el hallazgo.")
        # → "El inspector «INSPECTOR_1» firmó el hallazgo."
        tok.rehidratar("«INSPECTOR_1» debe recalibrar el equipo.")
        # → "LEG-00042 debe recalibrar el equipo."
    """

    def __init__(self) -> None:
        # legajo → token, y su inverso. Se mantienen los dos para no recorrer
        # linealmente en la rehidratación, que ocurre en el camino de la
        # respuesta al usuario.
        self._por_valor: dict[str, str] = {}
        self._por_token: dict[str, str] = {}

    # ── Salida: del perímetro hacia afuera ───────────────────────────────────

    def tokenizar(self, texto: str) -> str:
        """Reemplaza todo dato personal reconocible por su token."""
        return LEGAJO.sub(lambda m: self.token_de(m.group(0)), texto)

    def token_de(self, valor: str) -> str:
        """Token estable para un valor. El mismo valor da siempre el mismo."""
        if valor not in self._por_valor:
            token = f"{ABRE}{PREFIJO}_{len(self._por_valor) + 1}{CIERRA}"
            self._por_valor[valor] = token
            self._por_token[token] = valor
        return self._por_valor[valor]

    def tokenizar_estructura(self, datos: Any, campos: set[str]) -> Any:
        """Tokeniza los campos nombrados de una estructura anidada.

        Es el camino para datos que no tienen forma reconocible por patrón. Los
        nombres salen de `campos_pii()`, o sea de la ontología: agregar un campo
        personal al YAML alcanza para que se redacte.
        """
        if isinstance(datos, dict):
            return {
                clave: (
                    self.token_de(str(valor))
                    if clave in campos and valor is not None
                    else self.tokenizar_estructura(valor, campos)
                )
                for clave, valor in datos.items()
            }
        if isinstance(datos, list):
            return [self.tokenizar_estructura(item, campos) for item in datos]
        if isinstance(datos, str):
            return self.tokenizar(datos)
        return datos

    # ── Entrada: de afuera hacia el perímetro ────────────────────────────────

    def rehidratar(self, texto: str) -> str:
        """Devuelve los valores originales en lugar de los tokens.

        Un token que este tokenizador no emitió se deja como está: el modelo
        puede inventar `«INSPECTOR_9»` y no hay nada que poner en su lugar.
        Reemplazarlo por un legajo cualquiera sería atribuirle un hallazgo a una
        persona que no lo firmó.
        """
        return TOKEN.sub(
            lambda m: self._por_token.get(f"{ABRE}{m.group(1)}{CIERRA}", m.group(0)), texto
        )

    # ── Estado observable ────────────────────────────────────────────────────

    @property
    def mapa(self) -> dict[str, str]:
        """Token → valor original. Lo consume el log de auditoría."""
        return dict(self._por_token)

    def __len__(self) -> int:
        return len(self._por_valor)


# ─────────────────────────────────────────────────────────────────────────────
# Detección
# ─────────────────────────────────────────────────────────────────────────────


def detectar_legajos(texto: str) -> list[str]:
    """Legajos presentes en un texto, sin repetir y en orden de aparición.

    Lo usa el detector de `PIIMiddleware` y también el test de la frontera de
    datos, que verifica que ninguno llegó al modelo.
    """
    vistos: dict[str, None] = {}
    for coincidencia in LEGAJO.finditer(texto):
        vistos.setdefault(coincidencia.group(0), None)
    return list(vistos)


def contiene_pii(texto: str) -> bool:
    return bool(LEGAJO.search(texto))


def campos_pii(ontologia: Ontology | None = None) -> set[str]:
    """Nombres de campo que la ontología marca como no publicables.

    Se derivan del YAML y no se listan a mano: marcar una entidad entera como
    `restricted` alcanza para que todos sus campos entren acá. Ver
    `Ontology.pii_fields`.
    """
    por_entidad = (ontologia or get_ontology()).pii_fields()
    return {campo for campos in por_entidad.values() for campo in campos}
