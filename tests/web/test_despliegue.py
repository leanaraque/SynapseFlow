"""Contrato del despliegue: lo que se puede verificar sin desplegar.

**Casi nada de esto se puede probar de verdad desde acá.** Publicar pasa en
Firebase, y los headers hay que comprobarlos con una petición al sitio servido —
así lo dice `docs/05-despliegue.md`, y así está anotado el error que este
repositorio ya cometió una vez.

Lo que sí se puede verificar es la coherencia entre las piezas: que el rewrite
apunte al servicio que el Dockerfile documenta, que el build salga donde el
hosting mira, y que las reglas de caché cubran la ruta que la gente pide de
verdad. Son los tres errores que no fallan al desplegar: dejan el sitio andando y
mal.

Ver docs/plan/fases/F7-consola.md § F7.4
"""

from __future__ import annotations

import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]

FIREBASE = json.loads((RAIZ / "firebase.json").read_text(encoding="utf-8"))
HOSTING = FIREBASE["hosting"]
DESPLIEGUE = (RAIZ / "docs" / "05-despliegue.md").read_text(encoding="utf-8")


def cache_de(source: str) -> str | None:
    for regla in HOSTING["headers"]:
        if regla["source"] == source:
            return next((h["value"] for h in regla["headers"] if h["key"] == "Cache-Control"), None)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Los headers, que es donde ya se falló una vez
# ─────────────────────────────────────────────────────────────────────────────


def test_la_raiz_tiene_su_propia_regla_de_cache() -> None:
    """**El error registrado en las convenciones, cerrado.**

    Los headers se evalúan contra la URL que pidió el navegador, no contra el
    archivo que el rewrite terminó sirviendo. Una regla escrita solo para
    `/index.html` no coincide con `/`, que es lo que pide todo el mundo, y el
    shell de la SPA queda cacheado sin que nada lo delate.
    """
    assert cache_de("/") == "no-cache"


def test_index_html_tambien_la_tiene() -> None:
    """Quien entra escribiendo la ruta completa recibe el mismo trato."""
    assert cache_de("/index.html") == "no-cache"


def test_los_assets_se_cachean_para_siempre() -> None:
    """Llevan hash en el nombre: un cambio produce otro archivo, así que no hay
    forma de servir uno viejo por error."""
    valor = cache_de("**/*.@(js|css|woff2)")

    assert valor is not None
    assert "immutable" in valor
    assert "max-age=31536000" in valor


def test_la_regla_inmutable_va_antes_que_la_del_shell() -> None:
    """Firebase aplica todas las reglas que coinciden y la última gana. Con el
    orden invertido, un `no-cache` amplio podría pisar la de los assets."""
    fuentes = [r["source"] for r in HOSTING["headers"]]

    assert fuentes.index("**/*.@(js|css|woff2)") < fuentes.index("/")


def test_ninguna_regla_de_cache_alcanza_a_la_api() -> None:
    """Cachear una respuesta del agente serviría la contestación de una pregunta
    a la siguiente — y, peor, la de un usuario a otro."""
    for regla in HOSTING["headers"]:
        assert not regla["source"].startswith("/api")


# ─────────────────────────────────────────────────────────────────────────────
# Las piezas apuntan entre sí
# ─────────────────────────────────────────────────────────────────────────────


def test_el_rewrite_de_la_api_va_a_cloud_run() -> None:
    """Si `/api/**` cayera en el rewrite general, la consola recibiría el HTML
    del index en cada llamada — con status 200."""
    api = next(r for r in HOSTING["rewrites"] if r["source"] == "/api/**")

    assert "run" in api
    assert api["run"]["serviceId"] == "synapseflow-api"


def test_el_rewrite_de_la_api_va_primero() -> None:
    """El rewrite general `**` matchea todo. Puesto antes, se traga la API."""
    fuentes = [r["source"] for r in HOSTING["rewrites"]]

    assert fuentes.index("/api/**") < fuentes.index("**")


