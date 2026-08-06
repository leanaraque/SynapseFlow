"""Ingesta del corpus de normativa: de Markdown a fragmentos citables.

## El troceado no puede ser ciego

Cortar cada mil caracteres es lo que hace cualquier tutorial de RAG, y acá
produciría el peor error posible: un fragmento que empieza a mitad de la cláusula
7.4 y termina dentro de la 7.5, con la metadata de una sola de las dos. El
sistema citaría «API 570 §7.4» respaldando una afirmación que en realidad vino de
otra cláusula. Una cita con formato de rigor y contenido equivocado es peor que
no citar.

Por eso se trocea **por sección**, usando los encabezados del Markdown. Solo si
una sección excede el tamaño máximo se subdivide, y los subfragmentos conservan
el número de sección de su padre.

## Sin sección no hay cita

Un fragmento sin `seccion` no se puede citar, así que no se puede usar como
fundamento. Es un **error de ingesta**, no un caso a tolerar: se levanta
`IngestaError` en lugar de indexarlo con la sección vacía y descubrirlo cuando el
verificador de F3.4 rechace todas las respuestas sin explicar por qué.

Ver docs/plan/fases/F3-rag.md § F3.1
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, ConfigDict

from synapseflow.persistence.vectorstore import FirestoreVectorStore

# Raíz del corpus versionado. Es fuente, no derivado: los .md están en el
# repositorio a propósito.
CORPUS = Path(__file__).resolve().parents[3] / "data" / "corpus"

# Tamaño máximo de un fragmento, en caracteres. Una sección más larga que esto se
# subdivide. El valor sale de que el perfil `synthesis` recibe seis fragmentos
# por consulta: con 2000 caracteres cada uno son unos 3000 tokens de contexto
# solo en normativa, que deja lugar para la conversación y el historial.
MAXIMO_POR_FRAGMENTO = 2000
SOLAPAMIENTO = 200

# `## 7.4 · Evaluación de espesores` → ("7.4", "Evaluación de espesores").
# El separador es el punto medio «·», que es lo que usa el corpus. Se admite
# también «-» para no romper si alguien edita un documento a mano.
ENCABEZADO = re.compile(r"^##\s+(?P<seccion>[\w.]+)\s*[·\-]\s*(?P<titulo>.+?)\s*$", re.MULTILINE)

# Metadatos que todo fragmento tiene que llevar para poder citarse.
OBLIGATORIOS = ("doc_id", "seccion", "vigencia")


class IngestaError(RuntimeError):
    """Un documento del corpus no se puede convertir en fragmentos citables."""


class DocumentoFuente(BaseModel):
    """Un archivo del corpus, con su frontmatter ya validado."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    titulo: str
    tipo_documento: str
    vigencia: str
    cuerpo: str
    ruta: str


# ─────────────────────────────────────────────────────────────────────────────
# Lectura
# ─────────────────────────────────────────────────────────────────────────────


def leer_documento(ruta: Path) -> DocumentoFuente:
    """Lee un `.md` del corpus y valida su frontmatter.

    Raises:
        IngestaError: si falta el frontmatter o alguno de sus campos.
    """
    texto = ruta.read_text(encoding="utf-8")

    if not texto.startswith("---"):
        raise IngestaError(
            f"'{ruta.name}' no empieza con frontmatter YAML. Sin doc_id ni vigencia "
            "sus fragmentos no se pueden citar ni filtrar."
        )

    partes = texto.split("---", 2)
    if len(partes) < 3:
        raise IngestaError(f"el frontmatter de '{ruta.name}' no está cerrado con '---'")

    try:
        cabecera = yaml.safe_load(partes[1]) or {}
    except yaml.YAMLError as exc:
        raise IngestaError(f"el frontmatter de '{ruta.name}' no es YAML válido: {exc}") from exc

    faltantes = [
        c for c in ("doc_id", "titulo", "tipo_documento", "vigencia") if not cabecera.get(c)
    ]
    if faltantes:
        raise IngestaError(f"a '{ruta.name}' le faltan en el frontmatter: {faltantes}")

    return DocumentoFuente(
        doc_id=str(cabecera["doc_id"]),
        titulo=str(cabecera["titulo"]),
        tipo_documento=str(cabecera["tipo_documento"]),
        vigencia=str(cabecera["vigencia"]),
        cuerpo=partes[2].strip(),
        ruta=ruta.name,
    )


def leer_corpus(directorio: Path | None = None) -> list[DocumentoFuente]:
    """Todos los documentos del corpus, en orden estable por nombre de archivo."""
    raiz = directorio or CORPUS
    archivos = sorted(raiz.glob("*.md"))

    if not archivos:
        raise IngestaError(
            f"no hay documentos .md en '{raiz}'. El corpus está versionado en el "
            "repositorio: si falta, el clon está incompleto."
        )
    return [leer_documento(ruta) for ruta in archivos]


