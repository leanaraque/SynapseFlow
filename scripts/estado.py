"""Detector de estado del plan de trabajo.

    python -m scripts.estado

Este script existe para que nadie tenga que *creer* en qué fase está el
proyecto. Inspecciona el repositorio y lo determina.

## Por qué no una lista de casillas

Una checklist en un archivo Markdown se desincroniza: alguien implementa algo y
se olvida de tildar, o tilda algo que no terminó. Entonces el siguiente que llega
—una persona nueva, o un agente sin contexto previo— trabaja sobre una mentira.

Acá cada commit del plan declara una **señal**: un hecho comprobable en el
código. Si la señal está, el commit está hecho. No hay estado que mantener a
mano, y por lo tanto no hay estado que se pueda desincronizar.

## Cómo agregar un commit al plan

1. Sumar una entrada a COMMITS con su id, fase, título y señal.
2. Documentar el detalle en docs/plan/fases/F<n>-*.md, bajo el mismo id.

La señal debe ser lo más específica posible: que exista el archivo no alcanza si
el commit consiste en agregarle una función.
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# Salida por consola
#
# Este script es el primer comando que corre alguien que retoma el proyecto. Si
# se cae, esa persona queda sin punto de entrada.
#
# La consola de Windows usa cp1252 por defecto y no puede codificar los
# caracteres de dibujo: sin esto, `python -m scripts.estado` termina en
# UnicodeEncodeError en lugar de mostrar el estado. Se intenta pasar la salida a
# UTF-8 y, si no se puede, se cae a símbolos ASCII.
# ─────────────────────────────────────────────────────────────────────────────


def _consola_soporta_unicode() -> bool:
    # Si la salida no se puede reconfigurar —está redirigida a un archivo, o es
    # un stdout sustituido por un test— se sigue con lo que haya.
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    return "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()


UNICODE = _consola_soporta_unicode()

SEP = ("─" if UNICODE else "-") * 74
BULLET = "·" if UNICODE else "*"
MARCA_HECHO = "✅" if UNICODE else "[OK]"
MARCA_PENDIENTE = "○ " if UNICODE else "[  ]"
MARCA_ITEM = "✓" if UNICODE else "x"


# ─────────────────────────────────────────────────────────────────────────────
# Señales
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Senal:
    """Un hecho comprobable que indica que un commit está hecho.

    `archivo` es relativo a la raíz del repositorio. `contiene` es una lista de
    fragmentos de texto que deben aparecer todos en ese archivo; si está vacía,
    alcanza con que el archivo exista.
    """

    archivo: str
    contiene: tuple[str, ...] = ()

    def cumplida(self) -> bool:
        ruta = RAIZ / self.archivo
        if not ruta.is_file():
            return False
        if not self.contiene:
            return True
        try:
            texto = ruta.read_text(encoding="utf-8")
        except OSError:
            return False
        return all(fragmento in texto for fragmento in self.contiene)

    def describir_falta(self) -> str:
        ruta = RAIZ / self.archivo
        if not ruta.is_file():
            return f"falta el archivo {self.archivo}"
        texto = ruta.read_text(encoding="utf-8", errors="replace")
        faltantes = [f for f in self.contiene if f not in texto]
        return f"{self.archivo} no contiene: {', '.join(repr(f) for f in faltantes)}"


@dataclass(frozen=True)
class Commit:
    id: str
    fase: str
    titulo: str
    senales: tuple[Senal, ...]
    verificar: str = ""
    notas: str = ""

    def hecho(self) -> bool:
        return all(s.cumplida() for s in self.senales)


@dataclass(frozen=True)
class Fase:
    id: str
    nombre: str
    documento: str
    depende_de: tuple[str, ...] = ()
    compromiso: str = ""
    bloqueo_externo: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# El plan
#
# El orden de esta lista ES el orden de ejecución. No reordenar sin actualizar
# también las dependencias declaradas en FASES.
# ─────────────────────────────────────────────────────────────────────────────

FASES: dict[str, Fase] = {
    "F0": Fase(
        "F0",
        "Datos sintéticos y corpus",
        "docs/plan/fases/F0-datos.md",
        compromiso="Ninguno directo; habilita F2 y F3",
    ),
    "F1": Fase(
        "F1",
        "Gateway de LLM",
        "docs/plan/fases/F1-gateway.md",
        compromiso="Precondición de los compromisos 4 y 5",
        bloqueo_externo="GOOGLE_API_KEY solo para el test en vivo; el resto corre con el modelo falso",
    ),
    "F2": Fase(
        "F2",
        "Acciones del dominio",
        "docs/plan/fases/F2-dominio.md",
        depende_de=("F0",),
        compromiso="Cierra el compromiso 3: el modelo no calcula números",
    ),
    "F3": Fase(
        "F3",
        "RAG con citas",
        "docs/plan/fases/F3-rag.md",
        depende_de=("F0", "F1"),
        compromiso="Cierra el compromiso 4: sin cita no hay respuesta",
    ),
    "F4": Fase(
        "F4",
        "Gobernanza como middleware",
        "docs/plan/fases/F4-gobernanza.md",
        depende_de=("F1",),
        compromiso="Cierra el compromiso 5: los datos sensibles no salen",
    ),
    "F5": Fase(
        "F5",
        "Grafo de agentes",
        "docs/plan/fases/F5-grafo.md",
        depende_de=("F1", "F2", "F3", "F4"),
        compromiso="Cierra el compromiso 2 en ejecución real",
    ),
    "F6": Fase(
        "F6",
        "API en Cloud Run",
        "docs/plan/fases/F6-api.md",
        depende_de=("F5",),
        bloqueo_externo="Plan Blaze para desplegar",
    ),
    "F7": Fase(
        "F7",
        "Consola web",
        "docs/plan/fases/F7-consola.md",
        depende_de=("F6",),
        bloqueo_externo="Plan Blaze para desplegar",
    ),
    "F8": Fase(
        "F8",
        "Evaluación y CI de regresión",
        "docs/plan/fases/F8-evals.md",
        depende_de=("F5",),
    ),
}

P = "packages/synapseflow"

COMMITS: list[Commit] = [
    # ── F0 · Datos sintéticos y corpus ──────────────────────────────────────
    Commit(
        "F0.1",
        "F0",
        "Generador de datos estructurados del dominio",
        (
            Senal(
                "scripts/generar_datos.py",
                (
                    "def generar_instalaciones",
                    "def generar_activos",
                    "def generar_inspecciones",
                    "def generar_ordenes",
                ),
            ),
        ),
        verificar="python -m scripts.generar_datos --salida data/generado --semilla 42",
        notas="Semilla fija: dos corridas deben producir exactamente lo mismo.",
    ),
    Commit(
        "F0.2",
        "F0",
        "Corpus de normativa reescrito",
        (Senal("data/corpus/API-570.md"), Senal("data/corpus/PROC-INT-014.md")),
        verificar="ls data/corpus/*.md",
        notas="Parafrasear estructura y criterio de API; NUNCA copiar texto con derechos.",
    ),
    Commit(
        "F0.3",
        "F0",
        "Carga idempotente a Firestore",
        (Senal("scripts/seed.py", ("--dry-run", "async def cargar")),),
        verificar="python -m scripts.seed --dry-run",
        notas="Correrlo dos veces debe dejar la misma cantidad de documentos.",
    ),
    Commit(
        "F0.4",
        "F0",
        "Tests de coherencia de los datos generados",
        (
            Senal(
                "tests/datos/test_coherencia.py",
                ("def test_hay_un_activo_bajo_t_min", "def test_corrosion_es_monotona"),
            ),
        ),
        verificar="pytest tests/datos -v",
        notas="Sin un activo bajo t_min no se puede ejercitar el recorrido completo.",
    ),
    # ── F1 · Gateway de LLM ─────────────────────────────────────────────────
    Commit(
        "F1.1",
        "F1",
        "Registry de modelos y precios",
        (Senal(f"{P}/llm/registry.py", ("class ModelSpec", "def resolver", "def costo_de")),),
        verificar="pytest tests/llm/test_registry.py -v",
        notas="Lee models.yaml, que ya existe. Valida la dimensión de embeddings contra firestore.indexes.json.",
    ),
    Commit(
        "F1.2",
        "F1",
        "Modelo falso determinístico para tests",
        (Senal(f"{P}/llm/fake.py", ("class FakeChatModel",)),),
        verificar="pytest tests/llm/test_fake.py -v",
        notas="Sin esto, ninguna fase posterior se puede testear sin gastar cuota ni red.",
    ),
    Commit(
        "F1.3",
        "F1",
        "Gateway con adapters por proveedor",
        (Senal(f"{P}/llm/gateway.py", ("class Gateway", "def chat", "def embeddings")),),
        verificar="pytest tests/llm/test_gateway.py -v",
        notas="El código pide un PERFIL (router/synthesis/verifier/embedding), nunca un nombre de modelo.",
    ),
    Commit(
        "F1.4",
        "F1",
        "Contabilidad de tokens y costo",
        (Senal(f"{P}/llm/callbacks.py", ("class ContabilidadDeCosto",)),),
        verificar="pytest tests/llm/test_callbacks.py -v",
        notas="BaseCallbackHandler propio; escribe a la colección llm_usage.",
    ),
    Commit(
        "F1.5",
        "F1",
        "Test estructural de la frontera de datos",
        (
            Senal(
                "tests/llm/test_frontera.py",
                ("def test_ningun_modulo_externo_importa_un_chatmodel",),
            ),
        ),
        verificar="pytest tests/llm/test_frontera.py -v",
        notas="Recorre los imports del paquete. Es lo que hace verificable la garantía del compromiso 5.",
    ),
    # ── F2 · Acciones del dominio ───────────────────────────────────────────
    Commit(
        "F2.1",
        "F2",
        "Repositorio de acceso a Firestore por entidad",
        (Senal(f"{P}/domain/repository.py", ("class RepositorioDominio",)),),
        verificar="pytest tests/domain/test_repository.py -v",
    ),
    Commit(
        "F2.2",
        "F2",
        "Implementación de las cuatro acciones de lectura",
        (
            Senal(
                f"{P}/domain/lecturas.py",
                (
                    '@implements("consultar_activo")',
                    '@implements("listar_activos")',
                    '@implements("historial_inspecciones")',
                    '@implements("buscar_normativa")',
                ),
            ),
        ),
        verificar="pytest tests/domain/test_lecturas.py -v",
        notas="buscar_normativa puede devolver vacío hasta F3; la firma y el contrato sí quedan.",
    ),
    Commit(
        "F2.3",
        "F2",
        "Cálculo determinístico de vida remanente",
        (
            Senal(
                f"{P}/domain/calculos.py",
                (
                    '@implements("calcular_vida_remanente")',
                    "def velocidad_de_corrosion",
                    "def vida_remanente",
                ),
            ),
            Senal(
                "tests/domain/test_calculos.py",
                (
                    "def test_una_sola_medicion_no_permite_calcular",
                    "def test_espesor_creciente_es_sospechoso",
                    "def test_vida_remanente_negativa",
                ),
            ),
        ),
        verificar="pytest tests/domain/test_calculos.py -v",
        notas="CIERRA EL COMPROMISO 3. El modelo recibe el número ya calculado, no lo estima.",
    ),
    Commit(
        "F2.4",
        "F2",
        "Implementación de las cuatro acciones de escritura",
        (
            Senal(
                f"{P}/domain/escrituras.py",
                (
                    '@implements("registrar_borrador_ot")',
                    '@implements("emitir_orden_trabajo")',
                    '@implements("solicitar_parada_equipo")',
                    '@implements("reclasificar_criticidad")',
                ),
            ),
        ),
        verificar="pytest tests/domain/test_escrituras.py -v",
        notas="Ninguna debe poder invocarse sin contexto de usuario.",
    ),
    Commit(
        "F2.5",
        "F2",
        "El catálogo completo compila",
        (Senal("tests/domain/test_catalogo.py", ("def test_las_nueve_acciones_compilan",)),),
        verificar="pytest tests/domain/test_catalogo.py -v",
        notas="Hoy compile_tools lanza CompilationError por acciones sin implementación. Acá deja de hacerlo.",
    ),
    # ── F3 · RAG con citas ──────────────────────────────────────────────────
    Commit(
        "F3.1",
        "F3",
        "Ingesta y troceado del corpus",
        (Senal(f"{P}/rag/ingesta.py", ("def trocear", "async def indexar")),),
        verificar="pytest tests/rag/test_ingesta.py -v",
        notas="Cada fragmento conserva doc_id, seccion y vigencia: sin eso no hay cita posible.",
    ),
    Commit(
        "F3.2",
        "F3",
        "Retriever híbrido vectorial + léxico",
        (Senal(f"{P}/rag/retrievers.py", ("class BM25Retriever", "def construir_retriever")),),
        verificar="pytest tests/rag/test_retrievers.py -v",
        notas="BM25 propio sobre rank_bm25 para no arrastrar langchain-community. Ensemble viene de langchain-classic.",
    ),
    Commit(
        "F3.3",
        "F3",
        "Extracción y validación de citas",
        (Senal(f"{P}/rag/citas.py", ("class Cita", "def extraer_citas", "def validar_citas")),),
        verificar="pytest tests/rag/test_citas.py -v",
    ),
    Commit(
        "F3.4",
        "F3",
        "Verificador de fundamento",
        (
            Senal(
                f"{P}/rag/fundamento.py", ("class VerificadorDeFundamento", "async def verificar")
            ),
        ),
        verificar="pytest tests/rag/test_fundamento.py -v",
        notas="CIERRA EL COMPROMISO 4. Si una afirmación no tiene respaldo, la respuesta no se emite.",
    ),
    Commit(
        "F3.5",
        "F3",
        "Tests de rechazo y de normativa derogada",
        (
            Senal(
                "tests/rag/test_rechazo.py",
                (
                    "def test_se_niega_cuando_no_hay_fundamento",
                    "def test_no_cita_normativa_derogada",
                ),
            ),
        ),
        verificar="pytest tests/rag/test_rechazo.py -v",
        notas="Negarse a responder es una MÉTRICA DE ÉXITO, no un fallo.",
    ),
    # ── F4 · Gobernanza ─────────────────────────────────────────────────────
    Commit(
        "F4.1",
        "F4",
        "Contexto de ejecución con el rol del usuario",
        (Senal(f"{P}/governance/rbac.py", ("class ExecutionContext",)),),
        verificar="pytest tests/governance/test_rbac.py -v",
        notas="El agente hereda los permisos del usuario, NUNCA los de la cuenta de servicio.",
    ),
    Commit(
        "F4.2",
        "F4",
        "Tokenización y rehidratación de datos personales",
        (Senal(f"{P}/governance/pii.py", ("def detectar_legajos", "class Tokenizador")),),
        verificar="pytest tests/governance/test_pii.py -v",
        notas="CIERRA EL COMPROMISO 5. Tokens estables: el mismo legajo produce siempre el mismo símbolo.",
    ),
    Commit(
        "F4.3",
        "F4",
        "Log de auditoría inmutable",
        (Senal(f"{P}/governance/auditoria.py", ("async def registrar",)),),
        verificar="pytest tests/governance/test_auditoria.py -v",
        notas="Guarda thread_id y checkpoint_id: permiten reconstruir el razonamiento, no solo el hecho.",
    ),
    Commit(
        "F4.4",
        "F4",
        "Enforcement de la política zero-training",
        (Senal(f"{P}/governance/politica.py", ("def exigir_zero_training",)),),
        verificar="pytest tests/governance/test_politica.py -v",
        notas="Si el proveedor no la garantiza, la llamada FALLA en vez de degradar en silencio.",
    ),
    Commit(
        "F4.5",
        "F4",
        "Ensamblado del pipeline de middleware",
        (Senal(f"{P}/governance/middleware.py", ("def construir_middleware",)),),
        verificar="pytest tests/governance/test_middleware.py -v",
        notas="Sobre AgentMiddleware de LangChain 1.x. Ver docs/plan/00-convenciones.md § API verificada.",
    ),
    Commit(
        "F4.6",
        "F4",
        "Test de la frontera de datos de punta a punta",
        (
            Senal(
                "tests/governance/test_frontera_datos.py",
                ("def test_un_campo_restricted_nunca_llega_al_proveedor",),
            ),
        ),
        verificar="pytest tests/governance/test_frontera_datos.py -v",
    ),
    # ── F5 · Grafo de agentes ───────────────────────────────────────────────
    Commit(
        "F5.1",
        "F5",
        "Estado del agente, tipado y estrecho",
        (Senal(f"{P}/agents/state.py", ("class AgentState",)),),
        verificar="pytest tests/agents/test_state.py -v",
        notas="Todo lo que viaje por el estado debe ser serializable. Nada de clientes ni conexiones.",
    ),
    Commit(
        "F5.2",
        "F5",
        "Agentes especialistas",
        (
            Senal(
                f"{P}/agents/especialistas.py",
                ("def agente_normativa", "def agente_datos", "def agente_calculo"),
            ),
        ),
        verificar="pytest tests/agents/test_especialistas.py -v",
    ),
    Commit(
        "F5.3",
        "F5",
        "Nodo verificador previo a la emisión",
        (Senal(f"{P}/agents/verificador.py", ("async def nodo_verificador",)),),
        verificar="pytest tests/agents/test_verificador.py -v",
    ),
    Commit(
        "F5.4",
        "F5",
        "Supervisor que rutea y planifica",
        (Senal(f"{P}/agents/supervisor.py", ("async def nodo_supervisor",)),),
        verificar="pytest tests/agents/test_supervisor.py -v",
        notas="Usa el perfil 'router' del gateway: es la llamada más frecuente y debe ser la más barata.",
    ),
    Commit(
        "F5.5",
        "F5",
        "Ensamblado del grafo con gates desde la ontología",
        (Senal(f"{P}/agents/graph.py", ("def construir_grafo", "interrupt_config")),),
        verificar="pytest tests/agents/test_graph.py -v",
        notas="CIERRA EL COMPROMISO 2. Los gates salen de interrupt_config(ontology, role), no se escriben a mano.",
    ),
    Commit(
        "F5.6",
        "F5",
        "Test estructural de gates y recorrido completo",
        (
            Senal(
                "tests/agents/test_gates_estructural.py",
                ("def test_ninguna_accion_irreversible_es_alcanzable_sin_gate",),
            ),
            Senal(
                "tests/agents/test_recorrido_completo.py",
                ("def test_caso_p2101a_llega_al_gate_de_parada",),
            ),
        ),
        verificar="pytest tests/agents -v",
        notas="HITO PRINCIPAL DEL PROYECTO. Acá el sistema hace el recorrido de punta a punta.",
    ),
    # ── F8 · Evals ──────────────────────────────────────────────────────────
    Commit(
        "F8.1",
        "F8",
        "Golden dataset del dominio",
        (Senal("evals/datasets/normativa.jsonl"), Senal("evals/datasets/datos.jsonl")),
        verificar="wc -l evals/datasets/*.jsonl",
        notas="Incluir casos SIN respuesta en el corpus: miden la corrección del rechazo.",
    ),
    Commit(
        "F8.2",
        "F8",
        "Evaluadores propios",
        (
            Senal("evals/evaluadores/fidelidad.py"),
            Senal("evals/evaluadores/citas.py"),
            Senal("evals/evaluadores/rechazo.py"),
        ),
        verificar="pytest tests/evals -v",
    ),
    Commit(
        "F8.3",
        "F8",
        "Corredor de evaluación con línea base",
        (Senal("evals/run.py", ("--comparar-linea-base",)),),
        verificar="python -m evals.run --suite normativa",
    ),
    Commit(
        "F8.4",
        "F8",
        "CI que bloquea el merge ante regresión",
        (Senal(".github/workflows/evals.yml", ("comparar-linea-base",)),),
        verificar="cat .github/workflows/evals.yml",
    ),
    # ── F6 · API ────────────────────────────────────────────────────────────
    Commit(
        "F6.1",
        "F6",
        "FastAPI con identidad de Firebase",
        (
            Senal("services/api/main.py", ("app = FastAPI",)),
            Senal("services/api/auth.py", ("async def resolver_usuario",)),
        ),
        verificar="pytest tests/api/test_auth.py -v",
        notas="El rol sale del token de Firebase Auth y se resuelve contra la ontología.",
    ),
    Commit(
        "F6.2",
        "F6",
        "Streaming del grafo por SSE",
        (Senal("services/api/streaming.py", ("astream_events",)),),
        verificar="pytest tests/api/test_streaming.py -v",
    ),
    Commit(
        "F6.3",
        "F6",
        "Endpoints de aprobación",
        (Senal("services/api/aprobaciones.py", ("Command(resume=",)),),
        verificar="pytest tests/api/test_aprobaciones.py -v",
        notas="Validar approver_roles Y que el aprobador no sea el proponente.",
    ),
    Commit(
        "F6.4",
        "F6",
        "Dockerfile y despliegue a Cloud Run",
        (
            Senal("services/api/Dockerfile"),
            Senal("docs/adr/0006-cloud-run-sobre-cloud-functions.md"),
        ),
        verificar="docker build -t synapseflow-api services/api",
        notas="Requiere plan Blaze. Escribir el ADR-0006 en este commit.",
    ),
    # ── F7 · Consola ────────────────────────────────────────────────────────
    Commit(
        "F7.1",
        "F7",
        "Scaffold de la consola con Vite",
        (Senal("apps/web/package.json"), Senal("apps/web/index.html")),
        verificar="cd apps/web && npm install && npm run build",
    ),
    Commit(
        "F7.2",
        "F7",
        "Chat con streaming y citas desplegables",
        (Senal("apps/web/src/Chat.tsx"),),
        verificar="cd apps/web && npm run build",
    ),
    Commit(
        "F7.3",
        "F7",
        "Bandeja de aprobaciones",
        (Senal("apps/web/src/Aprobaciones.tsx"),),
        verificar="cd apps/web && npm run build",
    ),
    Commit(
        "F7.4",
        "F7",
        "Despliegue a Firebase Hosting",
        # La señal era `apps/web/dist/index.html`, y estaba mal: `dist/` está en
        # .gitignore, así que correr `npm run build` una vez daba el commit por
        # hecho sin que nada se hubiera desplegado — y un clon limpio lo reportaba
        # pendiente otra vez. Un detector que depende de un artefacto ignorado
        # dice cosas distintas en dos máquinas con el mismo código.
        #
        # El despliegue en sí no se puede derivar del repositorio: pasa en
        # Firebase. Lo que sí se versiona es el procedimiento, con los headers a
        # comprobar después de publicar.
        (Senal("docs/05-despliegue.md"),),
        verificar="firebase deploy --only hosting && curl -I <url>",
        notas="Requiere plan Blaze. Verificar los headers SERVIDOS, no los escritos.",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Compromisos de diseño
#
# Se derivan del avance: un compromiso está cumplido cuando su commit lo está.
# ─────────────────────────────────────────────────────────────────────────────

COMPROMISOS = [
    ("1", "La ontología es declarativa, no código", None),
    ("2", "La reversibilidad es un atributo del dominio", "F5.5"),
    ("3", "El modelo no calcula números", "F2.3"),
    ("4", "Sin cita no hay respuesta", "F3.4"),
    ("5", "Los datos sensibles no salen del perímetro", "F4.2"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Presentación
# ─────────────────────────────────────────────────────────────────────────────


def _por_id(commit_id: str) -> Commit | None:
    return next((c for c in COMMITS if c.id == commit_id), None)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    detallado = "--detallado" in args or "-d" in args

    hechos = [c for c in COMMITS if c.hecho()]
    pendientes = [c for c in COMMITS if not c.hecho()]

    print()
    print(f"SynapseFlow {BULLET} Estado del plan")
    print(SEP)

    if not pendientes:
        print()
        print("  Todas las fases completas. El plan terminó.")
        print()
        return 0

    siguiente = pendientes[0]
    fase = FASES[siguiente.fase]

    # Un commit hecho después de uno pendiente indica que se salteó el orden.
    fuera_de_orden = [c.id for c in COMMITS[COMMITS.index(siguiente) + 1 :] if c.hecho()]

    print(f"  Progreso        {len(hechos)} de {len(COMMITS)} commits")
    print(f"  Fase actual     {fase.id} {BULLET} {fase.nombre}")
    if fase.depende_de:
        print(f"  Depende de      {', '.join(fase.depende_de)}")
    if fase.compromiso:
        print(f"  Aporta          {fase.compromiso}")
    if fase.bloqueo_externo:
        print(f"  Bloqueo         {fase.bloqueo_externo}")
    print()
    print(SEP)
    print("  SIGUIENTE COMMIT")
    print(SEP)
    print(f"  {siguiente.id}  {siguiente.titulo}")
    print()
    print(f"  Instrucciones   {fase.documento} § {siguiente.id}")
    if siguiente.verificar:
        print(f"  Verificar con   {siguiente.verificar}")
    if siguiente.notas:
        print(f"  Atención        {siguiente.notas}")
    print()
    print("  Falta para darlo por hecho:")
    for s in siguiente.senales:
        if not s.cumplida():
            print(f"    {BULLET} {s.describir_falta()}")
    print()

    if fuera_de_orden:
        print(SEP)
        print("  AVISO: hay commits posteriores ya hechos, fuera de orden:")
        print(f"    {', '.join(fuera_de_orden)}")
        print("  Revisá que las dependencias de la fase estén realmente cubiertas.")
        print()

    print(SEP)
    print("  COMPROMISOS DE DISEÑO")
    print(SEP)
    for numero, texto, commit_id in COMPROMISOS:
        if commit_id is None:
            marca = MARCA_HECHO
        else:
            c = _por_id(commit_id)
            marca = MARCA_HECHO if c and c.hecho() else MARCA_PENDIENTE
        pendiente = "" if marca == MARCA_HECHO else f"  (lo cierra {commit_id})"
        print(f"  {marca} {numero}. {texto}{pendiente}")
    print()

    if detallado:
        print(SEP)
        print("  TODAS LAS FASES")
        print(SEP)
        for fid, f in FASES.items():
            de_la_fase = [c for c in COMMITS if c.fase == fid]
            listos = sum(1 for c in de_la_fase if c.hecho())
            estado = "completa" if listos == len(de_la_fase) else f"{listos}/{len(de_la_fase)}"
            print(f"  {fid}  {f.nombre:38s} {estado}")
            for c in de_la_fase:
                print(f"       {MARCA_ITEM if c.hecho() else ' '} {c.id}  {c.titulo}")
        print()

    print(SEP)
    print("  Antes de escribir código, leé docs/plan/00-convenciones.md")
    print("  Volvé a correr este comando después de cada commit.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