def test_el_hosting_publica_lo_que_construye_vite() -> None:
    """Un desajuste acá publica una carpeta vacía sin que el despliegue falle."""
    assert HOSTING["public"] == "apps/web/dist"
    assert 'outDir: "dist"' in (RAIZ / "apps" / "web" / "vite.config.ts").read_text(
        encoding="utf-8"
    )


def test_la_region_del_rewrite_esta_en_el_procedimiento() -> None:
    """Desplegar en otra región deja el rewrite apuntando a un servicio que no
    existe, con la API corriendo perfecto donde nadie la busca."""
    api = next(r for r in HOSTING["rewrites"] if r["source"] == "/api/**")

    assert api["run"]["region"] in DESPLIEGUE


# ─────────────────────────────────────────────────────────────────────────────
# El procedimiento dice lo que hay que decir
# ─────────────────────────────────────────────────────────────────────────────


def test_el_procedimiento_manda_verificar_lo_servido() -> None:
    """Es la lección entera: los headers se comprueban con una petición, no
    leyendo la configuración."""
    assert "curl -sI" in DESPLIEGUE


def test_las_claves_van_por_secret_manager() -> None:
    """`--set-env-vars` las dejaría en el manifiesto del servicio, visibles para
    cualquiera con permiso de lectura en Cloud Run."""
    assert "--set-secrets" in DESPLIEGUE
    assert "--set-env-vars GOOGLE_API_KEY" not in DESPLIEGUE


def test_la_cuenta_de_servicio_no_es_owner() -> None:
    """Una cuenta con más permisos de los que usa convierte cualquier falla de
    la API en una falla de alcance ilimitado.

    Se mira lo que los comandos **hacen**, no lo que el texto menciona: la prosa
    nombra `roles/owner` justamente para decir que no se usa.
    """
    concedidos = {
        linea.strip().removeprefix('--role="').split('"')[0]
        for linea in DESPLIEGUE.splitlines()
        if linea.strip().startswith("--role=")
    }

    assert concedidos == {
        "roles/datastore.user",
        "roles/secretmanager.secretAccessor",
        # De Cloud Build, no del servicio: sin esto el primer build de cualquier
        # proyecto nuevo falla al leer su propio tarball de origen.
        "roles/cloudbuild.builds.builder",
    }, (
        f"el procedimiento concede {sorted(concedidos)}: cada rol de más amplía "
        "el alcance de cualquier falla de la API"
    )


def test_las_concesiones_de_iam_no_abren_un_prompt() -> None:
    """`gcloud` pide la condición IAM de forma interactiva si no se la pasan, y
    el comando se cuelga en cualquier script. Pasó en el despliegue real."""
    concesiones = [linea for linea in DESPLIEGUE.splitlines() if "add-iam-policy-binding" in linea]

    assert concesiones
    bloque = DESPLIEGUE
    for concesion in concesiones:
        if "projects add-iam-policy-binding" in concesion:
            # El flag va unas líneas más abajo, en el mismo bloque de comando.
            posicion = bloque.index(concesion)
            assert "--condition=None" in bloque[posicion : posicion + 400], (
                f"falta --condition=None en: {concesion.strip()}"
            )


def test_el_procedimiento_no_crea_claves_descargables() -> None:
    """La organización lo prohíbe por política, y hace bien: una clave de larga
    vida es algo que hay que rotar y que nadie rota."""
    assert "gcloud iam service-accounts keys create" not in DESPLIEGUE


def test_el_procedimiento_declara_lo_que_no_se_ejecuto() -> None:
    """**Documentar como hecho algo que no se hizo ya se cometió una vez acá.**

    Ahora el documento describe un despliegue que sí ocurrió, así que lo que hay
    que sostener es lo contrario: que siga diciendo qué quedó sin hacer. Las
    colecciones del dominio están vacías, y un procedimiento que lo omitiera
    mandaría a alguien a probar el circuito completo contra una base sin datos.
    """
    assert "## Lo que falta" in DESPLIEGUE
    assert "vacías" in DESPLIEGUE


