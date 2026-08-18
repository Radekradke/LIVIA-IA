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
async def test_chave_invalida_nao_derruba_os_outros(monkeypatch):
    """Uma chave errada tira AQUELE provedor, não a conversa.

    A versão anterior deste teste exigia o oposto: 401 derrubava tudo, para
    o erro não passar despercebido. Resiliência com aviso é melhor que
    rigidez — o provedor sai de circulação, `saude.quebrados()` registra, e
    a interface avisa. O André descobre sem ficar sem assistente.
    """
    from livia import saude

    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    respx.post(GROQ).mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid key"}})
    )
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("o reserva atendeu"))
    )

    texto, usados = await coletar()
    assert usados == ["gemini"] and "reserva" in texto
    assert "groq" in saude.quebrados(), "a Groq deveria ficar marcada"
    assert not saude.disponivel("groq")


@respx.mock
async def test_provedor_quebrado_nao_e_tentado_de_novo(monkeypatch):
    """Depois de marcado, ele nem entra na fila — sem gastar outra chamada."""
    from livia import saude

    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    rota_groq = respx.post(GROQ).mock(return_value=httpx.Response(401))
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("ok"))
    )

    await coletar()
    chamadas_iniciais = rota_groq.call_count

    await coletar()
    assert rota_groq.call_count == chamadas_iniciais, "não deveria tentar de novo"


@respx.mock
async def test_modelo_inexistente_repassa_a_mensagem_da_api(monkeypatch):
    """Sem outro provedor, o erro sobe — e leva junto o recado da API."""
    monkeypatch.setattr(config, "PROVIDERS", ["gemini"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(
            404,
            json={"error": {"message": "This model is no longer available. Use gemini-9."}},
        )
    )

    with pytest.raises(brain.BrainError) as erro:
        await coletar()

    # A mensagem do Google diz qual modelo usar. Esconder isso seria cruel.
    msg = str(erro.value)
    assert "gemini-9" in msg
    assert ".env" in msg, "deveria dizer onde corrigir"


@respx.mock
async def test_cota_estourada_poe_o_provedor_de_castigo(monkeypatch):
    from livia import saude

    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    respx.post(GROQ).mock(return_value=httpx.Response(429))
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text=sse_gemini("ok"))
    )

    await coletar()
    assert "groq" in saude.descansando()
    assert "groq" not in saude.quebrados(), "cota passa sozinha; não é quebra"


@respx.mock
async def test_sucesso_limpa_o_castigo(monkeypatch):
    from livia import saude

    monkeypatch.setattr(config, "PROVIDERS", ["groq"])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")

    saude.registrar_falha("groq", "cota", "cota estourada")
    assert not saude.disponivel("groq")

    # Sem mais ninguém na fila, o provedor de castigo é tentado assim mesmo —
    # uma chance contra a parede é melhor que recusar de cara.
    respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("voltei")))
    texto, usados = await coletar()

    assert usados == ["groq"] and "voltei" in texto
    assert saude.disponivel("groq"), "sucesso deveria limpar o castigo"


# ── casos-limite ──────────────────────────────────────────────────────────


async def test_sem_nenhuma_chave_da_erro_util(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")

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
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    assert brain._provedores() == ["gemini"]


def test_openrouter_entra_na_fila_so_com_chave(monkeypatch):
    """Configurar a chave basta — sem precisar editar LIVIA_PROVIDERS também."""
    monkeypatch.setattr(config, "PROVIDERS", ["groq", "gemini"])
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    assert "openrouter" not in brain._provedores()

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "chave-falsa-or")
    assert brain._provedores()[-1] == "openrouter", "entra no fim, sem furar a ordem"


# ── OpenRouter ────────────────────────────────────────────────────────────

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"


@respx.mock
async def test_openrouter_responde(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "chave-falsa-or")
    monkeypatch.setattr(config, "PROVIDERS", ["openrouter"])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    respx.post(OPENROUTER).mock(
        return_value=httpx.Response(200, text=sse_groq("veio do openrouter"))
    )

    texto, usados = await coletar()
    assert usados == ["openrouter"] and "openrouter" in texto


@respx.mock
async def test_openrouter_429_passa_adiante(monkeypatch):
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "chave-falsa-or")
    monkeypatch.setattr(config, "PROVIDERS", ["openrouter", "groq"])
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    respx.post(OPENROUTER).mock(return_value=httpx.Response(429))
    respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("groq salvou")))

    _, usados = await coletar()
    assert usados == ["groq"]


@respx.mock
async def test_openrouter_nunca_recebe_ferramentas(monkeypatch):
    """O catálogo do roteador não declara `tools` para ele. Isto fixa isso."""
    from livia import router

    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "chave-falsa-or")
    monkeypatch.setattr(config, "PROVIDERS", ["openrouter", "groq", "gemini"])
    _, ordem = router.escolher("liste os arquivos", precisa_ferramentas=True)
    assert "openrouter" not in ordem


@respx.mock
async def test_token_do_openrouter_nunca_vaza_na_mensagem_de_erro(monkeypatch):
    segredo = "sk-or-v1-super-secreto-nao-pode-vazar"
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", segredo)
    monkeypatch.setattr(config, "PROVIDERS", ["openrouter"])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    respx.post(OPENROUTER).mock(
        return_value=httpx.Response(401, json={"error": {"message": "no auth"}})
    )

    with pytest.raises(brain.BrainError) as erro:
        await coletar()
    assert segredo not in str(erro.value)
