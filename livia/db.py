"""O SQLite da Livia: conversas, índice de memória, experiências e cache.

Para 1-2 usuários isso é mais do que suficiente e não exige servidor nenhum.
O banco é um arquivo só: data/livia.db.

O QUE VIVE AQUI E O QUE NÃO VIVE
--------------------------------
Memórias, skills e lições continuam sendo arquivos Markdown, e essa é a
fonte da verdade — dá para abrir no bloco de notas, corrigir e apagar. O que
mora neste banco é o que seria ruim escrever no Markdown: vetores, contadores
de uso, carimbos de última utilização, hashes.

A consequência prática: apagar `livia.db` custa o histórico de conversas e as
experiências, e obriga a reconstruir os índices — mas NÃO apaga nada do que
o André escreveu ou ensinou. Isso é de propósito. Índice é derivado; texto é
original. Inverter isso transformaria um arquivo binário na única cópia da
memória de alguém.

MIGRAÇÃO
--------
Tudo é `CREATE TABLE IF NOT EXISTS` e `ALTER TABLE` tolerante a coluna já
existente. Quem atualiza a Livia abre a versão nova e continua usando; não há
passo manual de conversão.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone

import numpy as np

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT 'Nova conversa',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);

-- Índice das memórias. O texto continua no Markdown; aqui ficam o vetor e os
-- números operacionais, que não teriam por que sujar um arquivo que a pessoa
-- edita à mão. `nome` é o slug, o mesmo do arquivo .md.
CREATE TABLE IF NOT EXISTS memory_index (
    nome         TEXT PRIMARY KEY,
    colecao      TEXT NOT NULL DEFAULT 'memories',
    kind         TEXT NOT NULL DEFAULT 'fact',
    escopo       TEXT NOT NULL DEFAULT 'global',
    status       TEXT NOT NULL DEFAULT 'active',
    origem       TEXT NOT NULL DEFAULT '',
    importancia  REAL NOT NULL DEFAULT 0.5,
    confianca    REAL NOT NULL DEFAULT 0.7,
    criado_em    TEXT NOT NULL DEFAULT '',
    atualizado_em TEXT NOT NULL DEFAULT '',
    usado_em     TEXT NOT NULL DEFAULT '',
    usos         INTEGER NOT NULL DEFAULT 0,
    substitui    TEXT NOT NULL DEFAULT '',
    substituida_por TEXT NOT NULL DEFAULT '',
    hash         TEXT NOT NULL DEFAULT '',
    assinatura   TEXT NOT NULL DEFAULT '',
    vetor        BLOB
);

CREATE INDEX IF NOT EXISTS idx_memory_colecao
    ON memory_index(colecao, status);

-- Experiências: o que foi tentado, o que aconteceu, o que se aprendeu.
-- Volume alto e valor individual baixo — por isso ficam só aqui, e não em
-- Markdown. O que vira regra durável sobe para data/lessons/ como texto.
CREATE TABLE IF NOT EXISTS experiences (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em  TEXT NOT NULL,
    tarefa     TEXT NOT NULL DEFAULT '',
    contexto   TEXT NOT NULL DEFAULT '',
    acoes      TEXT NOT NULL DEFAULT '[]',
    resultado  TEXT NOT NULL DEFAULT '',
    sucesso    INTEGER,
    erro       TEXT NOT NULL DEFAULT '',
    feedback   TEXT NOT NULL DEFAULT '',
    licao      TEXT NOT NULL DEFAULT '',
    escopo     TEXT NOT NULL DEFAULT 'global',
    conversa   INTEGER,
    promovida  INTEGER NOT NULL DEFAULT 0,
    hash       TEXT NOT NULL DEFAULT '',
    assinatura TEXT NOT NULL DEFAULT '',
    vetor      BLOB
);

CREATE INDEX IF NOT EXISTS idx_experiences_data ON experiences(criado_em DESC);

-- Skills que a Livia deduziu de padrões e que ESPERAM aprovação. Nada aqui
-- entra no prompt: uma skill nasce de sugestão, não de decisão dela.
CREATE TABLE IF NOT EXISTS skill_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em   TEXT NOT NULL,
    nome        TEXT NOT NULL,
    descricao   TEXT NOT NULL DEFAULT '',
    passos      TEXT NOT NULL DEFAULT '',
    evidencia   TEXT NOT NULL DEFAULT '[]',
    confianca   REAL NOT NULL DEFAULT 0.0,
    origem      TEXT NOT NULL DEFAULT '',
    situacao    TEXT NOT NULL DEFAULT 'pendente'
);

-- Cache de vetores por hash de conteúdo + assinatura do gerador.
-- Puramente derivado: apagar só custa recalcular.
CREATE TABLE IF NOT EXISTS embedding_cache (
    hash       TEXT NOT NULL,
    assinatura TEXT NOT NULL,
    vetor      BLOB NOT NULL,
    criado_em  TEXT NOT NULL,
    PRIMARY KEY (hash, assinatura)
);

-- Fila do Knowledge Engine. Construir grafo leva minutos e não pode
-- acontecer dentro do pedido HTTP do upload. Uma tabela resolve: sobrevive
-- a reinício e não acrescenta serviço nenhum para manter de pé.
--
-- `operacao` guarda também os tombstones de remoção: documento apagado com
-- o serviço offline vira um 'remove' pendente, senão o grafo acumularia
-- conhecimento órfão aparecendo em respostas futuras.
CREATE TABLE IF NOT EXISTS knowledge_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  TEXT NOT NULL,
    operacao     TEXT NOT NULL DEFAULT 'ingest',
    situacao     TEXT NOT NULL DEFAULT 'queued',
    criado_em    TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    tentativas   INTEGER NOT NULL DEFAULT 0,
    erro         TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_jobs_situacao ON knowledge_jobs(situacao, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_criado = False


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Conexão com o esquema já garantido.

    Criar as tabelas na primeira conexão do processo, em vez de só no arranque
    do servidor, existe porque agora há módulos (memória, experiências) que
    usam o banco fora do ciclo de vida do app. Sem isto, o primeiro acesso de
    um teste ou de um script quebraria com "no such table".
    """
    global _criado
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if not _criado:
            conn.executescript(_SCHEMA)
            _criado = True
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    global _criado
    _criado = False
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def create_conversation(title: str = "Nova conversa") -> int:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        return int(cur.lastrowid)