def test_el_procedimiento_usa_la_imagen_construida() -> None:
    """`--source .` no sirve: `gcloud` busca el `Dockerfile` en la raíz del
    contexto y el de este proyecto vive en `services/api/`."""
    assert "--config cloudbuild.yaml" in DESPLIEGUE
    assert "gcloud run deploy synapseflow-api \\\n  --image " in DESPLIEGUE


def test_el_procedimiento_declara_el_proveedor_valido() -> None:
    """**El contenedor arranca igual con un valor inválido.**

    El gateway se construye perezosamente, así que un `SYNAPSEFLOW_PROVIDER`
    equivocado no rompe el despliegue: rompe la primera consulta, con el usuario
    esperando. Pasó en el despliegue real.
    """
    assert "SYNAPSEFLOW_PROVIDER=gemini" in DESPLIEGUE
    assert "SYNAPSEFLOW_PROVIDER=google" not in DESPLIEGUE


def test_se_explica_por_que_el_servicio_es_publico() -> None:
    """El documento recomendaba `--no-allow-unauthenticated` y estaba mal:
    Firebase Hosting no tiene identidad de servicio a la que darle `run.invoker`.

    Lo que protege la API es su propia validación de token. Que eso esté escrito
    importa: sin la explicación, `allUsers` parece un descuido.
    """
    assert "allUsers" in DESPLIEGUE
    assert "no tiene una identidad de servicio" in DESPLIEGUE


# ─────────────────────────────────────────────────────────────────────────────
# El límite de 60 segundos del rewrite
# ─────────────────────────────────────────────────────────────────────────────
#
# Medido contra el sistema desplegado: un recorrido completo de P-2101-A tarda
# ~52 s. Por `synapseflow-5fc52.web.app` devolvió **502 a los 60,29 s**; contra
# la URL de Cloud Run, 200.
#
# El rewrite sigue sirviendo para los endpoints cortos, pero el flujo de
# `/api/consultas` no puede depender de él.

API_TS = (RAIZ / "apps" / "web" / "src" / "api.ts").read_text(encoding="utf-8")
MAIN_PY = (RAIZ / "services" / "api" / "main.py").read_text(encoding="utf-8")


def test_el_cliente_acepta_una_url_base() -> None:
    """Sin esto, la consola pasa por el rewrite y la consulta lenta da 502."""
    assert "VITE_API_BASE" in API_TS


def test_todas_las_llamadas_pasan_por_la_url_base() -> None:
    """Una que se olvide sigue yendo por el rewrite, y falla solo con las
    consultas largas — el modo de falla más difícil de reproducir."""
    import re

    llamadas = re.findall(r"fetch\(([^,]+),", API_TS)

    assert llamadas
    for llamada in llamadas:
        assert llamada.strip().startswith("url("), f"fetch sin url(): {llamada.strip()}"


def test_la_base_vacia_deja_las_rutas_relativas() -> None:
    """En desarrollo el proxy de Vite tiene que seguir funcionando."""
    assert '(import.meta.env.VITE_API_BASE ?? "")' in API_TS


def test_la_api_permite_el_origen_de_la_consola() -> None:
    """Llamar a Cloud Run directo es una petición de otro origen: sin CORS, el
    navegador la bloquea aunque la API responda bien."""
    assert "CORSMiddleware" in MAIN_PY
    assert "https://synapseflow-5fc52.web.app" in MAIN_PY


def test_cors_no_admite_cualquier_origen() -> None:
    """**`*` deja que cualquier página monte una interfaz sobre tus datos.**

    Y con credenciales el navegador lo rechaza igual.
    """
    assert 'allow_origins=["*"]' not in MAIN_PY
    assert "allow_origins=list(ORIGENES)" in MAIN_PY


def test_el_hilo_se_expone_al_navegador() -> None:
    """Sin `expose_headers`, el navegador no deja leer `X-Thread-Id` — y sin el
    hilo la consola no puede aprobar el gate que ese recorrido abrió."""
    assert 'expose_headers=["X-Thread-Id"]' in MAIN_PY
