"""Voz → texto: o pedido que vai pro Groq e os erros que voltam pro André."""

from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient

from livia import config, voz
from livia.server import app


def _groq_falso(monkeypatch, resposta: httpx.Response, pedidos: list):
    real = httpx.AsyncClient

    def handler(req: httpx.Request) -> httpx.Response:
        pedidos.append(req)
        return resposta

    monkeypatch.setattr(voz.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(config, "GROQ_API_KEY", "gsk_teste")


async def test_transcreve_em_portugues_com_vocabulario(monkeypatch):
    pedidos: list = []
    _groq_falso(monkeypatch, httpx.Response(200, json={"text": " ideia de jogo de cartas "}), pedidos)
    texto = await voz.transcrever(b"\x00" * 5000, "audio/mp4")
    assert texto == "ideia de jogo de cartas"
    corpo = pedidos[0].content
    assert b"whisper-large-v3-turbo" in corpo and b'name="language"\r\n\r\npt' in corpo
    assert b'filename="fala.m4a"' in corpo        # o iPhone manda mp4
    assert b"Livia" in corpo                        # vocabulário vai no prompt


async def test_sem_chave_explica(monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(voz.VozError, match="GROQ_API_KEY"):
        await voz.transcrever(b"123", "audio/webm")


async def test_cota_estourada_vira_mensagem_legivel(monkeypatch):
    _groq_falso(monkeypatch, httpx.Response(429, json={}), [])
    with pytest.raises(voz.VozError, match="Cota"):
        await voz.transcrever(b"\x00" * 5000, "audio/webm")


def test_rota_de_voz(monkeypatch):
    _groq_falso(monkeypatch, httpx.Response(200, json={"text": "oi livia"}), [])
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/api/voz", content=b"x").status_code == 401   # precisa de sessão
        assert c.post("/api/entrar", json={"senha": config.PASSWORD}).status_code == 200
        r = c.post("/api/voz", content=b"\x00" * 5000, headers={"Content-Type": "audio/webm"})
        assert r.json() == {"texto": "oi livia"}
        assert c.post("/api/voz", content=b"").status_code == 400


async def test_ollama_desligado_nao_prende_os_vetores(monkeypatch):
    """PC desligado: a conexão desiste rápido em vez de esperar o timeout inteiro."""
    import time

    from livia import embeddings

    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://10.255.255.1:11434")  # não roteável
    monkeypatch.setattr(config, "OLLAMA_TIMEOUT", 300.0)
    inicio = time.monotonic()
    with pytest.raises(Exception):
        await embeddings._ollama(["oi"], embeddings.DOCUMENTO)
    assert time.monotonic() - inicio < 12
