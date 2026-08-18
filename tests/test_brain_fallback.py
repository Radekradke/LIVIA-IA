"""Cadeia de provedores: quando trocar e quando não trocar.

A distinção que importa aqui é entre falha passageira e erro de
configuração. Cota estourada e servidor fora pedem o próximo provedor;
chave inválida e modelo inexistente, não — trocar só esconderia o problema
real e o André levaria dias para descobrir.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from livia import brain, config

GROQ = "https://api.groq.com/openai/v1/chat/completions"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"


async def coletar(**kwargs) -> tuple[str, list[str]]:
    usados: list[str] = []
    partes: list[str] = []
    async for p in brain.stream(
        "sistema", [{"role": "user", "content": "oi"}], usados=usados, **kwargs
    ):
        partes.append(p)
    return "".join(partes), usados


def sse_groq(texto: str) -> str:
    import json
    linhas = [
        "data: " + json.dumps({"choices": [{"delta": {"content": texto}}]}),
        "data: [DONE]",
    ]
    return "\n\n".join(linhas) + "\n\n"


def sse_gemini(texto: str) -> str:
    import json
    corpo = {"candidates": [{"content": {"parts": [{"text": texto}]}}]}
    return "data: " + json.dumps(corpo) + "\n\n"


# ── falha passageira: troca de provedor ───────────────────────────────────


@respx.mock
async def test_429_na_groq_passa_para_o_gemini(monkeypatch):
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    respx.post(GROQ).mock(return_value=httpx.Response(429, json={"error": "rate limit"}))
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("resposta do reserva"))
    )

    texto, usados = await coletar()
    assert usados == ["gemini"]
    assert "reserva" in texto


@respx.mock
async def test_500_na_groq_passa_para_o_gemini(monkeypatch):
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    respx.post(GROQ).mock(return_value=httpx.Response(503, json={"error": "indisponivel"}))
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("ok"))
    )

    _, usados = await coletar()
    assert usados == ["gemini"]


@respx.mock
async def test_timeout_passa_para_o_proximo(monkeypatch):
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    monkeypatch.setattr(brain, "ESPERA_BASE", 0)
    respx.post(GROQ).mock(side_effect=httpx.ReadTimeout("demorou"))
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("chegou"))
    )

    texto, usados = await coletar()
    assert usados == ["gemini"] and "chegou" in texto


# ── erro de configuração: NÃO troca ───────────────────────────────────────


@respx.mock
async def test_chave_invalida_nao_aciona_fallback(monkeypatch):
    """401 é problema de configuração. Trocar de provedor esconderia isso."""
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    rota_groq = respx.post(GROQ).mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid key"}})
    )
    rota_gemini = respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("nao deveria ser usado"))
    )

    with pytest.raises(brain.BrainError) as erro:
        await coletar()

    assert "chave" in str(erro.value).lower()
    assert rota_groq.called
    assert not rota_gemini.called, "não deveria ter tentado o reserva"


@respx.mock
async def test_modelo_inexistente_repassa_a_mensagem_da_api(monkeypatch):
    monkeypatch.setattr(config, "PROVIDERS", ["gemini"])
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(
            404,
            json={"error": {"message": "This model is no longer available. Use gemini-9."}},
        )
    )

    with pytest.raises(brain.BrainError) as erro:
        await coletar()

    # A mensagem do Google diz qual modelo usar. Esconder isso seria cruel.
    assert "gemini-9" in str(erro.value)


# ── casos-limite ──────────────────────────────────────────────────────────


async def test_sem_nenhuma_chave_da_erro_util(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")

    with pytest.raises(brain.BrainError) as erro:
        await coletar()

    msg = str(erro.value)
    assert "chave" in msg.lower() and ".env" in msg


@respx.mock
async def test_todos_fora_consolida_os_motivos(monkeypatch):
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    respx.post(GROQ).mock(return_value=httpx.Response(429))
    respx.post(url__startswith=GEMINI).mock(return_value=httpx.Response(429))

    with pytest.raises(brain.BrainError) as erro:
        await coletar()

    msg = str(erro.value)
    assert "groq" in msg and "gemini" in msg
    assert msg.count("groq") == 1, "tentativas repetidas deveriam virar uma linha só"


@respx.mock
async def test_preferir_muda_a_ordem(monkeypatch):
    """Mensagem com link vai para o Gemini mesmo com a Groq como padrão."""
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("groq")))
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("gemini"))
    )

    _, usados = await coletar(preferir="gemini")
    assert usados == ["gemini"]


def test_provedor_sem_chave_sai_da_fila(monkeypatch):
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    assert brain._provedores() == ["gemini"]
