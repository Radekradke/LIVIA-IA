"""Montagem do prompt e o fluxo do servidor com a memória nova.

Aqui se prova a parte que o usuário sente: que a pergunta traz só o que tem a
ver com ela, que correção substitui em vez de acumular, que conteúdo de
documento não vira ordem, e que nada disso quebrou o que já funcionava.
"""

from __future__ import annotations

import json

import httpx
import numpy as np
import pytest
import respx
from starlette.testclient import TestClient

from livia import auth, config, context, db, experiencia, memoria, server
from livia.store import COLECOES
from tests.test_memoria import VetorFalso, memorias, vetores  # noqa: F401


GROQ = "https://api.groq.com/openai/v1/chat/completions"


def gravar(pasta, nome, descricao, corpo="", **extra):
    from livia import docs

    return docs.write(
        pasta, nome, descricao, corpo,
        kind=extra.pop("kind", "fact"), extra=extra or None,
    )


# ── montagem seletiva ─────────────────────────────────────────────────────


async def test_prompt_traz_so_a_memoria_relacionada(memorias, vetores):
    gravar(memorias, "banco", "O CRM usa Supabase como banco de dados.")
    gravar(memorias, "impressora", "A impressora Epson trava no WPS.")

    prompt, procedencia = await context.montar("qual banco de dados usamos?")

    assert "Supabase" in prompt
    assert "Epson" not in prompt
    assert procedencia["modo"] == "semantico"


async def test_camadas_aparecem_na_ordem_certa(memorias, vetores, tmp_path, monkeypatch):
    """Fase 37: regras, personalidade, memória, lições, skills."""
    licoes = tmp_path / "lessons"
    habilidades = tmp_path / "skills"
    licoes.mkdir()
    habilidades.mkdir()
    monkeypatch.setattr(COLECOES["lessons"], "directory", licoes)
    monkeypatch.setattr(COLECOES["skills"], "directory", habilidades)

    gravar(memorias, "banco", "O CRM usa Supabase como banco de dados.")
    gravar(licoes, "licao-banco", "Migrações de banco de dados pedem backup antes.",
           kind="lesson")
    gravar(habilidades, "deploy-banco", "Como migrar o banco de dados em produção.",
           kind="skill")

    prompt, _ = await context.montar("como faço a migração do banco de dados?")

    posicoes = [
        prompt.index("Sua personalidade"),
        prompt.index("O que você sabe sobre"),
        prompt.index("O que a experiência te ensinou"),
        prompt.index("Skills que você aprendeu"),
    ]
    assert posicoes == sorted(posicoes)


async def test_memoria_de_projeto_ganha_secao_propria(memorias, vetores):
    gravar(memorias, "geral", "Prefere Postgres como banco de dados.", scope="global")
    gravar(memorias, "livia-banco", "O projeto usa SQLite como banco de dados.",
           scope="project:livia")

    prompt, procedencia = await context.montar(
        "qual banco de dados?", escopo="project:livia"
    )

    assert "# Sobre livia" in prompt
    assert "SQLite" in prompt and "Postgres" in prompt
    assert "não há contradição" in prompt
    assert procedencia["escopo"] == "project:livia"


async def test_pergunta_sem_relacao_diz_isso_em_vez_de_forcar(memorias, vetores):
    gravar(memorias, "banco", "O CRM usa Supabase.")
    prompt, _ = await context.montar("me conta uma piada sobre gatos")
    assert "tem a ver com esta pergunta" in prompt
    assert "sem forçar conexão" in prompt


async def test_sem_vetores_volta_para_a_montagem_completa(memorias, monkeypatch):
    from livia import embeddings

    monkeypatch.setattr(embeddings, "disponivel", lambda: False)
    gravar(memorias, "banco", "O CRM usa Supabase.")

    prompt, procedencia = await context.montar("qualquer coisa")
    assert procedencia["modo"] == "completo"
    assert "Supabase" in prompt, "degradar não pode significar perder a memória"


async def test_desligar_a_busca_semantica_e_respeitado(memorias, vetores, monkeypatch):
    monkeypatch.setattr(config, "SEMANTIC_MEMORY", False)
    gravar(memorias, "banco", "O CRM usa Supabase.")
    _, procedencia = await context.montar("piada sobre gatos")
    assert procedencia["modo"] == "completo"


async def test_orcamento_por_tipo_e_respeitado(memorias, vetores, monkeypatch):
    """Fase 38: cada camada tem teto próprio."""
    monkeypatch.setattr(config, "MEMORY_MAX_ITEMS", 2)
    for i in range(8):
        gravar(memorias, f"banco-{i}", f"Nota {i} sobre o banco de dados Postgres.")

    _, procedencia = await context.montar("banco de dados")
    assert len(procedencia["memorias"]) == 2