def list_conversations(limit: int = 50) -> list[dict[str, object]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(conversation_id: int, role: str, content: str) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )


def get_messages(conversation_id: int, limit: int | None = None) -> list[dict[str, str]]:
    """Mensagens em ordem cronológica. Com `limit`, devolve as N mais recentes."""
    with _connect() as conn:
        if limit is None:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
            rows = list(reversed(rows))
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def conversation_exists(conversation_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
    return row is not None


def set_title(conversation_id: int, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title.strip()[:80] or "Nova conversa", conversation_id),
        )


def delete_conversation(conversation_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


# --------------------------------------------------------------------------
# Vetores
# --------------------------------------------------------------------------
#
# Guardados como blob de float32. Poderia ser JSON, e seria legível — mas um
# vetor de 768 posições vira ~9 KB em texto contra 3 KB em binário, e são
# milhares deles. Legibilidade aqui não vale nada: ninguém lê vetor a olho.


def _para_blob(vetor: "np.ndarray") -> bytes:
    return np.asarray(vetor, dtype=np.float32).tobytes()


# Sentinela para distinguir "não passei este campo" de "quero apagar este
# campo". Sem ela, `vetor=None` era indistinguível de campo ausente, e a
# invalidação do vetor ao mudar o texto não acontecia — a busca continuaria
# comparando a pergunta com o texto de ontem.
_AUSENTE = object()


def _de_blob(dados: bytes | None) -> "np.ndarray | None":
    if not dados:
        return None
    return np.frombuffer(dados, dtype=np.float32)


def embedding_em_cache(hash_texto: str, assinatura: str) -> "np.ndarray | None":
    with _connect() as conn:
        linha = conn.execute(
            "SELECT vetor FROM embedding_cache WHERE hash = ? AND assinatura = ?",
            (hash_texto, assinatura),
        ).fetchone()
    return _de_blob(linha["vetor"]) if linha else None


def guardar_embedding(hash_texto: str, assinatura: str, vetor: "np.ndarray") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO embedding_cache (hash, assinatura, vetor, criado_em) "
            "VALUES (?, ?, ?, ?)",
            (hash_texto, assinatura, _para_blob(vetor), _now()),
        )


