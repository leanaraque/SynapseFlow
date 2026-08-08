"""Contrato de la identidad de la API.

**Es la capa de la que no hay una segunda.** Si acá se resuelve mal el rol, el
agente corre con los permisos de la cuenta de servicio —que son todos— y ninguna
capa posterior lo corrige: la cuenta de servicio pasa por encima de las reglas de
Firestore por diseño.

Los tests que más importan son los negativos, y en particular el de «sin rol no
se asigna uno por defecto». Es la decisión que más fácil se toma mal: dar
`consulta` a quien no tiene claims parece prudente y convierte un problema de
aprovisionamiento de identidad en un acceso silencioso.

Ver docs/plan/fases/F6-api.md § F6.1
"""

from __future__ import annotations

from typing import Any

import pytest

from services.api.auth import (
    CLAIM_DE_ROL,
    ErrorDeIdentidad,
    resolver_usuario,
    roles_del_dominio,
    usuario_actual,
)
from synapseflow.ontology import get_ontology

ONTOLOGIA = get_ontology()


def claims(**extra: Any) -> Any:
    """Verificador de token falso: devuelve los claims que le dicten.

    Los tests no deben depender del SDK de Firebase: lo que se verifica es la
    resolución del rol, no la criptografía de Google.
    """
    base: dict[str, Any] = {"uid": "uid-1", CLAIM_DE_ROL: "inspector"}
    base.update(extra)
    return lambda _token: base


# ─────────────────────────────────────────────────────────────────────────────
# El camino feliz
# ─────────────────────────────────────────────────────────────────────────────


async def test_un_token_valido_produce_el_contexto() -> None:
    ctx = await resolver_usuario("t", ontologia=ONTOLOGIA, verificador=claims())

    assert ctx.usuario == "uid-1"
    assert ctx.rol == "inspector"


async def test_el_nombre_sale_de_los_claims() -> None:
    """La consola lo muestra; el log de auditoría guarda el uid."""
    ctx = await resolver_usuario("t", ontologia=ONTOLOGIA, verificador=claims(name="Marta Suárez"))
    assert ctx.nombre == "Marta Suárez"


async def test_sin_name_se_usa_el_email() -> None:
    ctx = await resolver_usuario(
        "t", ontologia=ONTOLOGIA, verificador=claims(email="marta@ypf.com")
    )
    assert ctx.nombre == "marta@ypf.com"


async def test_el_thread_id_llega_al_contexto() -> None:
    """Es lo que correlaciona la acción con el razonamiento que la produjo."""
    ctx = await resolver_usuario("t", ontologia=ONTOLOGIA, verificador=claims(), thread_id="hilo-9")
    assert ctx.thread_id == "hilo-9"


async def test_se_acepta_sub_ademas_de_uid() -> None:
    """Un id token de Firebase trae `sub`; el SDK de admin agrega `uid`."""
    ctx = await resolver_usuario(
        "t", ontologia=ONTOLOGIA, verificador=lambda _: {"sub": "uid-2", CLAIM_DE_ROL: "auditor"}
    )
    assert ctx.usuario == "uid-2"


@pytest.mark.parametrize("rol", [r.id for r in ONTOLOGIA.roles])
async def test_todo_rol_del_dominio_se_resuelve(rol: str) -> None:
    """Si uno no resolviera, ese rol quedaría sin poder usar el sistema."""
    ctx = await resolver_usuario(
        "t", ontologia=ONTOLOGIA, verificador=claims(**{CLAIM_DE_ROL: rol})
    )
    assert ctx.rol == rol


# ─────────────────────────────────────────────────────────────────────────────
# Los negativos, que son los que importan
# ─────────────────────────────────────────────────────────────────────────────


async def test_sin_rol_no_se_asigna_uno_por_defecto() -> None:
    """**La decisión que más fácil se toma mal.**

    Dar `consulta` a quien no tiene claims parece prudente —es el rol más
    restringido— y convierte un problema de aprovisionamiento de identidad en un
    acceso silencioso: el usuario entra y nadie se entera de que sus claims nunca
    se configuraron.
    """
    with pytest.raises(ErrorDeIdentidad) as excinfo:
        await resolver_usuario("t", ontologia=ONTOLOGIA, verificador=lambda _: {"uid": "uid-1"})

    assert excinfo.value.status_code == 403
    assert "acceso silencioso" in excinfo.value.detail


