"""O registro de trechos — como a procedência sobrevive ao grafo.

O PROBLEMA
----------
Um motor de grafo recupera conhecimento, não citações. Ele pode devolver
"Projeto Orion utiliza PostgreSQL" tendo lido isso na página 4 do `doc_b`, e
não necessariamente devolver "página 4 do doc_b" junto. A informação de
origem existe em algum lugar do pipeline dele, mas depender do formato interno
de um motor que pode ser trocado seria amarrar a Livia a ele.

A SOLUÇÃO
---------
O sidecar guarda um registro próprio de tudo que mandou para o motor: cada
trecho, com documento, título, página e origem. Quando um resultado volta,
casamos o texto contra esse registro e devolvemos a procedência que NÓS
sabemos.

    ingestão   trecho → registro (SQLite) → motor
    consulta   motor → texto → registro → documento + página

O casamento é por hash do texto normalizado, com uma segunda tentativa por
prefixo (o motor pode recortar ou reagrupar). O que não casar volta com a
procedência do DATASET, que é por documento — pior que página, melhor que
nada. E o que não casar nem com o dataset é descartado pela Livia.

Isto também é o que permite trocar Cognee por outro motor sem perder
auditabilidade: o registro é nosso, não dele.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    document_id   TEXT PRIMARY KEY,
    dataset       TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    collection_id TEXT NOT NULL DEFAULT '',
    chunks        INTEGER NOT NULL DEFAULT 0,
    ingested_at   TEXT NOT NULL DEFAULT '',
    engine        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trechos (
    chunk_id    TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    hash        TEXT NOT NULL,
    prefixo     TEXT NOT NULL DEFAULT '',
    page        INTEGER,
    origin      TEXT NOT NULL DEFAULT '',
    tipo        TEXT NOT NULL DEFAULT 'text',
    texto       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_trechos_hash ON trechos(hash);
CREATE INDEX IF NOT EXISTS idx_trechos_doc  ON trechos(document_id);
CREATE INDEX IF NOT EXISTS idx_trechos_pref ON trechos(prefixo);
"""

# Quanto do começo do texto serve de "impressão digital" para o casamento por
# prefixo. Curto o bastante para sobreviver a um recorte do motor, longo o
# bastante para não casar dois parágrafos diferentes por acaso.
PREFIXO = 120


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalizar(texto: str) -> str:
    """Espaço e caixa não distinguem um trecho de outro."""
    return re.sub(r"\s+", " ", (texto or "").strip().lower())


def impressao(texto: str) -> str:
    return hashlib.sha256(normalizar(texto).encode("utf-8")).hexdigest()


_criado = False


@contextmanager
def _conectar() -> Iterator[sqlite3.Connection]:
    global _criado
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATA_DIR / "registro.db")
    conn.row_factory = sqlite3.Row
    try:
        if not _criado:
            conn.executescript(_SCHEMA)
            _criado = True
        yield conn
        conn.commit()
    finally:
        conn.close()


def dataset_de(document_id: str) -> str:
    """Cada documento tem dataset próprio.

    Um dataset único e gigante tornaria impossível apagar um documento sem
    apagar todo o resto — que é exatamente o que a missão pede para evitar.
    """
    seguro = re.sub(r"[^a-z0-9_]+", "_", (document_id or "").lower()).strip("_")
    return f"livia_doc_{seguro or 'sem_nome'}"


def registrar(
    document_id: str,
    title: str,
    source: str,
    collection_id: str,
    chunks: list[dict[str, object]],
    engine: str,
) -> None:
    """Guarda a procedência de tudo que vai para o motor."""
    with _conectar() as conn:
        conn.execute("DELETE FROM trechos WHERE document_id = ?", (document_id,))
        conn.execute(
            "INSERT OR REPLACE INTO documentos (document_id, dataset, title, source, "
            "collection_id, chunks, ingested_at, engine) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (document_id, dataset_de(document_id), title, source, collection_id,
             len(chunks), _agora(), engine),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO trechos (chunk_id, document_id, hash, prefixo, "
            "page, origin, tipo, texto) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    str(c.get("chunk_id") or f"{document_id}#{i}"),
                    document_id,
                    impressao(str(c.get("text") or "")),
                    normalizar(str(c.get("text") or ""))[:PREFIXO],
                    c.get("page"),
                    str(c.get("origin") or ""),
                    str(c.get("type") or "text"),
                    str(c.get("text") or "")[:8000],
                )
                for i, c in enumerate(chunks)
            ],
        )


