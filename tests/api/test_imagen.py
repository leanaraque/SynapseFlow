"""Contrato de la imagen de Cloud Run.

**Un `Dockerfile` que se degrada no falla el build.** Sigue construyendo: solo
tarda más, pesa más o expone más. Por eso las propiedades que importan se
verifican como texto, igual que se hace con el workflow de evals en F8.

Las tres que se rompen en silencio:

1. Copiar el código antes de instalar las dependencias. El build sigue andando y
   cada cambio de una línea reinstala LangChain entero.
2. Meter `.env` en una capa. La imagen arranca igual y la clave viaja adentro.
3. Cablear el puerto. Funciona local y falla en el despliegue, que es el peor
   orden posible para enterarse.

No construye la imagen: eso necesita Docker, y el objetivo de estos tests es que
la degradación se detecte en el CI, donde no hay ninguno.

Ver docs/adr/0006-cloud-run-sobre-cloud-functions.md
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
DOCKERFILE = RAIZ / "services" / "api" / "Dockerfile"
DOCKERIGNORE = RAIZ / ".dockerignore"

TEXTO = DOCKERFILE.read_text(encoding="utf-8")
IGNORADOS = DOCKERIGNORE.read_text(encoding="utf-8")

# Instrucciones, sin comentarios ni líneas vacías. La mayoría de las propiedades
# son sobre el *orden*, y los comentarios de este Dockerfile son largos.
INSTRUCCIONES = [
    linea.strip()
    for linea in TEXTO.splitlines()
    if linea.strip() and not linea.strip().startswith("#")
]


def indice_de(patron: str) -> int:
    """Posición de la primera instrucción que coincide. -1 si no hay ninguna."""
    for posicion, linea in enumerate(INSTRUCCIONES):
        if re.search(patron, linea):
            return posicion
    return -1


# ─────────────────────────────────────────────────────────────────────────────
# Existencia
# ─────────────────────────────────────────────────────────────────────────────


def test_el_dockerfile_existe() -> None:
    assert DOCKERFILE.is_file()


def test_hay_dockerignore() -> None:
    """Sin él, el contexto sube `.venv/` entero y `.env` con él."""
    assert DOCKERIGNORE.is_file()


# ─────────────────────────────────────────────────────────────────────────────
# La base y las etapas
# ─────────────────────────────────────────────────────────────────────────────


def test_la_base_es_la_declarada_en_el_adr() -> None:
    """Cambiar la base es una decisión de arquitectura, no un ajuste."""
    assert indice_de(r"^FROM python:3\.11-slim") >= 0


def test_la_construccion_es_multietapa() -> None:
    """El compilador y las cabeceras no tienen por qué llegar a producción:
    es superficie de ataque que nadie va a parchear."""
    etapas = [linea for linea in INSTRUCCIONES if linea.startswith("FROM ")]

    assert len(etapas) >= 2
    assert any(" AS " in etapa for etapa in etapas)


def test_la_etapa_final_recibe_el_entorno_ya_resuelto() -> None:
    """Copiar el venv entero es lo que deja la etapa de build afuera."""
    assert indice_de(r"^COPY --from=\w+ /opt/venv") >= 0


# ─────────────────────────────────────────────────────────────────────────────
# El orden de las capas
# ─────────────────────────────────────────────────────────────────────────────


def test_las_dependencias_se_instalan_antes_de_copiar_el_codigo() -> None:
    """**La propiedad que más fácil se pierde y que nada delata.**

    Con el código copiado primero, cada cambio de una línea invalida la capa de
    dependencias y reinstala el árbol entero: minutos por build en lugar de
    segundos. El build sigue funcionando, así que nadie lo nota hasta que el
    ciclo de trabajo ya se volvió lento.
    """
    instalacion = indice_de(r"pip install \.")
    codigo = indice_de(r"^COPY packages/")

    assert instalacion >= 0, "no se instala el paquete en ninguna etapa"
    assert codigo >= 0, "no se copia el código en ninguna etapa"
    assert instalacion < codigo, (
        "el código se copia antes de instalar las dependencias: "
        "cada cambio de una línea va a reinstalar LangChain entero"
    )


def test_el_manifiesto_se_copia_solo_para_instalar() -> None:
    """Copiar todo el repositorio para instalar tiene el mismo efecto que copiar
    el código primero, y es más difícil de ver."""
    manifiesto = indice_de(r"^COPY pyproject\.toml")
    instalacion = indice_de(r"pip install \.")

    assert 0 <= manifiesto < instalacion


# ─────────────────────────────────────────────────────────────────────────────
# Credenciales
# ─────────────────────────────────────────────────────────────────────────────


def test_no_se_copian_credenciales_a_la_imagen() -> None:
    """**En Cloud Run la identidad sale del servicio, no de un archivo.**

    Una clave dentro de la imagen es una credencial de larga vida distribuida en
    cada capa, que sobrevive aunque un paso posterior la borre.
    """
    prohibidos = (
        r"GOOGLE_APPLICATION_CREDENTIALS",
        r"service-account",
        r"COPY \.env",
        r"\.json.*credential",
    )
    # Sobre las instrucciones y no sobre el archivo: los comentarios explican
    # por qué las credenciales *no* están, y esa explicación no es el problema.
    ejecutable = "\n".join(INSTRUCCIONES)

    for patron in prohibidos:
        assert not re.search(patron, ejecutable, re.IGNORECASE), (
            f"el Dockerfile ejecuta '{patron}': las credenciales no van en la imagen"
        )


@pytest.mark.parametrize("secreto", [".env", "*.pem", "service-account*.json"])
def test_el_dockerignore_excluye_los_secretos(secreto: str) -> None:
    """El modo de falla no se ve mirando la imagen que arranca."""
    assert secreto in IGNORADOS


def test_el_dockerignore_excluye_el_entorno_virtual() -> None:
    """Son cientos de megas que se suben al daemon antes de la primera
    instrucción, para reinstalarse igual adentro."""
    assert ".venv/" in IGNORADOS


def test_la_clave_del_proveedor_se_documenta_por_secret_manager() -> None:
    """Que esté escrito en el Dockerfile importa: es donde alguien va a copiar el
    comando de despliegue, y `--set-env-vars` deja la clave en el manifiesto del
    servicio."""
    assert "--set-secrets" in TEXTO
    assert "Secret Manager" in TEXTO


# ─────────────────────────────────────────────────────────────────────────────
# El proceso
# ─────────────────────────────────────────────────────────────────────────────


def test_el_puerto_sale_de_la_variable_de_entorno() -> None:
    """**Cablear 8080 funciona local y falla en el despliegue.**

    Cloud Run inyecta `PORT` y no siempre es 8080.
    """
    arranque = INSTRUCCIONES[-1]

    assert arranque.startswith("CMD ")
    assert "${PORT}" in arranque or "$PORT" in arranque


def test_el_servicio_escucha_en_todas_las_interfaces() -> None:
    """Con `127.0.0.1` el contenedor arranca sano y Cloud Run no lo alcanza."""
    assert "--host 0.0.0.0" in TEXTO


def test_el_proceso_no_corre_como_root() -> None:
    """Da lo mismo hasta el día que una dependencia tiene un RCE."""
    usuario = indice_de(r"^USER ")
    codigo = indice_de(r"^COPY packages/")

    assert usuario >= 0, "el contenedor corre como root"
    assert usuario > codigo, "el USER va después de copiar, o el COPY falla por permisos"


def test_arranca_la_app_de_la_api() -> None:
    """Un módulo mal escrito acá no lo detecta ningún test de Python."""
    assert "services.api.main:app" in INSTRUCCIONES[-1]


def test_los_logs_no_quedan_en_buffer() -> None:
    """Sin esto se pierden cuando el contenedor termina, que es justo cuando más
    se los necesita."""
    assert "PYTHONUNBUFFERED=1" in TEXTO


def test_un_solo_worker() -> None:
    """Cloud Run escala por instancias. Dos workers compiten por la misma CPU
    asignada y empeoran la latencia en lugar de mejorarla."""
    assert "--workers" not in TEXTO


# ─────────────────────────────────────────────────────────────────────────────
# El despliegue declarado
# ─────────────────────────────────────────────────────────────────────────────


def test_el_nombre_del_servicio_coincide_con_el_rewrite() -> None:
    """`firebase.json` rutea `/api/**` a un `serviceId` concreto. Si el
    Dockerfile documenta otro nombre, el despliegue funciona y la consola sigue
    llegando a un servicio que no existe."""
    import json

    firebase = json.loads((RAIZ / "firebase.json").read_text(encoding="utf-8"))
    reescrituras = firebase["hosting"]["rewrites"]
    servicio = next(r["run"]["serviceId"] for r in reescrituras if "run" in r)

    assert servicio in TEXTO


def test_el_adr_existe_y_esta_indexado() -> None:
    """El plan pide el ADR-0006 en este commit, y un ADR fuera del índice es un
    ADR que nadie encuentra."""
    adr = RAIZ / "docs" / "adr" / "0006-cloud-run-sobre-cloud-functions.md"

    assert adr.is_file()
    assert adr.name in (RAIZ / "docs" / "adr" / "README.md").read_text(encoding="utf-8")


def test_el_adr_registra_lo_que_el_despliegue_corrigio() -> None:
    """**Una decisión que resultó mal razonada no se corrige en silencio.**

    Este test pedía que el ADR declarara la imagen como no construida. Ya se
    construyó, así que lo que hay que sostener es lo otro: el ADR recomendaba
    `--no-allow-unauthenticated` y desplegar demostró que con un rewrite de
    Hosting no funciona. Borrar esa recomendación sin dejar rastro haría que la
    próxima persona la volviera a proponer.
    """
    adr = (RAIZ / "docs" / "adr" / "0006-cloud-run-sobre-cloud-functions.md").read_text(
        encoding="utf-8"
    )

    assert "## Corrección posterior" in adr
    assert "no tiene\nuna identidad de servicio" in adr
    # Y sigue diciendo qué falta: el circuito completo necesita datos sembrados.
    assert "vacías" in adr


# ─────────────────────────────────────────────────────────────────────────────
# Lo que el runtime lee del repositorio
# ─────────────────────────────────────────────────────────────────────────────
#
# Esta sección existe porque la imagen se desplegó sin `firestore.indexes.json` y
# la primera consulta dio 500. El archivo parece configuración de despliegue —lo
# usa `firebase deploy`— pero el registry lo lee EN RUNTIME para comprobar que la
# dimensión del modelo de embeddings coincide con la del índice vectorial.
#
# El gateway falló cerrado, que es lo correcto. El problema es que un archivo
# faltante no se nota hasta que alguien pregunta algo.


def _archivos_que_lee_el_paquete() -> set[str]:
    """Archivos de la raíz del repositorio referenciados desde `packages/`.

    Se detectan por la forma en que el proyecto los resuelve: `parents[N]` sobre
    `__file__`, que sale del paquete hacia la raíz.
    """
    import re

    encontrados: set[str] = set()
    for modulo in (RAIZ / "packages").rglob("*.py"):
        texto = modulo.read_text(encoding="utf-8")
        for linea in texto.splitlines():
            if "parents[" not in linea or "Path(__file__)" not in linea:
                continue
            nombre = re.search(r'/\s*"([^"]+\.(?:json|yaml|yml|md))"', linea)
            if nombre:
                encontrados.add(nombre.group(1))
    return encontrados


def test_la_imagen_copia_todo_lo_que_el_paquete_lee_de_la_raiz() -> None:
    """**El fallo del despliegue del 2026-08-12, fijado.**

    Un archivo que el paquete resuelve contra la raíz del repositorio tiene que
    entrar en la imagen. Si no, el contenedor arranca —la sonda de salud no lo
    toca— y revienta en la primera consulta.
    """
    leidos = _archivos_que_lee_el_paquete()

    assert leidos, "no se detectó ningún archivo de la raíz; ¿cambió la forma de resolverlos?"

    faltantes = [nombre for nombre in leidos if f"COPY {nombre}" not in TEXTO]

    assert not faltantes, (
        f"el paquete lee {faltantes} desde la raíz y el Dockerfile no los copia. "
        "El contenedor va a arrancar igual y fallar en la primera consulta."
    )


def test_el_indice_vectorial_esta_en_la_imagen() -> None:
    """El caso concreto, escrito aparte para que el motivo quede a la vista."""
    assert "COPY firestore.indexes.json" in TEXTO
