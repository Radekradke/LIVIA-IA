"""Busca por PALAVRA — o que o vetor sozinho não faz.

O PROBLEMA
----------
A biblioteca era 100% densa: tudo por semelhança de significado. Isso é ótimo
para "como reservo memória?" achar o trecho sobre `malloc`, e é justamente o
que faz a busca por palavra parecer desnecessária.

Mas os dois erram em direções opostas, e a direção do vetor é conhecida:

    ERR_CONN_2043        um código de erro não "se parece" com nada
    RFC 7231             número não tem vizinhança semântica
    lru_cache            nome de função é sequência de letras, não conceito
    Kubernetes 1.28      versão exata some no meio de tudo sobre Kubernetes
    "Cláusula 4.2.1"     citação literal

Nesses casos o vetor traz um trecho sobre o assunto geral e erra o específico.
O BM25 acha na primeira tentativa, porque para ele o termo raro é o que vale
mais — quanto menos documentos contêm uma palavra, mais peso ela tem.

POR QUE NÃO ENTROU UMA DEPENDÊNCIA
----------------------------------
O SQLite já tem FTS5 embutido, com BM25 implementado em C. Não há biblioteca
para instalar, não há serviço para subir, e o índice vive no mesmo arquivo do
resto. Uma implementação de BM25 em Python puro seria mais código e mais
lenta.

ISTO É ÍNDICE DERIVADO
----------------------
Como os vetores. A fonte da verdade continua sendo `trechos.jsonl` de cada
documento. Apagar esta tabela não perde nada: `sincronizar()` reconstrói lendo
os arquivos, e é isso que faz o índice aparecer sozinho para quem já tinha
biblioteca antes desta funcionalidade existir.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata

from . import config

log = logging.getLogger("livia.lexical")

# `remove_diacritics 2` é o que faz "memória" na pergunta achar "memoria" no
# documento — e vice-versa. Sem isso, metade das buscas em português falharia
# por causa de um acento que ninguém digitou.
TOKENIZADOR = "unicode61 remove_diacritics 2"

_ESQUEMA = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS trechos_fts USING fts5(
    texto,
    slug     UNINDEXED,
    pagina   UNINDEXED,
    origem   UNINDEXED,
    linha    UNINDEXED,
    tokenize='{TOKENIZADOR}'
);
"""

# Palavras que aparecem em quase toda pergunta. O BM25 já as despreza sozinho
# (termo comum pesa pouco), mas tirá-las antes evita um caso chato: uma
# pergunta feita SÓ de palavras comuns viraria uma consulta que casa com o
# documento inteiro e devolve lixo ordenado por acaso.
_VAZIAS = frozenset("""
a as o os um uma uns umas de do da dos das em no na nos nas por para com sem
sob sobre entre ate e ou mas que qual quais quando onde como porque por que
qem quem se ja nao sim ha tem ter foi ser sao e eh esta estao isso isto aquilo
me te se lhe nos vos meu minha seu sua dele dela deles delas
o_que mais menos muito pouco todo toda todos todas outro outra
""".split())

_PALAVRA = re.compile(r"[0-9a-zA-Z_À-ɏ][0-9a-zA-Z_À-ɏ.\-]*")


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in base if not unicodedata.combining(c))


def consulta_fts(pergunta: str) -> str:
    """Texto do usuário virado numa expressão que o FTS5 aceita.

    Isto NÃO é frescura de sanitização: o MATCH do FTS5 tem sintaxe própria —
    aspas, `NEAR`, `*`, `AND`, `OR`, `-` e parênteses são operadores. Uma
    pergunta comum como `o que é "cache-control"?` não é uma consulta ruim,
    é um erro de sintaxe, e derrubaria a busca inteira.

    Cada termo sai entre aspas (vira literal, sem operador) e são unidos por
    OR. OR e não AND porque a pergunta tem palavras que o documento não tem:
    exigir todas devolveria vazio quase sempre, e quem separa o joio é o BM25
    na ordenação, não o filtro.
    """
    termos: list[str] = []
    for bruto in _PALAVRA.findall(pergunta or ""):
        palavra = _sem_acento(bruto).lower().strip(".-")
        if len(palavra) < 2 or palavra in _VAZIAS:
            continue
        # A aspa dupla é o único caractere perigoso dentro de um termo citado.
        seguro = palavra.replace('"', "")
        if seguro and seguro not in termos:
            termos.append(seguro)
    return " OR ".join(f'"{t}"' for t in termos)


# --------------------------------------------------------------------------
# O índice
# --------------------------------------------------------------------------


def _conectar() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_ESQUEMA)
    return conn