# ─────────────────────────────────────────────────────────────────────────────
# Troceado
# ─────────────────────────────────────────────────────────────────────────────


def trocear(documento: DocumentoFuente) -> list[Document]:
    """Fragmentos citables de un documento, uno por sección.

    Raises:
        IngestaError: si el documento no declara ninguna sección. Un documento
            sin secciones no aporta fundamento citable, y silenciarlo lo dejaría
            indexado e inutilizable.
    """
    secciones = _secciones(documento.cuerpo)

    if not secciones:
        raise IngestaError(
            f"'{documento.doc_id}' no tiene ninguna sección '## N · Título'. "
            "Sin número de sección sus fragmentos no se pueden citar."
        )

    divisor = RecursiveCharacterTextSplitter(
        chunk_size=MAXIMO_POR_FRAGMENTO,
        chunk_overlap=SOLAPAMIENTO,
        # El orden importa: se parte primero por párrafo y solo se llega a
        # cortar por carácter si un párrafo solo excede el máximo.
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    fragmentos: list[Document] = []
    for seccion, titulo_seccion, cuerpo in secciones:
        base = {
            "doc_id": documento.doc_id,
            "titulo": documento.titulo,
            "seccion": seccion,
            "titulo_seccion": titulo_seccion,
            "tipo_documento": documento.tipo_documento,
            "vigencia": documento.vigencia,
        }

        # El encabezado se antepone al texto indexado. Es lo que hace que una
        # consulta por «frecuencia de inspección» recupere la sección que se
        # llama así aunque el cuerpo no repita esas palabras.
        completo = f"{documento.doc_id} §{seccion} · {titulo_seccion}\n\n{cuerpo}"

        if len(completo) <= MAXIMO_POR_FRAGMENTO:
            fragmentos.append(Document(page_content=completo, metadata=dict(base)))
            continue

        partes = divisor.split_text(completo)
        for indice, parte in enumerate(partes):
            # Los subfragmentos conservan el número de sección del padre: son la
            # misma cláusula, y citarlos por separado rompería la referencia.
            fragmentos.append(
                Document(
                    page_content=parte,
                    metadata={**base, "parte": indice + 1, "partes": len(partes)},
                )
            )

    _verificar_citables(fragmentos, documento)
    return fragmentos


def _secciones(cuerpo: str) -> list[tuple[str, str, str]]:
    """Parte el cuerpo en (numero, titulo, texto) por cada encabezado `##`."""
    encontrados = list(ENCABEZADO.finditer(cuerpo))
    secciones: list[tuple[str, str, str]] = []

    for indice, encabezado in enumerate(encontrados):
        desde = encabezado.end()
        hasta = encontrados[indice + 1].start() if indice + 1 < len(encontrados) else len(cuerpo)
        texto = cuerpo[desde:hasta].strip()
        if texto:
            secciones.append(
                (encabezado.group("seccion"), encabezado.group("titulo").strip(), texto)
            )

    return secciones


def _verificar_citables(fragmentos: list[Document], documento: DocumentoFuente) -> None:
    """Ningún fragmento puede salir de acá sin con qué citarse."""
    for fragmento in fragmentos:
        vacios = [c for c in OBLIGATORIOS if not fragmento.metadata.get(c)]
        if vacios:
            raise IngestaError(
                f"un fragmento de '{documento.doc_id}' quedó sin {vacios}. "
                "Sin esos campos no se puede citar ni filtrar por vigencia."
            )


# ─────────────────────────────────────────────────────────────────────────────
# Indexado
# ─────────────────────────────────────────────────────────────────────────────


async def indexar(documentos: list[Document], almacen: FirestoreVectorStore) -> int:
    """Vectoriza e indexa fragmentos. Devuelve cuántos escribió.

    La idempotencia la aporta el vector store: el id de cada documento se deriva
    de su contenido, así que reindexar el corpus sobreescribe en lugar de
    duplicar. Sin eso, cada reindexado ensucia la recuperación con fragmentos
    repetidos que compiten entre sí en el ranking.
    """
    if not documentos:
        return 0

    ids = await almacen.aadd_texts(
        [d.page_content for d in documentos],
        [dict(d.metadata) for d in documentos],
    )
    return len(ids)


async def ingestar_corpus(
    almacen: FirestoreVectorStore, directorio: Path | None = None
) -> dict[str, Any]:
    """Lee, trocea e indexa el corpus completo. Devuelve un resumen."""
    documentos = leer_corpus(directorio)
    fragmentos = [f for documento in documentos for f in trocear(documento)]
    escritos = await indexar(fragmentos, almacen)

    return {
        "documentos": len(documentos),
        "fragmentos": len(fragmentos),
        "indexados": escritos,
        "vigentes": sum(1 for f in fragmentos if f.metadata.get("vigencia") == "vigente"),
        "derogados": sum(1 for f in fragmentos if f.metadata.get("vigencia") == "derogado"),
    }