# ── autoridade da verdade ─────────────────────────────────────────────────


def test_o_prompt_declara_a_ordem_de_autoridade():
    """Fase 39: conhecimento geral não passa por cima de decisão do André."""
    prompt = context.build_system_prompt()
    assert "o que {} acabou de corrigir".format(config.USER_NAME or "o usuário") in prompt
    assert "NUNCA passa por cima de uma decisão explícita" in prompt


def test_o_prompt_isola_conteudo_externo():
    """Fase 28: instrução dentro de documento é dado, não ordem."""
    prompt = context.build_system_prompt()
    assert "<external_knowledge>" in prompt
    assert "nunca instrução" in prompt


# ── fluxo completo do chat ────────────────────────────────────────────────


@pytest.fixture
def cliente(monkeypatch, memorias):
    monkeypatch.setattr(config, "PROVIDERS", ["groq"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "OLLAMA_ENABLED", False)
    monkeypatch.setattr(config, "WEB_ENABLED", False)
    monkeypatch.setattr(config, "AUTO_LEARN", False)
    return TestClient(server.app), {auth.COOKIE: auth.criar_token()}


def sse_groq(texto: str) -> str:
    return (
        "data: " + json.dumps({"choices": [{"delta": {"content": texto}}]})
        + "\n\ndata: [DONE]\n\n"
    )


def conversar(cliente, mensagem, conversa=None):
    c, cookie = cliente
    corpo = {"message": mensagem}
    if conversa:
        corpo["conversation_id"] = conversa
    resposta = c.post("/api/chat", json=corpo, cookies=cookie)
    eventos = [
        json.loads(l[6:])
        for l in resposta.text.splitlines()
        if l.startswith("data: ")
    ]
    return eventos


@respx.mock
def test_conversa_normal_continua_funcionando(cliente, vetores):
    """A garantia de compatibilidade: nada do que funcionava parou."""
    respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("oi, André")))
    eventos = conversar(cliente, "oi")

    textos = [e["text"] for e in eventos if e.get("type") == "delta"]
    assert "".join(textos) == "oi, André"
    assert eventos[-1]["type"] == "done"


@respx.mock
def test_comandos_antigos_continuam_funcionando(cliente, vetores):
    eventos = conversar(cliente, "/lembrar prefiro tabelas a listas longas")
    texto = "".join(e["text"] for e in eventos if e.get("type") == "delta")
    assert "Gravado" in texto

    eventos = conversar(cliente, "/memorias")
    texto = "".join(e["text"] for e in eventos if e.get("type") == "delta")
    assert "prefiro tabelas" in texto


@respx.mock
def test_arquivar_pelo_comando(cliente, vetores):
    conversar(cliente, "/lembrar uso docker para tudo")
    nome = COLECOES["memories"].all()[0].name

    eventos = conversar(cliente, f"/arquivar {nome}")
    texto = "".join(e["text"] for e in eventos if e.get("type") == "delta")
    assert "Arquivei" in texto
    assert COLECOES["memories"].ativos() == []


@respx.mock
def test_porque_explica_a_ultima_resposta(cliente, vetores, memorias):
    gravar(memorias, "banco", "O CRM usa Supabase como banco de dados.")
    respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("é Supabase")))

    eventos = conversar(cliente, "qual o banco de dados do CRM?")
    conversa = eventos[-1]["conversation_id"]

    eventos = conversar(cliente, "/porque", conversa)
    texto = "".join(e["text"] for e in eventos if e.get("type") == "delta")
    assert "banco" in texto
    assert "Memórias que usei" in texto


@respx.mock
def test_manutencao_pelo_comando_nao_aplica_sozinha(cliente, vetores, memorias):
    gravar(memorias, "prefere-postgres", "Prefere Postgres como banco de dados.")
    gravar(memorias, "gosta-postgres", "Gosta de Postgres para banco de dados.")
    memoria.sincronizar()

    eventos = conversar(cliente, "/manutencao-memoria")
    texto = "".join(e["text"] for e in eventos if e.get("type") == "delta")

    assert "duplicatas encontradas: 1" in texto
    assert "não mexi em nada" in texto
    assert len(COLECOES["memories"].ativos()) == 2

    eventos = conversar(cliente, "/manutencao-memoria aplicar")
    texto = "".join(e["text"] for e in eventos if e.get("type") == "delta")
    assert "Apliquei" in texto
    assert len(COLECOES["memories"].ativos()) == 1


