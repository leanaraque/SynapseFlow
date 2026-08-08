"""El contrato de eventos entre la API y la consola.

**Es un protocolo de texto entre dos lenguajes.** Nada obliga a que
`services/api/streaming.py` y `apps/web/src/sse.ts` usen los mismos nombres, y si
uno cambia, el otro sigue compilando: la consola escucha un evento que ya no
llega y muestra una respuesta vacía sin un solo error.

Este test es la única cosa que ata las dos puntas. Corre en Python porque el CI
de Python ya existe y no necesita node.

Ver docs/plan/fases/F7-consola.md § F7.2
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.api import streaming

RAIZ = Path(__file__).resolve().parents[2]
SSE_TS = (RAIZ / "apps" / "web" / "src" / "sse.ts").read_text(encoding="utf-8")
CHAT_TSX = (RAIZ / "apps" / "web" / "src" / "Chat.tsx").read_text(encoding="utf-8")

# `nombreEnCamello: "valor_en_serpiente"` dentro del objeto TIPOS.
TIPOS_TS = dict(re.findall(r'(\w+):\s*"([^"]+)"', SSE_TS.split("TIPOS = {")[1].split("}")[0]))

TIPOS_PY = {
    streaming.TOKEN,
    streaming.HERRAMIENTA_INICIO,
    streaming.HERRAMIENTA_FIN,
    streaming.CITAS,
    streaming.APROBACION_REQUERIDA,
    streaming.ERROR,
    streaming.FIN,
}


def test_el_cliente_conoce_todos_los_eventos_del_servidor() -> None:
    """**El modo de falla es silencioso en las dos direcciones.**

    Un evento que el servidor emite y el cliente no conoce se descarta sin ruido.
    """
    assert set(TIPOS_TS.values()) == TIPOS_PY


def test_no_hay_eventos_inventados_del_lado_del_cliente() -> None:
    """Escuchar algo que nadie emite deja una rama de la consola sin ejecutar
    nunca, y por lo tanto sin probar."""
    assert set(TIPOS_TS.values()) <= TIPOS_PY


def test_los_terminales_coinciden() -> None:
    """La consola decide cuándo dejar de esperar por estos dos. Si divergieran,
    se quedaría escuchando un flujo que ya cerró."""
    terminales = re.search(r"TERMINALES:\s*readonly string\[\]\s*=\s*\[([^\]]+)\]", SSE_TS)

    assert terminales is not None
    nombres = re.findall(r"TIPOS\.(\w+)", terminales.group(1))
    assert {TIPOS_TS[n] for n in nombres} == set(streaming.TERMINALES)


@pytest.mark.parametrize("tipo", sorted(TIPOS_PY))
def test_el_chat_hace_algo_con_cada_evento(tipo: str) -> None:
    """Un evento que el servidor emite y el chat no dibuja es trabajo que el
    usuario pagó y no ve."""
    clave = next(k for k, v in TIPOS_TS.items() if v == tipo)

    assert f"TIPOS.{clave}" in CHAT_TSX, (
        f"Chat.tsx no maneja el evento '{tipo}'. El servidor lo emite y la "
        "consola lo descarta en silencio."
    )


def test_el_chat_no_arma_las_citas_desde_el_texto_del_modelo() -> None:
    """**Es la misma razón por la que el verificador contrasta contra lo
    recuperado.**

    Las citas llegan en su propio evento, derivadas del `artifact` de las
    herramientas. Extraerlas del texto con una expresión regular mostraría como
    fuente cualquier cosa con forma de cita que el modelo haya escrito.
    """
    assert "TIPOS.citas" in CHAT_TSX
    assert not re.search(r"match\(.*§", CHAT_TSX), "el chat parsea citas del texto"


def test_el_chat_muestra_la_vigencia_de_cada_cita() -> None:
    """El corpus tiene un procedimiento derogado que contradice al vigente, a
    propósito. Citarlo sin decirlo es peor que no citar nada."""
    assert "vigencia" in CHAT_TSX


def test_el_chat_no_ofrece_aprobar_la_accion_propuesta() -> None:
    """La decisión se toma en la bandeja, que es donde vive la validación de
    autoridad — y donde puede entrar alguien que no es quien preguntó.

    Un botón de aprobar acá le ofrecería al proponente aprobarse a sí mismo.
    """
    propuesta = CHAT_TSX.split("function Propuesta")[1]

    assert "decidir(" not in propuesta
    assert "<button" not in propuesta