def limpar_cache_embeddings() -> int:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM embedding_cache")
        return cur.rowcount


# --------------------------------------------------------------------------
# Índice de memória
# --------------------------------------------------------------------------


def memoria_linhas(colecao: str = "memories") -> list[dict[str, object]]:
    with _connect() as conn:
        linhas = conn.execute(
            "SELECT * FROM memory_index WHERE colecao = ? ORDER BY nome", (colecao,)
        ).fetchall()
    return [_linha_memoria(l) for l in linhas]


def memoria_linha(nome: str, colecao: str = "memories") -> dict[str, object] | None:
    with _connect() as conn:
        linha = conn.execute(
            "SELECT * FROM memory_index WHERE nome = ? AND colecao = ?", (nome, colecao)
        ).fetchone()
    return _linha_memoria(linha) if linha else None


def _linha_memoria(linha: sqlite3.Row) -> dict[str, object]:
    dados = dict(linha)
    dados["vetor"] = _de_blob(dados.pop("vetor", None))
    return dados


def memoria_upsert(nome: str, colecao: str = "memories", **campos: object) -> None:
    """Cria ou atualiza a linha de índice de uma memória.

    Só os campos passados são tocados. Isso importa: sincronizar o Markdown
    não pode zerar o contador de uso, e registrar um uso não pode desfazer
    uma mudança de escopo feita segundos antes.
    """
    vetor = campos.pop("vetor", _AUSENTE)
    if vetor is not _AUSENTE:
        campos["vetor"] = _para_blob(vetor) if vetor is not None else None

    with _connect() as conn:
        existe = conn.execute(
            "SELECT 1 FROM memory_index WHERE nome = ? AND colecao = ?", (nome, colecao)
        ).fetchone()

        if existe is None:
            campos.setdefault("criado_em", _now())
            campos.setdefault("atualizado_em", _now())
            colunas = ["nome", "colecao", *campos]
            valores = [nome, colecao, *campos.values()]
            conn.execute(
                f"INSERT INTO memory_index ({', '.join(colunas)}) "
                f"VALUES ({', '.join('?' * len(colunas))})",
                valores,
            )
            return

        if not campos:
            return
        atribuicoes = ", ".join(f"{c} = ?" for c in campos)
        conn.execute(
            f"UPDATE memory_index SET {atribuicoes} WHERE nome = ? AND colecao = ?",
            [*campos.values(), nome, colecao],
        )


def memoria_apagar(nome: str, colecao: str = "memories") -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM memory_index WHERE nome = ? AND colecao = ?", (nome, colecao)
        )


def memoria_registrar_uso(nomes: list[str], colecao: str = "memories") -> None:
    """Marca as memórias que acabaram de entrar num prompt.

    É o sinal mais barato e mais honesto de utilidade: memória que nunca é
    recuperada é forte candidata a arquivamento na manutenção.
    """
    if not nomes:
        return
    agora = _now()
    with _connect() as conn:
        conn.executemany(
            "UPDATE memory_index SET usos = usos + 1, usado_em = ? "
            "WHERE nome = ? AND colecao = ?",
            [(agora, n, colecao) for n in nomes],
        )


def memoria_nomes(colecao: str = "memories") -> set[str]:
    with _connect() as conn:
        linhas = conn.execute(
            "SELECT nome FROM memory_index WHERE colecao = ?", (colecao,)
        ).fetchall()
    return {l["nome"] for l in linhas}


# --------------------------------------------------------------------------
# Experiências
# --------------------------------------------------------------------------


def experiencia_gravar(
    tarefa: str,
    *,
    contexto: str = "",
    acoes: list[dict[str, object]] | None = None,
    resultado: str = "",
    sucesso: bool | None = None,
    erro: str = "",
    feedback: str = "",
    licao: str = "",
    escopo: str = "global",
    conversa: int | None = None,
    hash_texto: str = "",
) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO experiences (criado_em, tarefa, contexto, acoes, resultado, "
            "sucesso, erro, feedback, licao, escopo, conversa, hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _now(), tarefa, contexto,
                json.dumps(acoes or [], ensure_ascii=False),
                resultado,
                None if sucesso is None else int(sucesso),
                erro, feedback, licao, escopo, conversa, hash_texto,
            ),
        )
        return int(cur.lastrowid)


