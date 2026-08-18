"""Servidor: autenticação e as rotas que já existem.

A Fase 7 vai acrescentar download do workspace. Estes testes fixam o que já
funciona hoje, para a mudança não derrubar nada em silêncio.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from livia import config
from livia.server import app


@pytest.fixture
def cliente():
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def logado(cliente):
    r = cliente.post("/api/entrar", json={"senha": config.PASSWORD})
    assert r.status_code == 200
    return cliente


# ── autenticação ──────────────────────────────────────────────────────────

PROTEGIDAS = [
    "/api/status",
    "/api/conversations",
    "/api/store/memories",
    "/api/store/skills",
    "/api/persona",
    "/api/biblioteca",
    "/api/backup",
]


@pytest.mark.parametrize("rota", PROTEGIDAS)
def test_sem_sessao_a_api_e_bloqueada(cliente, rota):
    assert cliente.get(rota).status_code == 401


def test_sem_sessao_a_pagina_manda_para_o_login(cliente):
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert "entrar" in r.headers["location"]


def test_saude_fica_livre_para_o_health_check(cliente):
    assert cliente.get("/saude").status_code == 200


def test_senha_errada_e_recusada(cliente):
    assert cliente.post("/api/entrar", json={"senha": "chute"}).status_code == 401


def test_cookie_forjado_e_recusado(cliente):
    from livia import auth
    cliente.cookies.set(auth.COOKIE, "9999999999.assinaturafalsa")
    assert cliente.get("/api/status").status_code == 401


def test_cookie_vencido_e_recusado(cliente):
    import hashlib
    import hmac

    from livia import auth

    vencido = "1000000000"
    assinatura = hmac.new(auth._segredo(), vencido.encode(), hashlib.sha256).hexdigest()
    cliente.cookies.set(auth.COOKIE, f"{vencido}.{assinatura}")
    assert cliente.get("/api/status").status_code == 401


# ── rotas com sessão ──────────────────────────────────────────────────────


@pytest.mark.parametrize("rota", PROTEGIDAS)
def test_com_sessao_as_rotas_respondem(logado, rota):
    assert logado.get(rota).status_code == 200


def test_status_nunca_devolve_segredo(logado):
    corpo = logado.get("/api/status").text
    assert config.PASSWORD not in corpo
    assert "chave-falsa" not in corpo
    for proibido in ("api_key", "GEMINI_API_KEY", "GROQ_API_KEY", "password", "senha"):
        assert proibido not in corpo


def test_colecao_invalida_da_404(logado):
    assert logado.get("/api/store/xpto").status_code == 404


def test_comando_ajuda_nao_chama_modelo(logado):
    r = logado.post("/api/chat", json={"message": "/ajuda"})
    assert r.status_code == 200
    assert "Comandos" in r.text
