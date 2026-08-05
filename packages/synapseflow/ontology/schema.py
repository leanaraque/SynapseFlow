"""Meta-esquema de la ontología de dominio.

Este módulo define la *forma* que debe tener un archivo de ontología, no el
contenido de ningún dominio en particular. El contenido vive en
`definitions/*.yaml`.

La validación acá es deliberadamente estricta y ruidosa: un dominio mal
declarado tiene que fallar al arrancar el proceso, no al ejecutar la acción.
Una acción irreversible sin rol aprobador es un agujero de gobernanza, y el
lugar donde eso se detecta es acá.

Ver docs/adr/0003-ontologia-declarativa-en-yaml.md
"""

from __future__ import annotations

import string
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Identificadores: snake_case, para poder mapearlos a nombres de herramienta y
# de campo sin transformaciones sorpresivas.
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$", min_length=2, max_length=64)]


class Effect(StrEnum):
    """Qué le hace una acción al mundo.

    La distinción entre `compute` y `read` no es cosmética: un cálculo no toca
    la base pero tampoco es una lectura, y conviene poder auditarlos por
    separado cuando se revisa de dónde salió un número.
    """

    READ = "read"
    COMPUTE = "compute"
    WRITE = "write"


class PropertyType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    ENUM = "enum"
    ARRAY = "array"
    REFERENCE = "reference"