def esquecer(document_id: str) -> bool:
    with _conectar() as conn:
        conn.execute("DELETE FROM trechos WHERE document_id = ?", (document_id,))
        cur = conn.execute(
            "DELETE FROM documentos WHERE document_id = ?", (document_id,)
        )
        return cur.rowcount > 0


def documento(document_id: str) -> dict[str, object] | None:
    with _conectar() as conn:
        linha = conn.execute(
            "SELECT * FROM documentos WHERE document_id = ?", (document_id,)
        ).fetchone()
    return dict(linha) if linha else None


def documentos() -> list[dict[str, object]]:
    with _conectar() as conn:
        linhas = conn.execute(
            "SELECT * FROM documentos ORDER BY ingested_at DESC"
        ).fetchall()
    return [dict(l) for l in linhas]


def trechos_de(document_id: str) -> list[dict[str, object]]:
    with _conectar() as conn:
        linhas = conn.execute(
            "SELECT * FROM trechos WHERE document_id = ? ORDER BY chunk_id",
            (document_id,),
        ).fetchall()
    return [dict(l) for l in linhas]


def por_dataset(dataset: str) -> dict[str, object] | None:
    with _conectar() as conn:
        linha = conn.execute(
            "SELECT * FROM documentos WHERE dataset = ?", (dataset,)
        ).fetchone()
    return dict(linha) if linha else None


# --------------------------------------------------------------------------
# O casamento
# --------------------------------------------------------------------------


def procedencia(texto: str, dataset: str = "") -> dict[str, object] | None:
    """De onde veio este texto? None quando não dá para saber.

    Três tentativas, da mais precisa para a menos:

      1. hash exato       o motor devolveu o trecho inteiro, como mandamos
      2. prefixo          ele recortou; o começo ainda casa
      3. dataset          nada casou, mas sabemos de qual documento veio

    Devolver None é um resultado legítimo e importante: a Livia descarta o
    que não tem procedência, e é melhor perder um resultado do que atribuir
    uma frase ao documento errado.
    """
    limpo = normalizar(texto)
    if not limpo:
        return None

    with _conectar() as conn:
        linha = conn.execute(
            "SELECT t.*, d.title, d.source, d.collection_id, d.ingested_at "
            "FROM trechos t JOIN documentos d ON d.document_id = t.document_id "
            "WHERE t.hash = ? LIMIT 1",
            (impressao(texto),),
        ).fetchone()

        if linha is None:
            # O motor pode ter recortado o trecho. O começo costuma sobreviver.
            alvo = limpo[:PREFIXO]
            linha = conn.execute(
                "SELECT t.*, d.title, d.source, d.collection_id, d.ingested_at "
                "FROM trechos t JOIN documentos d ON d.document_id = t.document_id "
                "WHERE t.prefixo = ? LIMIT 1",
                (alvo,),
            ).fetchone()

        if linha is None and len(limpo) >= 40:
            # Última tentativa por texto: o motor pode ter cortado o COMEÇO
            # (juntando com o parágrafo anterior). Procuramos o trecho que
            # contém este texto.
            linha = conn.execute(
                "SELECT t.*, d.title, d.source, d.collection_id, d.ingested_at "
                "FROM trechos t JOIN documentos d ON d.document_id = t.document_id "
                "WHERE instr(lower(t.texto), ?) > 0 LIMIT 1",
                (limpo[:200],),
            ).fetchone()

    if linha is not None:
        dados = dict(linha)
        return {
            "document_id": dados["document_id"],
            "chunk_id": dados["chunk_id"],
            "title": dados["title"],
            "source": dados["origin"] or dados["source"] or dados["title"],
            "page": dados["page"],
            "collection_id": dados["collection_id"],
            "ingested_at": dados["ingested_at"],
            "precisao": "trecho",
        }

    # Nada casou no texto. Se o motor disse de qual dataset veio, ainda
    # sabemos o documento — pior que a página, muito melhor que nada.
    if dataset:
        doc = por_dataset(dataset)
        if doc:
            return {
                "document_id": doc["document_id"],
                "chunk_id": None,
                "title": doc["title"],
                "source": doc["source"] or doc["title"],
                "page": None,
                "collection_id": doc["collection_id"],
                "ingested_at": doc["ingested_at"],
                "precisao": "documento",
            }

    return None


def estatisticas() -> dict[str, int]:
    with _conectar() as conn:
        docs = conn.execute("SELECT COUNT(*) AS n FROM documentos").fetchone()["n"]
        trechos = conn.execute("SELECT COUNT(*) AS n FROM trechos").fetchone()["n"]
    return {"documentos": int(docs), "trechos": int(trechos)}
