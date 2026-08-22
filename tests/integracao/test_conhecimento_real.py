"""Teste funcional do grafo, contra um Knowledge Engine DE VERDADE.

Não roda no `pytest` normal. Para rodar:

    pip install -r requirements-knowledge.txt
    ollama pull llama3.1:8b && ollama pull nomic-embed-text
    python -m services.knowledge.run          # noutro terminal

    LIVIA_KNOWLEDGE=1 python -m pytest tests/integracao/test_conhecimento_real.py

Sem LIVIA_KNOWLEDGE=1 tudo aqui é pulado.

O QUE SÓ ISTO PROVA
-------------------
A suíte mockada prova o contrato: que a Livia fala certo com o serviço, que
o fallback funciona, que a procedência é exigida. O que ela NÃO consegue
provar é se o motor realmente encontra a ligação — isso depende do modelo
extrair as entidades certas, e nenhum mock mede isso.

O cenário abaixo é o mínimo honesto: três fatos em três documentos, e uma
pergunta cuja resposta não está escrita em nenhum deles.
"""

from __future__ import annotations

import os

import httpx
import pytest

SERVICO = os.getenv("LIVIA_KNOWLEDGE_URL", "http://127.0.0.1:8110")

pytestmark = pytest.mark.skipif(
    os.getenv("LIVIA_KNOWLEDGE", "0") == "0",
    reason="precisa de um Knowledge Engine de verdade (LIVIA_KNOWLEDGE=1)",
)

DOCS = {
    "doc_a": "Alice trabalha no Projeto Orion desde 2024.",
    "doc_b": "O Projeto Orion utiliza PostgreSQL como banco de dados principal.",
    "doc_c": "PostgreSQL utiliza MVCC para controle de concorrência.",
}


@pytest.fixture(scope="module")
def cliente():
    with httpx.Client(base_url=SERVICO, timeout=900.0) as c:
        saude = c.get("/health").json()
        if saude.get("status") not in ("ok", "degraded"):
            pytest.skip(f"serviço não está pronto: {saude.get('mensagem', saude)}")
        yield c


@pytest.fixture(scope="module")
def ingerido(cliente):
    for doc_id, texto in DOCS.items():
        cliente.delete(f"/documents/{doc_id}")
        resposta = cliente.post("/documents", json={
            "document_id": doc_id,
            "title": doc_id,
            "source": f"{doc_id}.md",
            "chunks": [{"chunk_id": f"{doc_id}#0", "text": texto,
                        "page": 1, "origin": f"{doc_id}.md", "type": "text"}],
        })
        assert resposta.status_code == 200, resposta.text
    yield
    for doc_id in DOCS:
        cliente.delete(f"/documents/{doc_id}")


def test_o_servico_esta_pronto(cliente):
    saude = cliente.get("/health").json()
    assert saude["status"] == "ok", saude.get("mensagem")
    assert saude["llm"] and saude["embedding"]


def test_multi_hop_de_alice_ate_postgresql(cliente, ingerido):
    """A pergunta que o RAG vetorial sozinho pode não resolver."""
    resultados = cliente.post("/search/graph", json={
        "query": "Qual tecnologia de banco de dados aparece relacionada ao "
                 "projeto em que Alice trabalha?",
        "limit": 8,
    }).json()["results"]

    assert resultados, "o grafo não devolveu nada"
    texto = " ".join(r["text"] for r in resultados).lower()
    assert "postgresql" in texto, (
        "o caminho Alice → Orion → PostgreSQL não foi percorrido"
    )


def test_todo_resultado_tem_procedencia(cliente, ingerido):
    """A regra dura, contra um motor de verdade."""
    resultados = cliente.post(
        "/search/graph", json={"query": "Alice", "limit": 8}
    ).json()["results"]

    assert resultados
    for r in resultados:
        assert r["document_id"] in DOCS, f"origem irrecuperável: {r}"
        assert r["source"], "sem rótulo de fonte"


def test_remover_um_documento_nao_apaga_os_outros(cliente, ingerido):
    cliente.delete("/documents/doc_c")
    restantes = {d["document_id"] for d in cliente.get("/documents").json()["documents"]}
    assert "doc_c" not in restantes
    assert {"doc_a", "doc_b"} <= restantes

    # Recoloca, para os outros testes do módulo não sofrerem a ordem.
    cliente.post("/documents", json={
        "document_id": "doc_c", "title": "doc_c", "source": "doc_c.md",
        "chunks": [{"chunk_id": "doc_c#0", "text": DOCS["doc_c"],
                    "page": 1, "origin": "doc_c.md", "type": "text"}],
    })


def test_reconstruir_sem_o_arquivo_original(cliente, ingerido):
    resposta = cliente.post("/documents/doc_a/rebuild")
    assert resposta.status_code == 200, resposta.text
    assert resposta.json()["ok"] is True