class Cardinality(StrEnum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"


class StrictModel(BaseModel):
    """Base con `extra="forbid"`.

    Un campo de más en el YAML casi siempre es un error de tipeo en el nombre
    de un campo que sí importa: `pii` escrito `PII`, por ejemplo. Prohibir los
    extras convierte ese error silencioso en un fallo de arranque.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassificationLevel(StrictModel):
    """Nivel de sensibilidad de un dato.

    El `rank` ordena: un rol con `max_classification` de rank 1 no puede leer
    un campo de rank 2.
    """

    id: Identifier
    rank: int = Field(ge=0, le=10)
    description: str


class Role(StrictModel):
    id: Identifier
    title: str
    description: str
    max_classification: Identifier


class Property(StrictModel):
    """Un campo de una entidad."""

    name: Identifier
    type: PropertyType
    description: str
    required: bool = False
    # Solo para type=enum, y para type=array cuyos items son enum.
    values: list[str] | None = None
    # Solo para type=reference: id de la entidad apuntada.
    target: Identifier | None = None
    # Solo para type=array: tipo de los elementos.
    items: PropertyType | None = None
    unit: str | None = None
    example: Any = None
    default: Any = None
    # Marca de dato personal. Dispara redacción antes de salir del perímetro,
    # independientemente de la clasificación de la entidad que lo contiene.
    pii: bool = False
    # Sobreescribe la clasificación de la entidad para este campo puntual.
    classification: Identifier | None = None

    @model_validator(mode="after")
    def _coherencia_de_tipo(self) -> Property:
        if self.type is PropertyType.ENUM and not self.values:
            raise ValueError(f"la propiedad '{self.name}' es enum y no declara 'values'")
        if self.type is PropertyType.REFERENCE and not self.target:
            raise ValueError(f"la propiedad '{self.name}' es reference y no declara 'target'")
        if self.type is PropertyType.ARRAY and self.items is None:
            raise ValueError(f"la propiedad '{self.name}' es array y no declara 'items'")
        if self.values and self.type not in (PropertyType.ENUM, PropertyType.ARRAY):
            raise ValueError(
                f"la propiedad '{self.name}' declara 'values' pero su tipo es {self.type}"
            )
        return self


class Entity(StrictModel):
    id: Identifier
    name: str
    plural: str | None = None
    # Colección de Firestore donde viven las instancias.
    collection: str
    # Nombre de la propiedad que hace de clave natural.
    key: Identifier
    classification: Identifier
    description: str
    properties: list[Property]
    # True si la entidad se indexa en el vector store para RAG.
    vector_indexed: bool = False

    @model_validator(mode="after")
    def _la_clave_existe_y_es_requerida(self) -> Entity:
        nombres = [p.name for p in self.properties]
        if len(nombres) != len(set(nombres)):
            repetidos = sorted({n for n in nombres if nombres.count(n) > 1})
            raise ValueError(f"la entidad '{self.id}' repite propiedades: {repetidos}")
        if self.key not in set(nombres):
            raise ValueError(
                f"la entidad '{self.id}' declara key='{self.key}', "
                f"que no está entre sus propiedades: {sorted(nombres)}"
            )
        clave = next(p for p in self.properties if p.name == self.key)
        if not clave.required:
            raise ValueError(
                f"la entidad '{self.id}' usa '{self.key}' como clave natural, "
                "así que esa propiedad debe ser required"
            )
        return self

    def property_by_name(self, name: str) -> Property | None:
        return next((p for p in self.properties if p.name == name), None)


class Relation(StrictModel):
    # `from` es palabra reservada en Python: se acepta por alias y se expone
    # como `from_`.
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: Identifier
    name: str
    from_: Identifier = Field(alias="from")
    to: Identifier
    cardinality: Cardinality
    description: str
    inverse_name: str | None = None


class Parameter(StrictModel):
    """Un argumento de una acción.

    Se compila a un campo de un modelo Pydantic, que es lo que el LLM ve como
    schema de la herramienta. La `description` importa de verdad: es lo que el
    modelo lee para decidir qué pasar.
    """

    name: Identifier
    type: PropertyType
    description: str
    required: bool = False
    values: list[str] | None = None
    default: Any = None
    items: PropertyType | None = None

    @model_validator(mode="after")
    def _enum_con_valores(self) -> Parameter:
        if self.type is PropertyType.ENUM and not self.values:
            raise ValueError(f"el parámetro '{self.name}' es enum y no declara 'values'")
        return self


class Action(StrictModel):
    """Una operación que un agente puede invocar.

    Los campos `reversible` y `requires_approval` no son documentación: el
    compilador los lee para decidir si la herramienta se envuelve en un gate de
    aprobación humana. Ver docs/adr/0005-hitl-con-interrupt-de-langgraph.md
    """

    id: Identifier
    name: str
    description: str
    effect: Effect
    reversible: bool
    requires_approval: bool
    allowed_roles: list[Identifier] = Field(min_length=1)
    approver_roles: list[Identifier] = Field(default_factory=list)
    target_entity: Identifier
    parameters: list[Parameter] = Field(default_factory=list)
    # Texto que se le muestra al humano en el gate. Admite {placeholders} con
    # los nombres de los parámetros.
    approval_prompt: str | None = None

    @model_validator(mode="after")
    def _reglas_de_gobernanza(self) -> Action:
        # Esta es la regla que justifica que el meta-esquema exista.
        if not self.reversible and not self.requires_approval:
            raise ValueError(
                f"la acción '{self.id}' es irreversible y no requiere aprobación. "
                "Toda acción irreversible necesita un gate humano."
            )
        if self.requires_approval and not self.approver_roles:
            raise ValueError(
                f"la acción '{self.id}' requiere aprobación pero no declara "
                "'approver_roles': nadie podría aprobarla."
            )
        if self.effect is Effect.READ and not self.reversible:
            raise ValueError(
                f"la acción '{self.id}' es de lectura y está marcada como "
                "irreversible: probablemente sea un error de declaración."
            )
        nombres = [p.name for p in self.parameters]
        if len(nombres) != len(set(nombres)):
            raise ValueError(f"la acción '{self.id}' repite parámetros")
        return self

    @property
    def tool_name(self) -> str:
        """Nombre con el que el modelo ve la herramienta."""
        return self.id

    def parameter(self, name: str) -> Parameter | None:
        return next((p for p in self.parameters if p.name == name), None)


class Ontology(StrictModel):
    """Un dominio completo."""

    version: str
    domain: Identifier
    title: str
    description: str
    classification_levels: list[ClassificationLevel]
    roles: list[Role]
    entities: list[Entity]
    relations: list[Relation] = Field(default_factory=list)
    actions: list[Action]

    # ── Integridad referencial ───────────────────────────────────────────────
    # Todo lo que sigue son errores que, sin esta validación, se manifestarían
    # como un KeyError en runtime dentro de una herramienta, con el agente ya a
    # mitad de camino y el usuario esperando.

    @model_validator(mode="after")
    def _integridad_referencial(self) -> Ontology:
        clasificaciones = {c.id for c in self.classification_levels}
        roles = {r.id for r in self.roles}
        entidades = {e.id for e in self.entities}

        for coleccion, nombre in (
            (self.classification_levels, "classification_levels"),
            (self.roles, "roles"),
            (self.entities, "entities"),
            (self.actions, "actions"),
            (self.relations, "relations"),
        ):
            ids = [x.id for x in coleccion]
            if len(ids) != len(set(ids)):
                repetidos = sorted({i for i in ids if ids.count(i) > 1})
                raise ValueError(f"ids repetidos en {nombre}: {repetidos}")

        for rol in self.roles:
            if rol.max_classification not in clasificaciones:
                raise ValueError(
                    f"el rol '{rol.id}' referencia la clasificación "
                    f"'{rol.max_classification}', que no existe"
                )

        for entidad in self.entities:
            if entidad.classification not in clasificaciones:
                raise ValueError(
                    f"la entidad '{entidad.id}' referencia la clasificación "
                    f"'{entidad.classification}', que no existe"
                )
            for prop in entidad.properties:
                if prop.classification and prop.classification not in clasificaciones:
                    raise ValueError(
                        f"'{entidad.id}.{prop.name}' referencia la clasificación "
                        f"'{prop.classification}', que no existe"
                    )
                if prop.type is PropertyType.REFERENCE and prop.target not in entidades:
                    raise ValueError(
                        f"'{entidad.id}.{prop.name}' apunta a la entidad "
                        f"'{prop.target}', que no existe"
                    )

        for rel in self.relations:
            if rel.from_ not in entidades:
                raise ValueError(f"la relación '{rel.id}' sale de '{rel.from_}', que no existe")
            if rel.to not in entidades:
                raise ValueError(f"la relación '{rel.id}' llega a '{rel.to}', que no existe")

        for accion in self.actions:
            if accion.target_entity not in entidades:
                raise ValueError(
                    f"la acción '{accion.id}' apunta a la entidad "
                    f"'{accion.target_entity}', que no existe"
                )
            for rol_id in accion.allowed_roles:
                if rol_id not in roles:
                    raise ValueError(
                        f"la acción '{accion.id}' permite al rol '{rol_id}', que no existe"
                    )
            for rol_id in accion.approver_roles:
                if rol_id not in roles:
                    raise ValueError(
                        f"la acción '{accion.id}' declara como aprobador al rol "
                        f"'{rol_id}', que no existe"
                    )
            if accion.approval_prompt:
                _validar_placeholders(accion)

        return self

    # ── Accesores ────────────────────────────────────────────────────────────

    def entity(self, entity_id: str) -> Entity:
        e = next((x for x in self.entities if x.id == entity_id), None)
        if e is None:
            raise KeyError(f"no existe la entidad '{entity_id}' en el dominio '{self.domain}'")
        return e

    def action(self, action_id: str) -> Action:
        a = next((x for x in self.actions if x.id == action_id), None)
        if a is None:
            raise KeyError(f"no existe la acción '{action_id}' en el dominio '{self.domain}'")
        return a

    def role(self, role_id: str) -> Role:
        r = next((x for x in self.roles if x.id == role_id), None)
        if r is None:
            raise KeyError(f"no existe el rol '{role_id}' en el dominio '{self.domain}'")
        return r

    def classification_rank(self, classification_id: str) -> int:
        c = next((x for x in self.classification_levels if x.id == classification_id), None)
        if c is None:
            raise KeyError(f"no existe la clasificación '{classification_id}'")
        return c.rank

    def actions_for_role(self, role_id: str) -> list[Action]:
        """Catálogo de herramientas visible para un rol.

        Mínimo privilegio de verdad: el agente recibe exactamente estas
        acciones, no todas las del dominio con un chequeo posterior. Lo que el
        modelo no ve, no lo puede invocar ni alucinar como disponible.
        """
        self.role(role_id)  # valida que el rol exista
        return [a for a in self.actions if role_id in a.allowed_roles]

    def irreversible_actions(self) -> list[Action]:
        return [a for a in self.actions if not a.reversible]

    def actions_requiring_approval(self) -> list[Action]:
        return [a for a in self.actions if a.requires_approval]

    def pii_fields(self) -> dict[str, list[str]]:
        """Campos que nunca salen en claro del perímetro, por entidad.

        Lo consume el redactor del gateway. Incluye los marcados `pii` y los que
        tienen clasificación `restricted` o superior.
        """
        umbral = self.classification_rank("restricted")
        resultado: dict[str, list[str]] = {}
        for entidad in self.entities:
            campos = [
                p.name
                for p in entidad.properties
                if p.pii
                or (p.classification and self.classification_rank(p.classification) >= umbral)
            ]
            if campos:
                resultado[entidad.id] = campos
        return resultado

    def max_classification_for_role(self, role_id: str) -> int:
        return self.classification_rank(self.role(role_id).max_classification)

    def can_role_read_entity(self, role_id: str, entity_id: str) -> bool:
        return self.classification_rank(
            self.entity(entity_id).classification
        ) <= self.max_classification_for_role(role_id)


def _validar_placeholders(accion: Action) -> None:
    """Verifica los {placeholders} del texto de aprobación.

    Un placeholder que no corresponde a ningún parámetro produce un texto roto
    justo en el momento más sensible del flujo: cuando un supervisor tiene que
    decidir si autoriza la parada de un equipo.
    """
    plantilla = accion.approval_prompt
    if not plantilla:
        return

    declarados = {p.name for p in accion.parameters}
    # La entidad objetivo aporta su clave natural y algunos campos de contexto
    # que el gate puede querer mostrar aunque no sean parámetros de la acción.
    contexto_permitido = {"tag", "prioridad", "id_ot", "criticidad_nueva", "motivo"}
    permitidos = declarados | contexto_permitido
    usados = {campo for _, campo, _, _ in string.Formatter().parse(plantilla) if campo}
    desconocidos = usados - permitidos
    if desconocidos:
        raise ValueError(
            f"el approval_prompt de '{accion.id}' usa placeholders que no "
            f"corresponden a parámetros ni a campos de contexto: {sorted(desconocidos)}"
        )
