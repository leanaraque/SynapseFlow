<div align="center">

**English** · [Español](README.es.md)

# SynapseFlow

**A governed agent platform for regulated industries.**

Built on LangGraph and LangChain 1.x · deployed on Firebase

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![LangChain 1.3](https://img.shields.io/badge/LangChain-1.3-1C3C3C.svg)](pyproject.toml)
[![LangGraph 1.2](https://img.shields.io/badge/LangGraph-1.2-1C3C3C.svg)](pyproject.toml)
[![CI](https://github.com/leanaraque/SynapseFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/leanaraque/SynapseFlow/actions/workflows/ci.yml)

[Architecture](#architecture) ·
[Design decisions](#the-five-decisions-that-define-the-project) ·
[Status](#project-status) ·
[Getting started](#getting-started) ·
[ADRs](docs/adr)

</div>

> [!NOTE]
> **Work in progress.** The ontology and persistence layers are implemented and
> tested. The agent graph, the API and the web console are still being built.
> The [project status](#project-status) table says exactly what runs today and
> what does not — and [Getting started](#getting-started) only documents
> commands that actually work.

---

## The problem

Getting an agent into production inside a regulated company does not fail
because of the model. It fails because of everything around the model.

An agent that reads technical standards and can issue a work order against
equipment in service faces questions a notebook never answers:

- Under whose authority did it execute that action? Did it inherit the user's
  permissions, or the service account's?
- Which exact clause of which standard supports that answer? And what if the
  standard has been superseded?
- Did a person's employee ID leave the perimeter toward an external LLM
  provider?
- Who approved shutting down the equipment? Is there a record that it was a
  human?
- When the prompt changes next month, how do you know it did not get worse?

SynapseFlow is a reference implementation of that missing layer: the scaffolding
that turns an agent graph into a system an auditor can review.

## What it does

The implemented domain is **oil and gas asset integrity**: an assistant that
answers questions about inspection standards, queries an asset's history,
computes remaining life and — with explicit human approval — issues work orders
or requests that an asset be taken out of service.

The target behaviour of the complete system:

```
Usuario: El P-2101-A midió 6,8 mm en la última inspección. ¿Sigue apto?

SynapseFlow:
  → consultar_activo(P-2101-A)              [t_min = 7,1 mm · criticidad A]
  → historial_inspecciones(P-2101-A)        [4 mediciones, 2019-2026]
  → calcular_vida_remanente(P-2101-A)       [Python, no el modelo]
  → buscar_normativa("espesor por debajo de t_min")

  El activo NO está apto para continuar en servicio. El espesor medido
  (6,8 mm) está por debajo del mínimo requerido (7,1 mm).

  API 570 §7.4 exige que un componente cuyo espesor sea inferior a t_min
  sea retirado de servicio o sometido a evaluación de aptitud según
  API 579. La velocidad de corrosión calculada es 0,21 mm/año y la vida
  remanente es negativa: −1,4 años.

  Fuentes: API-570-2016 §7.4 · API-570-2016 §5.6 · PROC-INT-014 §3.2

  ⚠ Acción irreversible propuesta: solicitar_parada_equipo(P-2101-A)
    Requiere aprobación de un supervisor de mantenimiento.
    [ Aprobar ]  [ Rechazar ]
```

The agent does not execute the shutdown. It proposes it, justifies it, and waits
for a human.

> That transcript is the target design, not a captured session: the agent graph
> is still being built. What *is* implemented and verified today is the tool
> catalogue shown above — derived from the ontology and filtered by role — and
> the approval mechanism behind that last block.
>
> The domain is modelled in Spanish, so tool names and output are in Spanish.
> See [Project language](#project-language).

## Architecture

```mermaid
flowchart TB
    subgraph client["Web console · Firebase Hosting"]
        UI["Chat with citations · Approval inbox<br/>Ontology explorer · Cost dashboard"]
    end

    subgraph api["API · Cloud Run"]
        FASTAPI["FastAPI<br/>astream_events → SSE"]
        AUTH["Firebase Auth → RBAC<br/>The agent inherits the user's permissions"]
    end

    subgraph core["SynapseFlow engine · LangGraph"]
        SUP["Supervisor<br/>routes and plans"]
        NORM["Standards agent<br/>RAG with mandatory citations"]
        DATOS["Data agent<br/>queries over the ontology"]
        CALC["Calculation agent<br/>deterministic Python"]
        VERIF["Verifier<br/>groundedness before answering"]
        HITL{"interrupt()<br/>irreversible action"}
    end

    subgraph gov["Governance layer · AgentMiddleware"]
        PII["PII redaction"]
        POL["Zero-training policy"]
        AUDIT["Immutable audit log"]
    end

    subgraph data["Firebase"]
        FS[("Firestore<br/>vector store · checkpoints<br/>audit · approvals")]
        GCS[("Cloud Storage<br/>source documents")]
    end

    subgraph llm["LLM gateway"]
        GW["Cost- and task-aware router"]
        GEM["Gemini"]
        OAI["OpenAI"]
        AZ["Azure OpenAI"]
    end

    UI <-->|SSE| FASTAPI
    FASTAPI --> AUTH --> SUP
    SUP --> NORM & DATOS & CALC
    NORM & DATOS & CALC --> VERIF
    VERIF --> HITL
    HITL -.->|waits for human| UI
    NORM <--> FS
    DATOS <--> FS
    SUP -.->|checkpoint| FS
    NORM -.-> GCS
    core --> gov
    gov --> AUDIT --> FS
    core --> GW
    GW --> GEM & OAI & AZ
    core -.->|traces| LS["LangSmith"]
```

## The five decisions that define the project

**1. The ontology is declarative, not code.** ✅ implemented
Entities, relations and actions live in
[`oil_and_gas.yaml`](packages/synapseflow/ontology/definitions/oil_and_gas.yaml).
At startup that single file produces the validation models, the tool catalogue
the agents see, the per-role permissions and the data classification. Changing
domain means changing the YAML.
→ [ADR-0003](docs/adr/0003-ontologia-declarativa-en-yaml.md)

**2. Reversibility is a property of the domain, not an `if` in the code.** ✅ implemented
Every action declares `reversible` and `requires_approval`. The tool compiler
reads those fields and emits the approval gate configuration. A developer cannot
forget to add the gate: if the action is irreversible, the gate exists.
→ [ADR-0005](docs/adr/0005-hitl-con-interrupt-de-langgraph.md)

**3. The model does not compute numbers.** 🚧 in progress
Corrosion rate and remaining life are computed in deterministic Python and handed
to the model as fact. The LLM writes and justifies; it does not estimate
magnitudes that an engineer will later sign off on.

**4. No citation, no answer.** 🚧 in progress
The standards agent is required to return document and clause. A verifier node
checks that every claim is supported by the retrieved context before the answer
is emitted. If it is not, the system says it does not know.

**5. Sensitive data does not leave the perimeter.** 🚧 in progress
Fields marked `pii` or `restricted` in the ontology are tokenised before the
provider call and rehydrated in the response. The external model sees
`«INSPECTOR_1»`, never an employee ID. Deriving those fields from the ontology
already works; wiring it into the gateway is pending.
→ [ADR-0004](docs/adr/0004-gateway-provider-agnostic.md)

## Project status

| Component | Status | Verification |
|---|---|---|
| Ontology: meta-schema and validation | ✅ | governance invariants enforced at load time |
| Ontology: compiler to LangChain tools | ✅ | 9 actions, enum-typed schemas, role filtering |
| Ontology: derived RBAC and PII fields | ✅ | `consulta` sees 1 tool, `auditor` cannot write |
| `FirestoreSaver` — LangGraph checkpointer | ✅ | 8 tests against the emulator |
| `FirestoreVectorStore` — native `find_nearest` | ✅ | implemented; integration tests pending |
| Inspection CLI | ✅ | `synapseflow ontology validate` |
| Firestore rules and indexes | ✅ | declared and versioned |
| Multi-provider LLM gateway | 🚧 | model and pricing catalogue defined |
| Governance middleware | 📋 | design settled on LangChain 1.x `AgentMiddleware` |
| Hybrid RAG with citations | 📋 | |
| Agent graph | 📋 | |
| Cloud Run API | 📋 | |
| Web console | 📋 | |
| Eval suite and regression CI | 📋 | |

✅ implemented and verified · 🚧 in progress · 📋 planned

The [action map](docs/06-mapa-de-accion.md) breaks the remaining work into eight
phases, each with its dependencies, deliverables and — most importantly — how it
gets verified. It also tracks which of the project's five design commitments are
actually operative and which are still just declared.

**If you're picking up development**, start at [`docs/plan/`](docs/plan/), which
holds the same roadmap broken down commit by commit. Don't guess where the
project stands — ask it:

```bash
python -m scripts.estado
```

That command inspects the repository and reports the current phase, the next
commit, what exactly is missing for it, and how to verify it. The state is
derived from the code, never kept by hand, so it cannot drift.

## Getting started

Everything in this section works today. No API key, no Firebase, no network
access required.

```bash
git clone https://github.com/leanaraque/SynapseFlow.git
cd SynapseFlow

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### Inspect the domain

```bash
# Validates the ontology and reports the governance invariants
synapseflow ontology validate

# Least privilege: which tools each role can see
synapseflow ontology tools --role tecnico
synapseflow ontology tools --role auditor

# Scope of every role, at a glance
synapseflow ontology roles

# Entity diagram as Mermaid, ready to paste
synapseflow ontology graph
```

`synapseflow ontology tools --role tecnico` produces:

```
Rol        tecnico — Técnico de mantenimiento
Clasif.    hasta 'internal'
Ve         5 de 9 acciones del dominio

HERRAMIENTA            EFECTO  PARÁMETROS                                      GATE
─────────────────────  ──────  ──────────────────────────────────────────────  ────────────────────
buscar_normativa       read    consulta, tipo_documento
consultar_activo       read    tag
listar_activos         read    instalacion, clase, criticidad, estado, limite
registrar_borrador_ot  write   tag, tipo, descripcion_trabajo, prioridad       escritura reversible
emitir_orden_trabajo   write   id_ot                                           requiere aprobación
```

### Tests

```bash
# The whole suite. Needs the Firestore emulator in another terminal.
firebase emulators:start --only firestore --project synapseflow-lean
pytest
```

> All 8 tests are currently marked `emulator`, so `pytest -m "not emulator"`
> deselects every one of them and runs nothing. There are no emulator-free tests
> yet. The parts you *can* exercise with no external dependency are the CLI
> commands above.

The test that matters most is
[`test_hitl_sobrevive_a_la_muerte_del_proceso`](tests/persistence/test_checkpointer.py)
("HITL survives process death"): it runs the graph up to the approval gate,
**discards both the graph and the saver**, rebuilds them from scratch and
resumes with `Command(resume=...)`, asserting that the executed action carries
arguments identical to the proposed ones. It is the asynchronous
human-in-the-loop promise, demonstrated rather than claimed.

## Repository layout

```
packages/synapseflow/
  config.py              all environment configuration, in one place
  cli.py                 domain inspection without credentials
  ontology/
    definitions/*.yaml   the domain as data: entities, relations, actions
    schema.py            strict Pydantic meta-schema
    loader.py            loading and validation with actionable errors
    compiler.py          YAML → LangChain tools + gates + RBAC
  persistence/
    client.py            Firestore client shared per process
    vectorstore.py       VectorStore over native find_nearest
    checkpointer.py      LangGraph BaseCheckpointSaver
  llm/
    models.yaml          model catalogue, task profiles and pricing
docs/adr/                architecture decisions, with their alternatives
tests/                   persistence and ontology contract tests
firestore.rules          the client never talks to Firestore; the API applies RBAC
firestore.indexes.json   vector and composite indexes
```

## Stack

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph — supervisor, subgraphs, `interrupt()`, checkpointing |
| Composition and middleware | LangChain 1.x — `create_agent`, `AgentMiddleware`, tool calling |
| Retrieval | Hybrid RAG: `FirestoreVectorStore` + BM25 via `EnsembleRetriever` |
| State persistence | Custom `BaseCheckpointSaver` over Firestore |
| Observability | LangSmith — tracing, datasets, experiments |
| Models | Gemini (default) · OpenAI · Azure OpenAI, behind a gateway |
| API | FastAPI on Cloud Run, SSE streaming |
| Frontend | React + Vite on Firebase Hosting |
| Data | Firestore with native vector search · Cloud Storage |
| Identity | Firebase Auth → RBAC derived from the ontology |

## Architecture decisions

Every non-trivial decision is documented with its context, the alternatives that
were rejected and why, the consequences that count against it, and how it is
verified. The ADRs are written in Spanish.

| ADR | Decision |
|---|---|
| [0001](docs/adr/0001-langgraph-como-motor-de-orquestacion.md) | LangGraph as the orchestration engine |
| [0002](docs/adr/0002-integraciones-firestore-propias.md) | Custom Firestore integrations instead of the official package |
| [0003](docs/adr/0003-ontologia-declarativa-en-yaml.md) | The domain ontology is declarative and lives outside the code |
| [0004](docs/adr/0004-gateway-provider-agnostic.md) | Model gateway with an explicit data boundary |
| [0005](docs/adr/0005-hitl-con-interrupt-de-langgraph.md) | Approval gates use LangGraph's `interrupt()` |

## Scope and honesty

It is worth being explicit about what this is and what it is not.

- **The data is synthetic.** Installations, assets, inspections and work orders
  are generated. None of it comes from a real operation or a real client.
- **The standards corpus is a rewrite.** The indexed texts paraphrase the
  structure and technical criteria of API's in-service inspection codes, but do
  not reproduce their content, which is copyrighted material. They exist to
  exercise the RAG pipeline with the real shape of the problem.
- **This is a reference implementation,** not a system in operation.
- The goal is to demonstrate the architecture and the engineering practices that
  taking agents to production in a regulated environment actually requires.

## Project language

The code, the comments and the ADRs are written in **Spanish**. This is a
deliberate choice, explained in [CONTRIBUTING.md](CONTRIBUTING.md#estilo): the
domain is Spanish-language technical regulation, and mixing languages between the
domain YAML and the code that interprets it creates real friction when reading.

LangChain and LangGraph symbol names are kept in English
(`BaseCheckpointSaver`, `interrupt`, `AgentMiddleware`).

This README is available in [Español](README.es.md). Both versions are kept in
sync; CI fails if one changes without the other.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability, see
[SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
