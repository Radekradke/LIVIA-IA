"""Roteador: classificação local e montagem da fila.

Tudo aqui roda sem rede e sem IA — é essa a premissa do módulo. Se algum
teste precisar de mock de HTTP, o roteador deixou de ser determinístico.
"""

from __future__ import annotations

import pytest

from livia import config, router


@pytest.fixture
def tres(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "x", raising=False)
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini", "openrouter"])


# ── classificação ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mensagem,esperado",
    [
        ("oi, tudo bem?", "conversa"),
        ("resume https://example.com pra mim", "com_link"),
        ("veja http://a.com/x?y=1", "com_link"),
        ("refatora esse trecho pra ficar mais legível", "tecnico"),
        ("qual a arquitetura ideal pra esse caso?", "tecnico"),
        ("me ajuda a depurar esse traceback", "tecnico"),
        ("escreve um texto longo " * 20, "geral"),
    ],
)
def test_classificacao(mensagem, esperado):
    assert router.classificar(mensagem).kind == esperado


def test_ferramentas_exigem_capacidade_tools():
    perfil = router.classificar("liste meus arquivos", precisa_ferramentas=True)
    assert perfil.kind == "ferramentas"
    assert router.TOOLS in perfil.required_capabilities


def test_estruturado_exige_capacidade_structured():
    perfil = router.classificar("qualquer", precisa_estruturado=True)
    assert router.STRUCTURED in perfil.required_capabilities


def test_documento_prefere_contexto_longo():
    perfil = router.classificar("resume o capítulo", tem_documento=True)
    assert perfil.preferred_provider == "gemini"
    assert router.LONG_CONTEXT in perfil.required_capabilities


def test_pergunta_generica_nao_vira_tecnica():
    """'como funciona' é conversa. Se virasse tarefa técnica, tudo viraria."""
    assert router.classificar("como funciona uma API?").kind == "conversa"


# ── fila ──────────────────────────────────────────────────────────────────


def test_link_vai_para_o_gemini(tres):
    _, ordem = router.escolher("olha https://python.org")
    assert ordem[0] == "gemini"


def test_conversa_curta_vai_para_o_rapido(tres):
    _, ordem = router.escolher("bom dia")
    assert ordem[0] == "groq"


def test_tarefa_tecnica_vai_para_o_especialista(tres):
    _, ordem = router.escolher("refatora esse código por favor")
    assert ordem[0] == "openrouter"


def test_openrouter_sai_da_fila_quando_ha_ferramentas(tres):
    """Ele não declara `tools`. Tentar e falhar seria pior que não tentar."""
    _, ordem = router.escolher("liste os arquivos", precisa_ferramentas=True)
    assert "openrouter" not in ordem
    assert set(ordem) == {"groq", "gemini"}


def test_openrouter_sai_da_fila_no_estruturado(tres):
    _, ordem = router.escolher("x", precisa_estruturado=True)
    assert "openrouter" not in ordem


def test_link_sem_gemini_cai_para_quem_sobrou(monkeypatch):
    """Sem Gemini ninguém abre link — mas a conversa não pode morrer."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])

    perfil, ordem = router.escolher("veja https://example.com")
    assert perfil.preferred_provider == "gemini"
    assert ordem == [], "nenhum provedor tem url_context; quem chama decide o fallback"

    # A fila de conversa comum continua funcionando.
    _, comum = router.escolher("oi")
    assert comum == ["groq"]


def test_fila_respeita_a_ordem_do_env(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "PROVIDERS", ["gemini", "groq"])

    _, ordem = router.escolher("texto sem preferencia " * 30)
    assert ordem == ["gemini", "groq"], "a escolha do André vem antes do catálogo"


def test_chave_sem_estar_no_env_entra_no_fim(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "x", raising=False)
    monkeypatch.setattr(config, "PROVIDERS", ["groq"])
    assert router.disponiveis() == ["groq", "gemini", "openrouter"]


def test_provedor_sem_chave_nao_entra(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    assert router.disponiveis() == ["gemini"]


def test_quem_tem_embeddings(tres):
    assert router.quem_tem(router.EMBEDDINGS) == ["gemini"]


def test_so_com_gemini_tudo_ainda_funciona(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])

    for msg, ferr in [("oi", False), ("liste arquivos", True), ("veja https://a.com", False)]:
        _, ordem = router.escolher(msg, precisa_ferramentas=ferr)
        assert ordem == ["gemini"], f"'{msg}' deveria cair no Gemini"


def test_so_com_groq_conversa_e_ferramentas_funcionam(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "", raising=False)
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])

    assert router.escolher("oi")[1] == ["groq"]
    assert router.escolher("liste", precisa_ferramentas=True)[1] == ["groq"]
    assert router.quem_tem(router.EMBEDDINGS) == [], "a Groq não gera vetores"