def experiencia_atualizar(id_: int, **campos: object) -> None:
    vetor = campos.pop("vetor", _AUSENTE)
    if vetor is not _AUSENTE:
        campos["vetor"] = _para_blob(vetor) if vetor is not None else None
    if not campos:
        return
    atribuicoes = ", ".join(f"{c} = ?" for c in campos)
    with _connect() as conn:
        conn.execute(
            f"UPDATE experiences SET {atribuicoes} WHERE id = ?",
            [*campos.values(), id_],
        )


def experiencia_listar(limite: int = 50, escopo: str | None = None) -> list[dict[str, object]]:
    consulta = "SELECT * FROM experiences"
    args: list[object] = []
    if escopo:
        consulta += " WHERE escopo = ?"
        args.append(escopo)
    consulta += " ORDER BY id DESC LIMIT ?"
    args.append(limite)

    with _connect() as conn:
        linhas = conn.execute(consulta, args).fetchall()
    return [_linha_experiencia(l) for l in linhas]


def experiencia_todas() -> list[dict[str, object]]:
    with _connect() as conn:
        linhas = conn.execute("SELECT * FROM experiences ORDER BY id").fetchall()
    return [_linha_experiencia(l) for l in linhas]


def experiencia_apagar(id_: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM experiences WHERE id = ?", (id_,))
        return cur.rowcount > 0


def _linha_experiencia(linha: sqlite3.Row) -> dict[str, object]:
    dados = dict(linha)
    dados["vetor"] = _de_blob(dados.pop("vetor", None))
    try:
        dados["acoes"] = json.loads(dados.get("acoes") or "[]")
    except json.JSONDecodeError:
        dados["acoes"] = []
    if dados.get("sucesso") is not None:
        dados["sucesso"] = bool(dados["sucesso"])
    return dados


# --------------------------------------------------------------------------
# Skills candidatas
# --------------------------------------------------------------------------


def candidata_gravar(
    nome: str,
    descricao: str,
    passos: str,
    evidencia: list[object],
    confianca: float,
    origem: str,
) -> int:
    with _connect() as conn:
        existente = conn.execute(
            "SELECT id FROM skill_candidates WHERE nome = ? AND situacao = 'pendente'",
            (nome,),
        ).fetchone()
        if existente:
            conn.execute(
                "UPDATE skill_candidates SET descricao = ?, passos = ?, evidencia = ?, "
                "confianca = ? WHERE id = ?",
                (descricao, passos, json.dumps(evidencia, ensure_ascii=False),
                 confianca, existente["id"]),
            )
            return int(existente["id"])

        cur = conn.execute(
            "INSERT INTO skill_candidates (criado_em, nome, descricao, passos, "
            "evidencia, confianca, origem) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), nome, descricao, passos,
             json.dumps(evidencia, ensure_ascii=False), confianca, origem),
        )
        return int(cur.lastrowid)


def candidata_listar(situacao: str = "pendente") -> list[dict[str, object]]:
    with _connect() as conn:
        linhas = conn.execute(
            "SELECT * FROM skill_candidates WHERE situacao = ? ORDER BY id DESC",
            (situacao,),
        ).fetchall()
    saida = []
    for linha in linhas:
        dados = dict(linha)
        try:
            dados["evidencia"] = json.loads(dados.get("evidencia") or "[]")
        except json.JSONDecodeError:
            dados["evidencia"] = []
        saida.append(dados)
    return saida


def candidata_obter(id_: int) -> dict[str, object] | None:
    with _connect() as conn:
        linha = conn.execute(
            "SELECT * FROM skill_candidates WHERE id = ?", (id_,)
        ).fetchone()
    if linha is None:
        return None
    dados = dict(linha)
    try:
        dados["evidencia"] = json.loads(dados.get("evidencia") or "[]")
    except json.JSONDecodeError:
        dados["evidencia"] = []
    return dados


def candidata_situacao(id_: int, situacao: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE skill_candidates SET situacao = ? WHERE id = ?", (situacao, id_)
        )
        return cur.rowcount > 0


