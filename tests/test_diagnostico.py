"""Fase 10 — o diagnóstico que a aba Estado consome.

A regra que vale mais que todas as outras deste arquivo: nenhuma chave pode
aparecer. É uma tela feita para ser aberta quando algo dá errado, e é
justamente aí que se tira print e se manda para alguém.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from livia import config, saude, server


@pytest.fixture
def cliente(dados, workspace, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", dados / "livia.db")
    with TestClient(server.app, base_url="https://testserver") as c:
        assert c.post("/api/entrar", json={"senha": config.PASSWORD}).status_code == 200
        yield c


def test_o_retrato_tem_o_que_a_aba_precisa(cliente):
    d = cliente.get("/api/diagnostico").json()

    for chave in ("provedores", "workspace", "banco", "biblioteca", "memoria",
                  "ferramentas", "web", "protegido", "momento"):
        assert chave in d, f"faltou {chave}"

    assert set(d["provedores"]) == {"gemini", "groq", "openrouter"}


@pytest.mark.parametrize(
    "segredo",
    ["chave-falsa-gemini", "chave-falsa-groq", "senha-de-teste"],
)
def test_nenhuma_chave_aparece(cliente, segredo):
    """Esta tela é aberta quando algo deu errado — e é aí que se tira print."""
    bruto = cliente.get("/api/diagnostico").text
    assert segredo not in bruto, f"vazou: {segredo}"


def test_nao_vaza_por_nome_de_campo(cliente):
    bruto = cliente.get("/api/diagnostico").text.lower()
    for suspeito in ("api_key", "apikey", "bearer", "password", "cookie", "token"):
        assert suspeito not in bruto, f"campo suspeito: {suspeito}"


def test_provedor_sem_chave_aparece_como_nao_configurado(cliente):
    p = cliente.get("/api/diagnostico").json()["provedores"]
    assert p["openrouter"]["configurado"] is False, "o conftest deixa esta em branco"
    assert p["groq"]["configurado"] is True


def test_provedor_quebrado_mostra_o_motivo(cliente):
    saude.registrar_falha("groq", "chave", "groq: chave recusada — confira o .env")
    p = cliente.get("/api/diagnostico").json()["provedores"]["groq"]

    assert p["quebrado"] is True and p["disponivel"] is False
    assert ".env" in p["ultimo_erro"]


def test_provedor_de_castigo_diz_quanto_falta(cliente):
    saude.registrar_falha("gemini", "cota", "gemini: cota atingida")
    p = cliente.get("/api/diagnostico").json()["provedores"]["gemini"]

    assert p["quebrado"] is False, "cota passa sozinha; não é quebra"
    assert p["disponivel"] is False
    assert 0 < p["descanso_segundos"] <= saude.DESCANSO["cota"]


def test_conta_as_conversas(cliente):
    """É a prova mais direta de que o disco persiste: em hospedagem de disco
    efêmero o número volta a zero sozinho."""
    from livia import db

    assert cliente.get("/api/diagnostico").json()["banco"]["conversas"] == 0
    db.create_conversation("Uma conversa")
    assert cliente.get("/api/diagnostico").json()["banco"]["conversas"] == 1


def test_pasta_ilegivel_aparece_como_problema(cliente, monkeypatch, tmp_path):
    """Sem este aviso, nada do que ela criar é salvo e ninguém percebe."""
    monkeypatch.setattr(config, "WORKSPACE", tmp_path / "nao-existe" / "\x00invalido")
    assert cliente.get("/api/diagnostico").json()["workspace"]["gravavel"] is False


def test_livro_invalidado_e_contado(cliente, monkeypatch):
    from livia import biblioteca

    monkeypatch.setattr(
        biblioteca, "listar",
        lambda: [{"titulo": "A", "compativel": True}, {"titulo": "B", "compativel": False}],
    )
    bib = cliente.get("/api/diagnostico").json()["biblioteca"]
    assert bib["documentos"] == 2 and bib["invalidos"] == 1


def test_conta_as_ferramentas(cliente):
    from livia import ferramentas

    d = cliente.get("/api/diagnostico").json()["ferramentas"]
    assert d["ligadas"] is True
    assert d["quantas"] == len(ferramentas.catalogo())


def test_sem_sessao_o_diagnostico_e_recusado():
    with TestClient(server.app, base_url="https://testserver") as anonimo:
        assert anonimo.get("/api/diagnostico").status_code == 401


def test_uma_sonda_quebrada_nao_derruba_o_retrato(cliente, monkeypatch):
    """A tela é consultada JUSTAMENTE quando algo está quebrado. Uma sonda que
    estoura não pode levar junto a explicação do problema."""
    from livia import biblioteca, db

    def explode(*a, **k):
        raise RuntimeError("banco corrompido")

    monkeypatch.setattr(db, "count_conversations", explode)
    monkeypatch.setattr(biblioteca, "listar", explode)

    r = cliente.get("/api/diagnostico")
    assert r.status_code == 200, "o diagnóstico caiu junto com o problema"

    d = r.json()
    assert d["banco"]["conversas"] is None
    assert d["biblioteca"]["documentos"] == 0
    assert d["provedores"], "o resto do retrato deveria continuar chegando"


def test_erro_de_sonda_nao_vira_stack_trace_na_resposta(cliente, monkeypatch):
    from livia import db

    def explode(*a, **k):
        raise RuntimeError("SEGREDO_NO_TRACEBACK")

    monkeypatch.setattr(db, "count_conversations", explode)
    assert "SEGREDO_NO_TRACEBACK" not in cliente.get("/api/diagnostico").text