async def test_un_rol_que_no_existe_en_la_ontologia_es_un_rechazo() -> None:
    """Puede ser un typo en los claims o un rol que se eliminó del YAML.

    En los dos casos la respuesta es 403 con el motivo, no un rol de consuelo.
    """
    with pytest.raises(ErrorDeIdentidad) as excinfo:
        await resolver_usuario(
            "t", ontologia=ONTOLOGIA, verificador=claims(**{CLAIM_DE_ROL: "gerente_general"})
        )

    assert excinfo.value.status_code == 403
    assert "no existe en el dominio" in excinfo.value.detail
    assert "inspector" in excinfo.value.detail, "el error debería listar los roles válidos"


async def test_un_token_sin_usuario_es_401_y_no_403() -> None:
    """401 y 403 se resuelven distinto: uno volviendo a autenticar, el otro
    pidiéndole a alguien que configure tus permisos."""
    with pytest.raises(ErrorDeIdentidad) as excinfo:
        await resolver_usuario(
            "t", ontologia=ONTOLOGIA, verificador=lambda _: {CLAIM_DE_ROL: "inspector"}
        )

    assert excinfo.value.status_code == 401


async def test_un_rol_vacio_se_trata_como_ausente() -> None:
    """Una cadena vacía en los claims no es un rol: es un claim mal escrito."""
    with pytest.raises(ErrorDeIdentidad) as excinfo:
        await resolver_usuario("t", ontologia=ONTOLOGIA, verificador=claims(**{CLAIM_DE_ROL: ""}))

    assert excinfo.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# El header
# ─────────────────────────────────────────────────────────────────────────────


async def test_sin_header_de_autorizacion_es_401() -> None:
    """**Un endpoint sin identidad no es uno con menos seguridad.**

    Es uno que corre con la identidad del servicio, que puede todo.
    """
    with pytest.raises(ErrorDeIdentidad) as excinfo:
        await usuario_actual(authorization="")

    assert excinfo.value.status_code == 401
    assert excinfo.value.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header", ["Bearer", "Bearer   ", "Basic dXNlcjpwYXNz", "token abc123", "abc123"]
)
async def test_un_header_mal_formado_es_401(header: str) -> None:
    with pytest.raises(ErrorDeIdentidad) as excinfo:
        await usuario_actual(authorization=header)

    assert excinfo.value.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# El catálogo de roles
# ─────────────────────────────────────────────────────────────────────────────


def test_los_roles_salen_de_la_ontologia() -> None:
    """Una lista de roles cableada en la API sería una segunda fuente de verdad."""
    assert set(roles_del_dominio(ONTOLOGIA)) == {r.id for r in ONTOLOGIA.roles}


def test_el_claim_de_rol_es_un_custom_claim() -> None:
    """Firebase Auth no sabe nada de inspectores ni de supervisores.

    Ese mapeo lo administra quien da de alta a la persona, y por eso el nombre
    del claim está en un solo lugar.
    """
    assert CLAIM_DE_ROL.startswith("synapseflow")


# ─────────────────────────────────────────────────────────────────────────────
# La aplicación cableada
# ─────────────────────────────────────────────────────────────────────────────
#
# Los tests de arriba verifican la función; estos verifican que esté enchufada.
# Una resolución de identidad correcta que ningún endpoint invoca no protege
# nada, y es una falla que no se ve leyendo `auth.py`.
#
# Se usa `ASGITransport` y no `TestClient`: la plataforma entera es async —el
# checkpointer se niega a correr sincrónicamente— y `TestClient` levanta un hilo
# con su propio portal, que no es el event loop de la sesión. Además evita una
# dependencia más (`httpx2`) para no ganar nada.


@pytest.fixture
async def cliente() -> Any:
    import httpx

    from services.api.main import app

    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://api") as cliente:
        yield cliente


@pytest.fixture
async def cliente_identificado(cliente: Any) -> Any:
    """Cliente con la identidad ya resuelta, sin pasar por Firebase."""
    from services.api.main import app

    async def _inspector() -> Any:
        return await resolver_usuario("t", ontologia=ONTOLOGIA, verificador=claims())

    app.dependency_overrides[usuario_actual] = _inspector
    yield cliente
    app.dependency_overrides.clear()