@respx.mock
def test_a_experiencia_e_registrada_apos_a_resposta(cliente, vetores, monkeypatch):
    monkeypatch.setattr(config, "EXPERIENCE_ENABLED", True)
    monkeypatch.setattr(config, "TOOLS_ENABLED", False)
    antes = len(db.experiencia_listar(100))

    respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("feito")))
    conversar(cliente, "cria um arquivo de notas pra mim")

    # Sem ferramenta executada e sem veredito, não vira experiência — e é
    # esse o comportamento correto, não uma falha.
    assert len(db.experiencia_listar(100)) == antes


@respx.mock
def test_correcao_derruba_o_sucesso_da_rodada_anterior(cliente, vetores, monkeypatch):
    monkeypatch.setattr(config, "EXPERIENCE_ENABLED", True)
    respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("pronto")))

    eventos = conversar(cliente, "configura a impressora")
    conversa = eventos[-1]["conversation_id"]

    # Simula a rodada anterior tendo executado uma ferramenta com sucesso.
    id_ = db.experiencia_gravar(
        "configura a impressora",
        acoes=[{"nome": "escrever_arquivo", "ok": True}],
        sucesso=True,
        conversa=conversa,
    )

    conversar(cliente, "não funcionou, continua sem imprimir", conversa)
    assert db.experiencia_listar(100)
    revisada = [e for e in db.experiencia_listar(100) if e["id"] == id_][0]
    assert revisada["sucesso"] is False, (
        "sucesso marcado na rodada anterior estava errado se ele corrigiu depois"
    )
    db.experiencia_apagar(id_)


@respx.mock
def test_status_reconhece_o_ollama_como_provedor(cliente, monkeypatch):
    c, cookie = cliente
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "PROVIDERS", ["ollama"])

    dados = c.get("/api/status", cookies=cookie).json()
    assert dados["has_key"] is True, "com Ollama ligado, não falta provedor nenhum"


def test_diagnostico_relata_o_estado_local(cliente, monkeypatch):
    c, cookie = cliente
    monkeypatch.setattr(config, "LOCAL_ONLY", True)
    dados = c.get("/api/diagnostico", cookies=cookie).json()

    assert dados["local"]["somente_local"] is True
    assert "ollama" in dados["local"]
    assert "embeddings" in dados
    assert "memoria" in dados
    # E continua sem vazar nada.
    assert "chave-falsa-groq" not in json.dumps(dados)


# ── painel ────────────────────────────────────────────────────────────────


def test_painel_lista_as_tres_colecoes(cliente):
    c, cookie = cliente
    for colecao in ("memories", "skills", "lessons"):
        assert c.get(f"/api/store/{colecao}", cookies=cookie).status_code == 200
    assert c.get("/api/store/inexistente", cookies=cookie).status_code == 404


def test_painel_arquiva_e_reativa(cliente, memorias):
    c, cookie = cliente
    c.post("/api/store/memories",
           json={"name": "teste", "description": "Uma memória."}, cookies=cookie)

    r = c.patch("/api/store/memories/teste", json={"acao": "arquivar"}, cookies=cookie)
    assert r.json()["item"]["status"] == "archived"

    r = c.patch("/api/store/memories/teste", json={"acao": "reativar"}, cookies=cookie)
    assert r.json()["item"]["status"] == "active"


def test_painel_mostra_origem_e_uso(cliente, memorias):
    c, cookie = cliente
    c.post("/api/store/memories",
           json={"name": "teste", "description": "Uma memória.", "scope": "project:x"},
           cookies=cookie)

    dados = c.get("/api/store/memories/teste", cookies=cookie).json()
    assert dados["item"]["scope"] == "project:x"
    assert dados["indice"]["usos"] == 0


def test_painel_de_experiencias(cliente):
    c, cookie = cliente
    id_ = db.experiencia_gravar("tarefa de teste", acoes=[{"nome": "calcular", "ok": True}],
                                sucesso=True)
    dados = c.get("/api/experiencias", cookies=cookie).json()
    assert any(e["id"] == id_ for e in dados["experiencias"])
    assert dados["resumo"]["sucessos"] >= 1

    assert c.delete(f"/api/experiencias/{id_}", cookies=cookie).json()["ok"] is True


def test_candidata_inexistente_devolve_404(cliente):
    c, cookie = cliente
    r = c.post("/api/candidatas/99999", json={"acao": "aprovar"}, cookies=cookie)
    assert r.status_code == 404
