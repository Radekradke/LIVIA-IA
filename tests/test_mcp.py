"""A Livia como servidor MCP: catálogo, chamadas e a trava do token."""

from __future__ import annotations

import pytest
from mcp import Client, types
from starlette.testclient import TestClient

from livia import config, mcp_livia, nebulosa
from livia.server import app
from tests.test_nebulosa import _ceu_vazio  # noqa: F401  (fixture autouse)


async def test_catalogo_e_o_mesmo_do_chat_mais_memoria():
    async with Client(mcp_livia.servidor()) as c:
        nomes = {t.name for t in (await c.list_tools()).tools}
    assert {"criar_ideia", "registrar_relatorio", "buscar_ideias", "buscar_memoria", "lembrar"} <= nomes


async def test_relatorio_pelo_mcp_descobre_a_ia_pelo_nome_do_cliente():
    i = nebulosa.criar("jogo de combos")
    quem = types.Implementation(name="claude-code", version="1")
    async with Client(mcp_livia.servidor(), client_info=quem) as c:
        r = await c.call_tool("registrar_relatorio", {"id": i["id"], "texto": "fechei o loop"})
    assert not r.is_error
    assert nebulosa.notas(i["id"])[0]["ia"] == "claude"


async def test_erro_de_ferramenta_volta_como_resposta_e_nao_derruba():
    async with Client(mcp_livia.servidor()) as c:
        r = await c.call_tool("ver_ideia", {"id": 99999})
        assert r.is_error
        assert (await c.list_tools()).tools  # a sessão continua de pé


@pytest.mark.parametrize("nome,ia", [
    ("claude-code", "claude"), ("gemini-cli", "gemini"), ("Antigravity", "gemini"),
    ("codex", "gpt"), ("cursor", "outra"), ("", ""),
])
def test_ia_do_cliente(nome, ia):
    assert mcp_livia.ia_do_cliente(nome) == ia


def test_http_sem_token_configurado_a_porta_nao_existe(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOKEN", "")
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/mcp", json={}).status_code == 404


def test_http_exige_o_token_certo(monkeypatch):
    monkeypatch.setattr(config, "MCP_TOKEN", "chave-certa")
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/mcp", json={}).status_code == 401
        assert c.post("/mcp", json={}, headers={"Authorization": "Bearer errada"}).status_code == 401
        r = c.post("/mcp", headers={"Authorization": "Bearer chave-certa",
                                    "Accept": "application/json, text/event-stream"},
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
                         })
        assert r.status_code != 401 and r.status_code != 404
