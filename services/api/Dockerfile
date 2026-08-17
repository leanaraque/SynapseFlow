# SynapseFlow · imagen de la API para Cloud Run
#
# Se construye desde la RAÍZ del repositorio, no desde este directorio:
#
#     docker build -f services/api/Dockerfile -t synapseflow-api .
#
# El contexto tiene que incluir `packages/` y `pyproject.toml`, que están fuera
# de `services/api/`. Un contexto acotado a este directorio no puede instalar el
# paquete, y es el error que se comete primero.
#
# ## Por qué dos etapas
#
# El árbol de dependencias del proyecto es grande —LangChain, LangGraph, los SDK
# de proveedores, Firestore— y compilarlo requiere herramientas que la imagen
# final no necesita. Separarlo deja fuera el compilador y las cabeceras: menos
# superficie que parchear y una imagen que arranca antes.
#
# ## Por qué las dependencias se instalan antes que el código
#
# Docker cachea por capa. Con el código copiado primero, cada cambio de una línea
# invalidaría la capa de dependencias y reinstalaría el árbol entero: minutos por
# build en lugar de segundos. Por eso se copia `pyproject.toml` solo, se instalan
# las dependencias, y recién después entra el código.
#
# ## Sin credenciales adentro
#
# No hay archivo de service account ni `GOOGLE_APPLICATION_CREDENTIALS`: en Cloud
# Run la autenticación sale de la identidad del servicio (ADC). Una clave dentro
# de la imagen es una credencial de larga vida distribuida en cada capa, y la
# organización prohíbe crearlas — con razón.
#
# Las claves de proveedores se inyectan por **Secret Manager**, montadas como
# variables de entorno por Cloud Run:
#
#     gcloud run deploy synapseflow-api \
#       --source . \
#       --region southamerica-east1 \
#       --service-account synapseflow-api@PROJECT.iam.gserviceaccount.com \
#       --set-secrets GOOGLE_API_KEY=synapseflow-google-api-key:latest
#
# Nunca `--set-env-vars GOOGLE_API_KEY=...`: eso deja la clave en el manifiesto
# del servicio, visible para cualquiera con permiso de lectura en Cloud Run.
#
# Ver docs/adr/0006-cloud-run-sobre-cloud-functions.md

# ─────────────────────────────────────────────────────────────────────────────
# Etapa 1 · dependencias
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS dependencias

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Un venv propio y no el Python del sistema: se copia entero a la etapa final
# con un solo `COPY`, sin rastrear qué quedó en dist-packages.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Solo el manifiesto. El código todavía no: es lo que hace que un cambio de una
# línea no reinstale LangChain.
COPY pyproject.toml README.md LICENSE ./
# `hatchling` exige que el paquete exista para resolver la metadata, así que se
# crea el árbol vacío. Instalar las dependencias sin el código es justamente el
# punto de este paso.
RUN mkdir -p packages/synapseflow && touch packages/synapseflow/__init__.py
RUN pip install --upgrade pip && pip install .

# ─────────────────────────────────────────────────────────────────────────────
# Etapa 2 · imagen final
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# `PYTHONDONTWRITEBYTECODE`: el filesystem de Cloud Run es efímero y los .pyc no
# sobreviven al contenedor. `PYTHONUNBUFFERED`: sin esto, los logs se pierden
# cuando el contenedor termina — justo cuando más se los necesita.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/packages:/app"

COPY --from=dependencias /opt/venv /opt/venv

WORKDIR /app

# El código, ya con las dependencias instaladas. `data/` entra porque el corpus
# de normativa es fuente versionada, no un derivado: la ingesta lo lee.
COPY packages/ ./packages/
COPY services/ ./services/
COPY data/ ./data/

# `firestore.indexes.json` NO es solo configuración de despliegue: el registry lo
# lee EN RUNTIME para comprobar que la dimensión del modelo de embeddings
# coincide con la del índice vectorial, y esa comprobación corre al construir el
# gateway.
#
# Sin este archivo la verificación no puede hacerse y el gateway **falla
# cerrado**, que es lo correcto — pero convierte un archivo faltante en un 500 en
# la primera consulta. Pasó en el despliegue del 2026-08-12.
COPY firestore.indexes.json ./

# Usuario sin privilegios. Cloud Run no lo exige y da lo mismo hasta el día que
# una dependencia tiene un RCE, que es el día en que deja de dar lo mismo.
RUN useradd --create-home --uid 1000 synapseflow && chown -R synapseflow:synapseflow /app
USER synapseflow

# Cloud Run inyecta PORT y no siempre es 8080. Cablearlo funciona en las pruebas
# locales y falla en el despliegue, que es el peor orden posible para enterarse.
ENV PORT=8080
EXPOSE 8080

# Un worker por contenedor: Cloud Run escala por instancias, no por procesos, y
# varios workers compiten por la misma CPU asignada. La concurrencia la da el
# event loop, que es lo correcto para un servicio que pasa el tiempo esperando
# I/O de un LLM.
CMD exec uvicorn services.api.main:app --host 0.0.0.0 --port ${PORT}
