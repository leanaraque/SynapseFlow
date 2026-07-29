"""Capa de ontología: el dominio como dato, no como código.

Ver docs/adr/0003-ontologia-declarativa-en-yaml.md
"""

from synapseflow.ontology.compiler import (
    CompilationError,
    ToolResult,
    build_args_model,
    compile_tools,
    implements,
    interrupt_config,
    registered_actions,
    tool_description,
)
from synapseflow.ontology.loader import (
    DEFAULT_DOMAIN,
    OntologyError,
    available_domains,
    get_ontology,
    load_ontology_from_path,
)
from synapseflow.ontology.schema import (
    Action,
    Cardinality,
    ClassificationLevel,
    Effect,
    Entity,
    Ontology,
    Parameter,
    Property,
    PropertyType,
    Relation,
    Role,
)

__all__ = [
    "DEFAULT_DOMAIN",
    "Action",
    "Cardinality",
    "ClassificationLevel",
    "CompilationError",
    "Effect",
    "Entity",
    "Ontology",
    "OntologyError",
    "Parameter",
    "Property",
    "PropertyType",
    "Relation",
    "Role",
    "ToolResult",
    "available_domains",
    "build_args_model",
    "compile_tools",
    "get_ontology",
    "implements",
    "interrupt_config",
    "load_ontology_from_path",
    "registered_actions",
    "tool_description",
]
