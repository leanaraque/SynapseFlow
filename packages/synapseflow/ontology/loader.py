"""Carga y validación de archivos de ontología.

La carga está cacheada por ruta: la ontología se lee una vez por proceso. En
Cloud Run eso significa una vez por instancia, en el arranque en frío, que es
también donde conviene que falle si el dominio está mal declarado.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from synapseflow.ontology.schema import Ontology

DEFINITIONS_DIR = Path(__file__).parent / "definitions"
DEFAULT_DOMAIN = "oil_and_gas"


class OntologyError(RuntimeError):
    """La ontología no se pudo cargar o no es válida.

    Se distingue de ValidationError para que la API pueda responder algo
    inteligible y el arranque falle con un mensaje que diga qué corregir en el
    YAML, en lugar de un traceback de Pydantic.
    """


def available_domains() -> list[str]:
    """Dominios disponibles, por nombre de archivo."""
    return sorted(p.stem for p in DEFINITIONS_DIR.glob("*.yaml"))


def load_ontology_from_path(path: Path) -> Ontology:
    """Lee y valida un archivo de ontología.

    Se separa de `get_ontology` para poder cargar archivos arbitrarios en los
    tests, incluyendo los deliberadamente inválidos.
    """
    if not path.is_file():
        raise OntologyError(
            f"no existe el archivo de ontología '{path}'. "
            f"Dominios disponibles: {available_domains()}"
        )

    try:
        crudo = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise OntologyError(f"el YAML de '{path.name}' no se puede parsear: {exc}") from exc

    if not isinstance(crudo, dict):
        raise OntologyError(
            f"'{path.name}' debe contener un mapeo en la raíz, pero contiene {type(crudo).__name__}"
        )

    try:
        return Ontology.model_validate(crudo)
    except ValidationError as exc:
        raise OntologyError(_formatear_errores(path.name, exc)) from exc


@lru_cache(maxsize=8)
def get_ontology(domain: str = DEFAULT_DOMAIN) -> Ontology:
    """Ontología del dominio pedido, cacheada por proceso."""
    return load_ontology_from_path(DEFINITIONS_DIR / f"{domain}.yaml")


def _formatear_errores(nombre_archivo: str, exc: ValidationError) -> str:
    """Traduce los errores de Pydantic a algo accionable.

    El error crudo de Pydantic sobre un YAML anidado señala rutas como
    `actions.7.approver_roles` sin decir de qué acción se trata, que es
    justamente el dato que hace falta para arreglarlo.
    """
    lineas = [f"la ontología '{nombre_archivo}' no es válida:"]
    for err in exc.errors():
        ruta = ".".join(str(p) for p in err["loc"]) or "(raíz)"
        lineas.append(f"  · {ruta}: {err['msg']}")
    return "\n".join(lineas)
