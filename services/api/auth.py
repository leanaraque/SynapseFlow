"""Del token de Firebase Auth al contexto de ejecución.

## La regla que no se puede relajar

**El agente hereda los permisos del usuario, nunca los de la cuenta de servicio.**
La cuenta con la que corre Cloud Run pasa por encima de las reglas de Firestore
por diseño —así lo documenta `firestore.rules`— y por eso la API tiene que
aplicar los permisos ella misma. Si esta capa se equivoca, no hay una segunda que
la corrija: el usuario queda con los permisos del servicio, que son todos.

## Un rol inválido es un rechazo, no un valor por defecto

Es la decisión que más fácil se toma mal. «Si no tiene rol, dale `consulta`»
parece prudente —es el rol más restringido— y es un error: convierte un problema
de aprovisionamiento de identidad en un acceso silencioso. El usuario entra, ve
normativa pública, y nadie se entera de que sus claims nunca se configuraron.

Un rol que no existe en la ontología es lo mismo: puede ser un typo en los claims
o un rol que se eliminó del YAML. En los dos casos, la respuesta es 403 con el
motivo, no un rol de consuelo.

Ver docs/plan/fases/F6-api.md § F6.1
"""

from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, status

from synapseflow.governance.rbac import ExecutionContext
from synapseflow.ontology import Ontology, get_ontology

# Claim donde vive el rol del dominio. Es un custom claim: Firebase Auth no sabe
# nada de inspectores ni de supervisores, y ese mapeo lo administra quien da de
# alta a la persona.
CLAIM_DE_ROL = "synapseflow_rol"


class ErrorDeIdentidad(HTTPException):
    """El token no permite construir un contexto de ejecución.

    Tipo propio para que el manejador de errores pueda distinguir «no sé quién
    sos» de «sé quién sos y no podés hacer esto», que son 401 y 403 y se
    resuelven distinto: uno volviendo a autenticar, el otro pidiéndole a alguien
    que configure tus permisos.
    """


def verificar_token(token: str) -> dict[str, Any]:
    """Valida el token contra Firebase Auth y devuelve sus claims.

    Se importa `firebase_admin` acá adentro y no al tope del módulo para que los
    tests de la capa de permisos no necesiten inicializar el SDK ni tener
    credenciales: lo que se testea es la resolución del rol, no la criptografía
    de Google.
    """
    from firebase_admin import auth

    try:
        return dict(auth.verify_id_token(token))
    except Exception as exc:
        raise ErrorDeIdentidad(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token de Firebase no es válido o expiró.",
        ) from exc


async def resolver_usuario(
    token: str,
    *,
    ontologia: Ontology | None = None,
    verificador: Any = None,
    thread_id: str | None = None,
) -> ExecutionContext:
    """Del token de Firebase Auth al contexto de ejecución.

    El rol sale de los custom claims y **se valida contra la ontología**: un rol
    que no existe en el dominio es un rechazo, no un valor por defecto.

    Args:
        token: el id token de Firebase.
        ontologia: dominio cargado, para validar el rol.
        verificador: función que valida el token. Inyectable para los tests, que
            no deben depender del SDK de Firebase.
        thread_id: hilo de conversación, si ya se conoce.

    Raises:
        ErrorDeIdentidad: 401 si el token no vale; 403 si el rol falta o no
            existe en la ontología.
    """
    claims = (verificador or verificar_token)(token)

    uid = claims.get("uid") or claims.get("sub")
    if not uid:
        raise ErrorDeIdentidad(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token no identifica a ningún usuario.",
        )

    rol = claims.get(CLAIM_DE_ROL)
    if not rol:
        raise ErrorDeIdentidad(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"El usuario no tiene el claim '{CLAIM_DE_ROL}'. No se asigna un "
                "rol por defecto: eso convertiría un problema de aprovisionamiento "
                "de identidad en un acceso silencioso."
            ),
        )

    onto = ontologia or get_ontology()
    validos = {r.id for r in onto.roles}
    if rol not in validos:
        raise ErrorDeIdentidad(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"El rol '{rol}' no existe en el dominio. Roles válidos: "
                f"{sorted(validos)}. Puede ser un typo en los claims o un rol "
                "que se eliminó del YAML."
            ),
        )

    return ExecutionContext(
        usuario=str(uid),
        rol=str(rol),
        thread_id=thread_id,
        nombre=claims.get("name") or claims.get("email"),
    )


async def usuario_actual(
    authorization: str = Header(default=""),
    x_thread_id: str = Header(default=""),
) -> ExecutionContext:
    """Dependencia de FastAPI: extrae el token del header y resuelve el usuario.

    El header se parsea acá y no en cada endpoint para que ninguno pueda
    olvidarse de exigirlo. Un endpoint sin identidad no es un endpoint con menos
    seguridad: es uno que corre con la identidad del servicio.
    """
    esquema, _, token = authorization.partition(" ")

    if esquema.lower() != "bearer" or not token.strip():
        raise ErrorDeIdentidad(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta el header 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await resolver_usuario(token.strip(), thread_id=x_thread_id or None)


def roles_del_dominio(ontologia: Ontology | None = None) -> tuple[str, ...]:
    """Roles que la ontología declara. Lo consume el endpoint de diagnóstico."""
    return tuple(sorted(r.id for r in (ontologia or get_ontology()).roles))