def indexar(slug: str, trechos: list[dict[str, object]]) -> int:
    """Grava os trechos de um documento. Substitui o que houver dele."""
    conn = _conectar()
    try:
        conn.execute("DELETE FROM trechos_fts WHERE slug = ?", (slug,))
        conn.executemany(
            "INSERT INTO trechos_fts (texto, slug, pagina, origem, linha) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    str(t.get("texto") or ""),
                    slug,
                    int(t.get("pagina") or 0),
                    str(t.get("origem") or ""),
                    i,
                )
                for i, t in enumerate(trechos)
                if str(t.get("texto") or "").strip()
            ],
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def remover(slug: str) -> int:
    conn = _conectar()
    try:
        cur = conn.execute("DELETE FROM trechos_fts WHERE slug = ?", (slug,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def slugs_indexados() -> set[str]:
    conn = _conectar()
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT slug FROM trechos_fts")}
    finally:
        conn.close()


def vazio() -> bool:
    conn = _conectar()
    try:
        return conn.execute("SELECT 1 FROM trechos_fts LIMIT 1").fetchone() is None
    finally:
        conn.close()


def sincronizar() -> dict[str, int]:
    """Põe o índice em dia com o que está em disco.

    É o que faz a busca por palavra aparecer sozinha para quem já tinha
    biblioteca antes desta funcionalidade — sem botão, sem migração, sem
    pedir nada. Índice derivado se reconstrói; não se pede permissão para
    refazer o que dá para refazer.
    """
    from . import biblioteca

    em_disco = {str(l["slug"]) for l in biblioteca.listar()}
    indexados = slugs_indexados()

    novos = 0
    for slug in sorted(em_disco - indexados):
        trechos = _trechos_de(slug)
        if trechos:
            indexar(slug, trechos)
            novos += 1

    sumidos = 0
    for slug in sorted(indexados - em_disco):
        remover(slug)
        sumidos += 1

    if novos or sumidos:
        log.info("[lexical] índice em dia: +%d documento(s), -%d", novos, sumidos)
    return {"indexados": novos, "removidos": sumidos, "total": len(em_disco)}


def _trechos_de(slug: str) -> list[dict[str, object]]:
    from . import biblioteca

    caminho = biblioteca.PASTA / slug / "trechos.jsonl"
    try:
        linhas = caminho.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    trechos: list[dict[str, object]] = []
    for linha in linhas:
        try:
            trechos.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return trechos


# --------------------------------------------------------------------------
# A busca
# --------------------------------------------------------------------------


def buscar(pergunta: str, quantos: int = 10) -> list[dict[str, object]]:
    """Trechos que contêm as palavras da pergunta, ordenados por BM25.

    Devolve o mesmo formato de `biblioteca.buscar` para o roteador não
    precisar saber de onde veio cada coisa.
    """
    expressao = consulta_fts(pergunta)
    if not expressao:
        return []

    conn = _conectar()
    try:
        # bm25() devolve NEGATIVO, e mais negativo é melhor. Ordenar crescente
        # é o certo — inverter isto traria os piores primeiros, silenciosamente.
        linhas = conn.execute(
            "SELECT texto, slug, pagina, origem, bm25(trechos_fts) AS nota "
            "FROM trechos_fts WHERE trechos_fts MATCH ? "
            "ORDER BY nota LIMIT ?",
            (expressao, quantos),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # Sintaxe do FTS5 rejeitada apesar do cuidado acima. A busca lexical é
        # complemento: perder ela é responder pelo vetor, nunca não responder.
        log.debug("[lexical] consulta recusada (%s): %r", exc, expressao)
        return []
    finally:
        conn.close()

    from . import biblioteca

    # O que está no índice mas não está mais em disco NÃO é resultado. Os dois
    # índices moram em lugares diferentes — vetores na pasta do documento, este
    # no SQLite — e podem sair de sincronia por caminhos que ninguém previu:
    # restaurar um backup parcial, apagar a pasta na mão, trocar o DATA_DIR.
    # Sem este filtro, a Livia citaria um documento que não existe mais, com
    # confiança total. Custa nada: o título já vinha daqui.
    titulos = {str(l["slug"]): str(l["titulo"]) for l in biblioteca.listar()}
    return [
        {
            "livro": titulos[str(l["slug"])],
            "slug": str(l["slug"]),
            "pagina": int(l["pagina"] or 0),
            "origem": str(l["origem"] or ""),
            "texto": str(l["texto"] or ""),
            "nota": round(float(l["nota"]), 4),
        }
        for l in linhas
        if str(l["slug"]) in titulos
    ]


def estatisticas() -> dict[str, int]:
    conn = _conectar()
    try:
        total = conn.execute("SELECT count(*) FROM trechos_fts").fetchone()[0]
        docs = conn.execute(
            "SELECT count(DISTINCT slug) FROM trechos_fts"
        ).fetchone()[0]
        return {"trechos": int(total), "documentos": int(docs)}
    finally:
        conn.close()