# --------------------------------------------------------------------------
# Fila do Knowledge Engine
# --------------------------------------------------------------------------
#
# Construir o grafo de um documento leva minutos. Isso não pode acontecer
# dentro do pedido HTTP do upload — a conexão morreria antes, e o André
# ficaria olhando uma barra travada.
#
# A fila é uma TABELA, não Celery nem Redis. Para 1-2 usuários e alguns
# documentos por semana, uma tabela com quatro estados resolve, sobrevive a
# reinício e não acrescenta serviço nenhum para manter de pé.
#
# Os "tombstones" moram aqui também: quando o André apaga um documento com o
# serviço offline, a remoção local acontece na hora e fica registrada uma
# operação pendente. Sem isso o grafo acumularia conhecimento órfão — trechos
# de documento que não existe mais, aparecendo em respostas futuras.


def job_enfileirar(document_id: str, operacao: str = "ingest") -> int:
    """Põe (ou repõe) um documento na fila.

    Reenfileirar um documento que já está lá zera a tentativa em vez de criar
    uma segunda entrada: dois jobs para o mesmo documento construiriam o grafo
    duas vezes e o segundo sobrescreveria o primeiro.
    """
    agora = _now()
    with _connect() as conn:
        existente = conn.execute(
            "SELECT id FROM knowledge_jobs WHERE document_id = ? AND operacao = ? "
            "AND situacao IN ('queued', 'processing')",
            (document_id, operacao),
        ).fetchone()
        if existente:
            conn.execute(
                "UPDATE knowledge_jobs SET situacao = 'queued', atualizado_em = ?, "
                "erro = '' WHERE id = ?",
                (agora, existente["id"]),
            )
            return int(existente["id"])

        cur = conn.execute(
            "INSERT INTO knowledge_jobs (document_id, operacao, situacao, "
            "criado_em, atualizado_em) VALUES (?, ?, 'queued', ?, ?)",
            (document_id, operacao, agora, agora),
        )
        return int(cur.lastrowid)


def job_proximo() -> dict[str, object] | None:
    """Pega o próximo da fila e já marca como em processamento.

    A marcação no mesmo passo evita dois processos pegarem o mesmo job. Não é
    fila distribuída, mas o servidor pode ter mais de um worker.
    """
    with _connect() as conn:
        linha = conn.execute(
            "SELECT * FROM knowledge_jobs WHERE situacao = 'queued' "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if linha is None:
            return None
        conn.execute(
            "UPDATE knowledge_jobs SET situacao = 'processing', atualizado_em = ?, "
            "tentativas = tentativas + 1 WHERE id = ?",
            (_now(), linha["id"]),
        )
    return dict(linha)


def job_terminar(id_: int, ok: bool, erro: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE knowledge_jobs SET situacao = ?, erro = ?, atualizado_em = ? "
            "WHERE id = ?",
            ("completed" if ok else "failed", erro[:500], _now(), id_),
        )


def job_listar(limite: int = 50) -> list[dict[str, object]]:
    with _connect() as conn:
        linhas = conn.execute(
            "SELECT * FROM knowledge_jobs ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
    return [dict(l) for l in linhas]


def job_pendentes() -> int:
    with _connect() as conn:
        linha = conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge_jobs "
            "WHERE situacao IN ('queued', 'processing')"
        ).fetchone()
    return int(linha["n"])


def job_recuperar_abandonados() -> int:
    """Jobs em `processing` no arranque foram interrompidos por um reinício.

    Voltam para a fila em vez de virar `failed`: a interrupção não diz nada
    sobre o documento, e desistir na primeira queda de energia obrigaria o
    André a reenfileirar tudo à mão.

    Com tentativas demais, vira `failed` — aí o problema é o documento, e
    repetir para sempre só ocuparia a fila.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE knowledge_jobs SET situacao = 'failed', "
            "erro = 'abandonado depois de tentativas demais', atualizado_em = ? "
            "WHERE situacao = 'processing' AND tentativas >= 3",
            (_now(),),
        )
        cur = conn.execute(
            "UPDATE knowledge_jobs SET situacao = 'queued', atualizado_em = ? "
            "WHERE situacao = 'processing'",
            (_now(),),
        )
        return cur.rowcount


def job_limpar(document_id: str) -> None:
    """Tira da fila os jobs de um documento que não existe mais."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM knowledge_jobs WHERE document_id = ? AND situacao IN "
            "('queued', 'failed', 'completed')",
            (document_id,),
        )