async def test_health_no_pide_identidad(cliente: Any) -> None:
    """Es la sonda de arranque de Cloud Run: si pidiera token, nunca arrancaría."""
    assert (await cliente.get("/health")).status_code == 200


async def test_health_no_toca_firestore(cliente: Any) -> None:
    """Una sonda que depende de un servicio externo reporta caído al servicio
    propio cuando el que falla es el otro, y Cloud Run reinicia contenedores
    sanos. Que este test pase sin emulador es la verificación."""
    assert (await cliente.get("/health")).json()["estado"] == "ok"


async def test_un_endpoint_con_datos_exige_identidad(cliente: Any) -> None:
    """**La verificación que importa de este commit.**

    Si el endpoint respondiera sin token, correría con la identidad del servicio
    —que puede todo— y ninguna capa posterior lo notaría.
    """
    respuesta = await cliente.get("/api/yo")

    assert respuesta.status_code == 401
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


async def test_el_error_de_identidad_viaja_como_json_y_no_como_500(
    cliente: Any,
) -> None:
    """Sin el manejador registrado, `ErrorDeIdentidad` sale por el camino de las
    excepciones no atrapadas y el 401 se convierte en un 500."""
    cuerpo = (await cliente.get("/api/yo")).json()

    assert "Authorization" in cuerpo["error"]


async def test_yo_devuelve_el_catalogo_del_rol(cliente_identificado: Any) -> None:
    """El catálogo sale del mismo `compile_tools` que ve el agente.

    Si divergieran, la consola ofrecería acciones que el modelo no tiene —o peor,
    ocultaría acciones que sí puede ejecutar.
    """
    from synapseflow.ontology import compile_tools

    cuerpo = (await cliente_identificado.get("/api/yo")).json()

    assert cuerpo["rol"] == "inspector"
    esperadas = {h.name for h in compile_tools(ONTOLOGIA, "inspector")}
    assert {a["nombre"] for a in cuerpo["acciones"]} == esperadas


async def test_el_catalogo_marca_lo_que_necesita_aprobacion(
    cliente_identificado: Any,
) -> None:
    """Es lo que le permite a la consola avisar antes, no después del gate.

    Lo esperado se deriva de la ontología y no se escribe a mano: una lista fija
    acá sería una segunda fuente de verdad que envejece con el primer cambio del
    YAML.
    """
    from synapseflow.ontology.schema import Effect

    acciones = (await cliente_identificado.get("/api/yo")).json()["acciones"]
    declaradas = {a.tool_name: a for a in ONTOLOGIA.actions}

    assert acciones, "un inspector sin acciones sería un catálogo mal filtrado"
    assert all(a["efecto"] in set(Effect) for a in acciones)
    assert all(
        a["requiere_aprobacion"] == declaradas[a["nombre"]].requires_approval for a in acciones
    )


async def test_una_negativa_de_la_gobernanza_es_403_y_no_500(cliente: Any) -> None:
    """`AutoridadInsuficienteError` hereda de `PermissionError` para esto.

    La capa que decide no sabe nada de HTTP y la que sabe de HTTP no decide, así
    que la traducción vive en un solo lugar. Sin ella, negarle algo a alguien se
    vería como una falla del servidor.
    """
    from services.api.main import app
    from synapseflow.governance.rbac import AutoridadInsuficienteError

    async def _sin_autoridad() -> Any:
        raise AutoridadInsuficienteError("un inspector no aprueba una parada")

    app.dependency_overrides[usuario_actual] = _sin_autoridad
    try:
        respuesta = await cliente.get("/api/yo")
    finally:
        app.dependency_overrides.clear()

    assert respuesta.status_code == 403
    assert "no aprueba" in respuesta.json()["error"]


async def test_los_roles_son_publicos(cliente: Any) -> None:
    """Sin identidad a propósito: es información del YAML, no de nadie."""
    respuesta = await cliente.get("/api/roles")

    assert respuesta.status_code == 200
    assert set(respuesta.json()["roles"]) == {r.id for r in ONTOLOGIA.roles}
