"""O sidecar: contrato HTTP, procedência e a blindagem do modo local.

O Cognee NÃO está instalado nesta suíte, e é assim que tem que ser — o motor
é opcional. Onde o comportamento do motor importa, um duble ocupa o lugar
dele. O que se testa aqui é o que a Livia realmente depende: o contrato HTTP,
o registro de procedência e as recusas de segurança.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from services.knowledge import app as servico
from services.knowledge import cognee_engine, config as kconfig, registro


@pytest.fixture(autouse=True)
def _registro_isolado(tmp_path, monkeypatch):
    """Cada teste com o próprio banco de procedência."""
    monkeypatch.setattr(kconfig, "DATA_DIR", tmp_path / "knowledge")
    monkeypatch.setattr(registro, "_criado", False)
    yield
    monkeypatch.setattr(registro, "_criado", False)


@pytest.fixture
def cliente():
    return TestClient(servico.app)


class MotorFalso:
    """Um Knowledge Engine de mentira, com o mesmo formato do de verdade.

    Existe para provar que o CONTRATO funciona sem exigir Cognee, GPU nem
    modelo baixado. Ele grava no mesmo registro de procedência que o motor
    real usa — é justamente essa parte que precisa ser exercitada.
    """

    nome = "falso"

    def __init__(self) -> None:
        self.ingeridos: dict[str, list[dict]] = {}
        self.explodir = False

    async def status(self):
        return {"status": "ok", "engine": self.nome, "llm": True,
                "embedding": True, "graph": True, "version": "0"}

    async def ingest(self, document_id, chunks, meta):
        if self.explodir:
            raise cognee_engine.CogneeError("o motor recusou o documento")
        registro.registrar(
            document_id,
            title=str(meta.get("title") or document_id),
            source=str(meta.get("source") or ""),
            collection_id=str(meta.get("collection_id") or ""),
            chunks=chunks,
            engine="falso:0",
        )
        self.ingeridos[document_id] = chunks
        return {"ok": True, "document_id": document_id,
                "dataset": registro.dataset_de(document_id), "chunks": len(chunks)}

    async def remove(self, document_id):
        self.ingeridos.pop(document_id, None)
        return registro.esquecer(document_id)

    async def graph_search(self, pergunta, limite):
        """Devolve trechos já ingeridos, com procedência costurada."""
        saida = []
        for doc_id, chunks in self.ingeridos.items():
            for c in chunks:
                fonte = registro.procedencia(str(c["text"]), registro.dataset_de(doc_id))
                if fonte is None:
                    continue
                saida.append({
                    "text": c["text"], "source": fonte["source"],
                    "title": fonte["title"], "page": fonte["page"],
                    "document_id": fonte["document_id"],
                    "chunk_id": fonte["chunk_id"],
                    "collection_id": fonte["collection_id"],
                    "ingested_at": fonte["ingested_at"],
                    "retrieval_type": "graph", "tipo": "source",
                    "relation_path": c.get("relation_path"),
                })
        return saida[:limite]


@pytest.fixture
def motor_falso(monkeypatch):
    falso = MotorFalso()
    monkeypatch.setattr(servico, "motor", falso)
    return falso


TRES_DOCS = {
    "doc_a": ("Alice trabalha no Projeto Orion.", "doc_a.md"),
    "doc_b": ("Projeto Orion utiliza PostgreSQL.", "doc_b.md"),
    "doc_c": ("PostgreSQL utiliza MVCC para concorrência.", "doc_c.md"),
}


def ingerir(cliente, doc_id, texto, arquivo, pagina=1):
    return cliente.post("/documents", json={
        "document_id": doc_id,
        "title": doc_id,
        "source": arquivo,
        "chunks": [{"chunk_id": f"{doc_id}#0", "text": texto,
                    "page": pagina, "origin": arquivo, "type": "text"}],
    })


# ══════════════════════════════════════════════════════════════════════════
# O serviço existe mesmo quebrado
# ══════════════════════════════════════════════════════════════════════════


def test_sobe_sem_cognee_instalado(cliente):
    """Um sidecar que se recusa a subir não consegue contar por quê."""
    assert cognee_engine.instalado() is False
    resposta = cliente.get("/health")
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "not_installed"


def test_health_ensina_como_instalar(cliente):
    mensagem = cliente.get("/health").json()["mensagem"]
    assert "requirements-knowledge.txt" in mensagem


def test_health_nunca_derruba(cliente, monkeypatch):
    async def explodir():
        raise RuntimeError("desastre")

    monkeypatch.setattr(servico.motor, "status", explodir)
    resposta = cliente.get("/health")
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "error"


def test_health_com_motor_ok(cliente, motor_falso):
    dados = cliente.get("/health").json()
    assert dados["status"] == "ok" and dados["llm"] is True


# ══════════════════════════════════════════════════════════════════════════
# Ingestão e isolamento por documento
# ══════════════════════════════════════════════════════════════════════════


def test_ingestao_grava_procedencia(cliente, motor_falso):
    resposta = ingerir(cliente, "doc_a", *TRES_DOCS["doc_a"])
    assert resposta.status_code == 200
    assert resposta.json()["ok"] is True

    doc = registro.documento("doc_a")
    assert doc["title"] == "doc_a"
    assert doc["dataset"] == "livia_doc_doc_a"
    assert registro.trechos_de("doc_a")[0]["page"] == 1


def test_cada_documento_tem_dataset_proprio():
    """Um dataset único tornaria impossível apagar um documento só."""
    assert registro.dataset_de("crm-direcional") == "livia_doc_crm_direcional"
    assert registro.dataset_de("doc_a") != registro.dataset_de("doc_b")


def test_dataset_e_seguro_com_nome_estranho():
    assert registro.dataset_de("../../etc/passwd") == "livia_doc_etc_passwd"
    assert registro.dataset_de("") == "livia_doc_sem_nome"


def test_pedido_sem_document_id_e_recusado(cliente, motor_falso):
    resposta = cliente.post("/documents", json={"chunks": [{"text": "x"}]})
    assert resposta.status_code == 400


def test_pedido_sem_trechos_e_recusado(cliente, motor_falso):
    resposta = cliente.post("/documents", json={"document_id": "d", "chunks": []})
    assert resposta.status_code == 400


def test_falha_de_ingestao_devolve_4xx_e_nao_5xx(cliente, motor_falso):
    """5xx faria o disjuntor da Livia castigar o grafo inteiro por causa
    de UM documento problemático."""
    motor_falso.explodir = True
    resposta = ingerir(cliente, "doc_a", *TRES_DOCS["doc_a"])
    assert resposta.status_code == 422
    assert "recusou" in resposta.json()["error"]


def test_remover_um_nao_apaga_os_outros(cliente, motor_falso):
    for doc_id, (texto, arquivo) in TRES_DOCS.items():
        ingerir(cliente, doc_id, texto, arquivo)

    assert cliente.delete("/documents/doc_b").json()["ok"] is True

    restantes = {d["document_id"] for d in registro.documentos()}
    assert restantes == {"doc_a", "doc_c"}


def test_listar_mostra_o_que_o_grafo_conhece(cliente, motor_falso):
    ingerir(cliente, "doc_a", *TRES_DOCS["doc_a"])
    dados = cliente.get("/documents").json()
    assert dados["resumo"]["documentos"] == 1
    assert dados["documents"][0]["document_id"] == "doc_a"


# ══════════════════════════════════════════════════════════════════════════
# Reconstrução sem o arquivo original
# ══════════════════════════════════════════════════════════════════════════


def test_reconstruir_usa_o_registro_e_nao_o_arquivo(cliente, motor_falso):
    """É o que permite "reconstruir conhecimento" funcionar meses depois."""
    ingerir(cliente, "doc_a", *TRES_DOCS["doc_a"])
    motor_falso.ingeridos.clear()               # o motor perdeu tudo

    resposta = cliente.post("/documents/doc_a/rebuild")
    assert resposta.status_code == 200 and resposta.json()["ok"] is True
    assert "doc_a" in motor_falso.ingeridos


def test_reconstruir_documento_desconhecido_da_404(cliente, motor_falso):
    assert cliente.post("/documents/nao-existe/rebuild").status_code == 404


# ══════════════════════════════════════════════════════════════════════════
# Procedência — o coração do serviço
# ══════════════════════════════════════════════════════════════════════════


def test_casamento_exato(cliente, motor_falso):
    ingerir(cliente, "doc_b", *TRES_DOCS["doc_b"], pagina=4)
    fonte = registro.procedencia("Projeto Orion utiliza PostgreSQL.")
    assert fonte["document_id"] == "doc_b"
    assert fonte["page"] == 4
    assert fonte["precisao"] == "trecho"


def test_casamento_tolera_espaco_e_caixa(cliente, motor_falso):
    ingerir(cliente, "doc_b", *TRES_DOCS["doc_b"])
    fonte = registro.procedencia("  projeto   orion\nUTILIZA PostgreSQL.  ")
    assert fonte and fonte["document_id"] == "doc_b"


def test_casamento_por_trecho_contido(cliente, motor_falso):
    """O motor pode recortar o parágrafo. O pedaço ainda tem que casar."""
    texto = ("O Projeto Orion nasceu em 2024 e utiliza PostgreSQL como banco "
             "principal, com réplicas de leitura em três regiões.")
    ingerir(cliente, "doc_b", texto, "doc_b.md", pagina=7)

    fonte = registro.procedencia(
        "O Projeto Orion nasceu em 2024 e utiliza PostgreSQL como banco"
    )
    assert fonte and fonte["page"] == 7


def test_texto_desconhecido_sem_dataset_nao_tem_procedencia():
    """A regra que impede fato órfão de virar citação."""
    assert registro.procedencia("Isto nunca foi ingerido por ninguém.") is None


def test_texto_desconhecido_com_dataset_cai_para_o_documento(cliente, motor_falso):
    """Pior que a página, muito melhor que nada."""
    ingerir(cliente, "doc_a", *TRES_DOCS["doc_a"])
    fonte = registro.procedencia("frase que o motor sintetizou", "livia_doc_doc_a")
    assert fonte["document_id"] == "doc_a"
    assert fonte["page"] is None
    assert fonte["precisao"] == "documento"


def test_esquecer_apaga_a_procedencia_junto(cliente, motor_falso):
    """Procedência apontando para grafo inexistente é pior que nenhuma."""
    ingerir(cliente, "doc_a", *TRES_DOCS["doc_a"])
    cliente.delete("/documents/doc_a")
    assert registro.procedencia(TRES_DOCS["doc_a"][0]) is None


# ══════════════════════════════════════════════════════════════════════════
# Busca
# ══════════════════════════════════════════════════════════════════════════


def test_busca_devolve_resultados_com_origem(cliente, motor_falso):
    for doc_id, (texto, arquivo) in TRES_DOCS.items():
        ingerir(cliente, doc_id, texto, arquivo)

    dados = cliente.post("/search/graph", json={"query": "Alice", "limit": 5}).json()
    assert dados["count"] >= 1
    for r in dados["results"]:
        assert r["document_id"], "todo resultado precisa de origem recuperável"
        assert r["page"] is not None


def test_busca_sem_query_e_recusada(cliente, motor_falso):
    assert cliente.post("/search/graph", json={}).status_code == 400


def test_busca_respeita_o_limite(cliente, motor_falso):
    for doc_id, (texto, arquivo) in TRES_DOCS.items():
        ingerir(cliente, doc_id, texto, arquivo)
    dados = cliente.post("/search/graph", json={"query": "x", "limit": 2}).json()
    assert dados["count"] <= 2


def test_limite_absurdo_e_contido(cliente, motor_falso):
    dados = cliente.post("/search/graph", json={"query": "x", "limit": 99999})
    assert dados.status_code == 200


def test_busca_que_explode_devolve_vazio(cliente, motor_falso, monkeypatch):
    async def explodir(pergunta, limite):
        raise RuntimeError("o grafo pegou fogo")

    monkeypatch.setattr(motor_falso, "graph_search", explodir)
    dados = cliente.post("/search/graph", json={"query": "x"}).json()
    assert dados["results"] == []


def test_nao_existe_busca_vetorial_no_sidecar():
    """O RAG vetorial é da Livia. Dois mecanismos iguais envelhecem mal."""
    caminhos = {r.path for r in servico.rotas}
    assert "/search/vector" not in caminhos
    assert "/search/graph" in caminhos


# ══════════════════════════════════════════════════════════════════════════
# LOCAL_ONLY — a blindagem contra o vazamento silencioso
# ══════════════════════════════════════════════════════════════════════════


def test_local_only_recusa_provedor_de_nuvem(monkeypatch):
    monkeypatch.setattr(kconfig, "LOCAL_ONLY", True)
    monkeypatch.setattr(kconfig, "PROVIDER", "openai")
    problemas = kconfig.conferir()
    assert problemas and "não é local" in problemas[0]


def test_local_only_recusa_endpoint_remoto(monkeypatch):
    monkeypatch.setattr(kconfig, "LOCAL_ONLY", True)
    monkeypatch.setattr(kconfig, "PROVIDER", "ollama")
    monkeypatch.setattr(kconfig, "LLM_ENDPOINT", "https://api.openai.com/v1")
    assert any("não é esta máquina" in p for p in kconfig.conferir())


def test_local_only_recusa_embedding_remoto(monkeypatch):
    """A armadilha documentada do Cognee: configurar só o LLM faz o
    EMBEDDING virar OpenAI em silêncio — e o documento inteiro vaza."""
    monkeypatch.setattr(kconfig, "LOCAL_ONLY", True)
    monkeypatch.setattr(kconfig, "PROVIDER", "ollama")
    monkeypatch.setattr(kconfig, "LLM_ENDPOINT", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(kconfig, "EMBED_ENDPOINT", "https://api.openai.com")
    assert any("embeddings do grafo" in p for p in kconfig.conferir())


def test_local_only_recusa_chave_de_nuvem(monkeypatch):
    monkeypatch.setattr(kconfig, "LOCAL_ONLY", True)
    monkeypatch.setattr(kconfig, "PROVIDER", "ollama")
    monkeypatch.setattr(kconfig, "API_KEY", "sk-proj-chave-de-verdade")
    assert any("API_KEY de nuvem" in p for p in kconfig.conferir())


def test_local_only_aceita_tudo_local(monkeypatch):
    monkeypatch.setattr(kconfig, "LOCAL_ONLY", True)
    monkeypatch.setattr(kconfig, "PROVIDER", "ollama")
    monkeypatch.setattr(kconfig, "LLM_ENDPOINT", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(kconfig, "EMBED_ENDPOINT", "http://127.0.0.1:11434")
    monkeypatch.setattr(kconfig, "API_KEY", "")
    assert kconfig.conferir() == []


def test_fora_do_modo_local_a_nuvem_e_escolha_legitima(monkeypatch):
    monkeypatch.setattr(kconfig, "LOCAL_ONLY", False)
    monkeypatch.setattr(kconfig, "PROVIDER", "openai")
    assert kconfig.conferir() == []


def test_os_dois_lados_sao_sempre_escritos(monkeypatch):
    """A defesa concreta contra "o outro vira OpenAI"."""
    monkeypatch.setattr(kconfig, "PROVIDER", "ollama")
    valores = kconfig.aplicar_no_ambiente()
    assert valores["LLM_PROVIDER"] == "ollama"
    assert valores["EMBEDDING_PROVIDER"] == "ollama", (
        "deixar o embedding em branco faz o Cognee cair na OpenAI sozinho"
    )
    assert valores["EMBEDDING_DIMENSIONS"]
    assert valores["TELEMETRY_DISABLED"] == "1"


def test_o_resumo_nunca_expoe_a_chave(monkeypatch):
    monkeypatch.setattr(kconfig, "API_KEY", "sk-segredo-absoluto")
    import json
    assert "sk-segredo" not in json.dumps(kconfig.resumo())


# ══════════════════════════════════════════════════════════════════════════
# Tradução do formato do motor
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "bruto,texto_esperado",
    [
        ("uma string pura", "uma string pura"),
        ({"text": "campo text"}, "campo text"),
        ({"content": "campo content"}, "campo content"),
        ({"description": "campo description"}, "campo description"),
        ({"nada_conhecido": "x"}, ""),
    ],
)
def test_desmontar_aceita_varios_formatos(bruto, texto_esperado):
    """Cada versão do Cognee devolve uma forma um pouco diferente."""
    texto, _, _ = cognee_engine._desmontar(bruto)
    assert texto == texto_esperado


def test_desmontar_reconhece_tripla_solta():
    _, caminho, _ = cognee_engine._desmontar({
        "text": "algo", "source_node": "Alice",
        "relation": "trabalha em", "target_node": "Orion",
    })
    assert caminho == ["Alice", "trabalha em", "Orion"]


def test_desmontar_reconhece_caminho_em_lista():
    _, caminho, _ = cognee_engine._desmontar({
        "text": "algo", "relation_path": ["A", "liga", "B"],
    })
    assert caminho == ["A", "liga", "B"]


def test_desmontar_nao_quebra_com_lixo():
    texto, caminho, dataset = cognee_engine._desmontar(object())
    assert texto == "" and caminho == [] and dataset == ""
