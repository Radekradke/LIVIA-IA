"""O provedor local: protocolo, erros e lugar na fila.

NENHUM teste aqui precisa do Ollama instalado. Tudo é HTTP mockado, e é de
propósito: a suíte tem que passar numa máquina limpa, offline, sem 5 GB de
modelo baixado. Testes de integração com servidor de verdade ficam separados
(ver tests/integracao/), fora da coleta padrão.

O que importa provar aqui:

  1. o formato do Ollama é falado direito (NDJSON, `arguments` como objeto);
  2. cada falha vira a mensagem certa — e "modelo não baixado" diz o comando;
  3. o local encabeça a fila quando ligado, e cai para a nuvem quando falha;
  4. em modo local-only, nenhuma chamada externa acontece. Nenhuma.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from livia import brain, config, router, saude

OLLAMA = "http://127.0.0.1:11434/api/chat"
TAGS = "http://127.0.0.1:11434/api/tags"
GROQ = "https://api.groq.com/openai/v1/chat/completions"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"


@pytest.fixture
def local(monkeypatch):
    """Ollama ligado, sozinho na configuração."""
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen3:8b")
    monkeypatch.setattr(config, "OLLAMA_FAST_MODEL", "qwen3:4b")
    monkeypatch.setattr(config, "PROVIDERS", ["ollama"])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")


def ndjson(*pedacos: str, com_fim: bool = True) -> str:
    """A resposta em stream do Ollama: um JSON por linha, sem `data:`."""
    linhas = [
        json.dumps({"message": {"role": "assistant", "content": p}, "done": False})
        for p in pedacos
    ]
    if com_fim:
        linhas.append(json.dumps({"message": {"content": ""}, "done": True}))
    return "\n".join(linhas) + "\n"


async def coletar(**kwargs) -> tuple[str, list[str]]:
    usados: list[str] = []
    partes: list[str] = []
    async for p in brain.stream(
        "sistema", [{"role": "user", "content": "oi"}], usados=usados, **kwargs
    ):
        partes.append(p)
    return "".join(partes), usados


# ── conversa ──────────────────────────────────────────────────────────────


@respx.mock
async def test_stream_local_monta_a_resposta(local):
    respx.post(OLLAMA).mock(
        return_value=httpx.Response(200, text=ndjson("Oi", ", ", "André"))
    )
    texto, usados = await coletar()
    assert texto == "Oi, André"
    assert usados == ["ollama"]


@respx.mock
async def test_stream_ignora_linha_quebrada_no_meio(local):
    """NDJSON truncado acontece quando o modelo é interrompido. Não pode explodir."""
    corpo = ndjson("parte um") .rstrip("\n") + "\n{lixo nao json\n" + ndjson(" e dois")
    respx.post(OLLAMA).mock(return_value=httpx.Response(200, text=corpo))
    texto, _ = await coletar()
    assert "parte um" in texto and "e dois" in texto


@respx.mock
async def test_system_prompt_vai_como_mensagem_de_sistema(local):
    capturado: dict[str, object] = {}

    def responder(request):
        capturado.update(json.loads(request.content))
        return httpx.Response(200, text=ndjson("ok"))

    respx.post(OLLAMA).mock(side_effect=responder)
    await coletar()

    mensagens = capturado["messages"]
    assert mensagens[0] == {"role": "system", "content": "sistema"}
    assert capturado["model"] == "qwen3:8b"
    assert capturado["stream"] is True


# ── falhas ────────────────────────────────────────────────────────────────


@respx.mock
async def test_modelo_nao_baixado_ensina_o_comando(local):
    respx.post(OLLAMA).mock(
        return_value=httpx.Response(
            404, json={"error": 'model "qwen3:8b" not found, try pulling it first'}
        )
    )
    with pytest.raises(brain.BrainError) as erro:
        await coletar()
    assert "ollama pull qwen3:8b" in str(erro.value)


@respx.mock
async def test_servidor_desligado_diz_o_endereco(local, monkeypatch):
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    respx.post(OLLAMA).mock(side_effect=httpx.ConnectError("conexão recusada"))
    with pytest.raises(brain.BrainError) as erro:
        await coletar()
    texto = str(erro.value)
    assert "11434" in texto and "ollama serve" in texto


@respx.mock
async def test_timeout_do_local_nao_derruba_a_conversa(local, monkeypatch):
    monkeypatch.setattr(brain, "TENTATIVAS", 1)
    monkeypatch.setattr(brain, "ESPERA_BASE", 0)
    monkeypatch.setattr(config, "PROVIDERS", ["ollama", "groq"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")

    respx.post(OLLAMA).mock(side_effect=httpx.ReadTimeout("demorou"))
    respx.post(GROQ).mock(
        return_value=httpx.Response(
            200,
            text="data: "
            + json.dumps({"choices": [{"delta": {"content": "veio da nuvem"}}]})
            + "\n\ndata: [DONE]\n\n",
        )
    )

    texto, usados = await coletar()
    assert usados == ["groq"] and "nuvem" in texto


@respx.mock
async def test_modelo_inexistente_tira_o_local_de_circulacao(local, monkeypatch):
    """Erro de configuração não se resolve tentando de novo — some da fila."""
    monkeypatch.setattr(config, "PROVIDERS", ["ollama", "gemini"])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    respx.post(OLLAMA).mock(
        return_value=httpx.Response(404, json={"error": "model not found"})
    )
    respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(
            200,
            text="data: "
            + json.dumps({"candidates": [{"content": {"parts": [{"text": "oi"}]}}]})
            + "\n\n",
        )
    )

    _, usados = await coletar()
    assert usados == ["gemini"]
    assert "ollama" in saude.quebrados()


# ── fallback local -> nuvem ───────────────────────────────────────────────


@respx.mock
async def test_local_primeiro_nuvem_depois(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "PROVIDERS", ["ollama", "groq"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(brain, "TENTATIVAS", 1)

    respx.post(OLLAMA).mock(return_value=httpx.Response(500, json={"error": "oops"}))
    respx.post(GROQ).mock(
        return_value=httpx.Response(
            200,
            text="data: "
            + json.dumps({"choices": [{"delta": {"content": "reserva"}}]})
            + "\n\ndata: [DONE]\n\n",
        )
    )

    texto, usados = await coletar()
    assert usados == ["groq"] and "reserva" in texto


# ── saída estruturada ─────────────────────────────────────────────────────


@respx.mock
async def test_structured_converte_o_schema_para_json_schema(local):
    capturado: dict[str, object] = {}

    def responder(request):
        capturado.update(json.loads(request.content))
        return httpx.Response(
            200, json={"message": {"content": json.dumps({"titulo": "oi"})}}
        )

    respx.post(OLLAMA).mock(side_effect=responder)

    schema = {
        "type": "OBJECT",
        "properties": {"titulo": {"type": "STRING"}},
        "required": ["titulo"],
    }
    resultado = await brain.structured("sistema", "pergunta", schema)

    assert resultado == {"titulo": "oi"}
    # O dialeto do Gemini ("OBJECT") não existe em JSON Schema; sem converter,
    # o Ollama recusa o pedido inteiro.
    assert capturado["format"]["type"] == "object"
    assert capturado["format"]["properties"]["titulo"]["type"] == "string"
    assert capturado["model"] == "qwen3:4b"


@respx.mock
async def test_structured_com_json_invalido_devolve_none(local):
    respx.post(OLLAMA).mock(
        return_value=httpx.Response(200, json={"message": {"content": "nao é json"}})
    )
    assert await brain.structured("s", "p", {"type": "OBJECT"}) is None


# ── ferramentas ───────────────────────────────────────────────────────────


@respx.mock
async def test_ferramentas_so_quando_o_modelo_declara_suporte(local, monkeypatch):
    """Sem LIVIA_OLLAMA_TOOLS o local não recebe ferramenta — nem é tentado."""
    monkeypatch.setattr(config, "OLLAMA_TOOLS", False)
    rota = respx.post(OLLAMA).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(brain.BrainError):
        await brain.com_ferramentas("s", [{"role": "user", "content": "x"}], [])
    assert not rota.called


@respx.mock
async def test_ferramentas_com_suporte_declarado(local, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_TOOLS", True)
    respx.post(OLLAMA).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "listar_arquivos",
                                "arguments": {"pasta": "notas"},
                            }
                        }
                    ]
                }
            },
        )
    )
    chamadas, eco = await brain.com_ferramentas(
        "s", [{"role": "user", "content": "liste"}], [{"name": "listar_arquivos"}]
    )
    assert chamadas[0]["nome"] == "listar_arquivos"
    assert chamadas[0]["args"] == {"pasta": "notas"}
    assert eco[0]["role"] == "assistant"


@respx.mock
async def test_argumentos_em_string_tambem_sao_aceitos(local, monkeypatch):
    """Alguns modelos mandam string mesmo o protocolo pedindo objeto."""
    monkeypatch.setattr(config, "OLLAMA_TOOLS", True)
    respx.post(OLLAMA).mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "tool_calls": [
                        {"function": {"name": "calcular", "arguments": '{"expressao": "2+2"}'}}
                    ]
                }
            },
        )
    )
    chamadas, _ = await brain.com_ferramentas("s", [], [{"name": "calcular"}])
    assert chamadas[0]["args"] == {"expressao": "2+2"}


def test_historico_de_ferramenta_manda_argumentos_como_objeto(local):
    """`arguments` string faria o modelo local ler o nome do campo como valor."""
    saida = brain._ollama_mensagens(
        "",
        [
            {"role": "user", "content": "liste"},
            {"role": "assistant", "ferramentas": [
                {"id": "o0", "nome": "listar_arquivos", "args": {"pasta": "x"}}
            ]},
            {"role": "ferramenta", "id": "o0", "nome": "listar_arquivos", "resultado": "a.md"},
        ],
    )
    assert saida[1]["tool_calls"][0]["function"]["arguments"] == {"pasta": "x"}
    assert saida[2] == {"role": "tool", "content": "a.md"}


# ── roteamento ────────────────────────────────────────────────────────────


def test_local_encabeca_a_fila_quando_capaz(local):
    perfil = router.classificar("oi, tudo bem?")
    assert perfil.preferred_provider == "ollama"


def test_local_nao_recebe_tarefa_que_nao_sabe_fazer(local, monkeypatch):
    """Ferramenta sem suporte declarado: a preferência volta para a nuvem."""
    monkeypatch.setattr(config, "OLLAMA_TOOLS", False)
    perfil = router.classificar("liste", precisa_ferramentas=True)
    assert perfil.preferred_provider == "groq"
    assert "ollama" not in router.fila(perfil, ["ollama", "groq"])


def test_link_continua_indo_para_quem_abre_pagina(local):
    """O modelo local não busca a página — ele inventa o que acha que tinha nela."""
    perfil = router.classificar("resume https://example.com")
    assert perfil.preferred_provider == "gemini"


def test_ollama_dispensa_chave(local):
    """Nenhum outro provedor configurado, e ainda assim a fila não fica vazia."""
    assert router.disponiveis() == ["ollama"]
    assert router.configurado("ollama") is True


def test_ollama_desligado_some_da_fila(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_ENABLED", False)
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "PROVIDERS", ["ollama", "groq"])
    assert router.disponiveis() == ["groq"]


# ── modo totalmente local ─────────────────────────────────────────────────


def test_local_only_remove_a_nuvem_da_fila(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_ONLY", True)
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "x")
    monkeypatch.setattr(config, "PROVIDERS", ["ollama", "groq", "gemini", "openrouter"])

    assert router.disponiveis() == ["ollama"]


@respx.mock
async def test_local_only_nao_toca_em_servico_externo(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_ONLY", True)
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(config, "PROVIDERS", ["ollama", "groq", "gemini"])

    nuvem_groq = respx.post(GROQ).mock(return_value=httpx.Response(200, text=""))
    nuvem_gemini = respx.post(url__startswith=GEMINI).mock(
        return_value=httpx.Response(200, text="")
    )
    respx.post(OLLAMA).mock(return_value=httpx.Response(200, text=ndjson("só local")))

    texto, usados = await coletar()
    assert usados == ["ollama"] and "só local" in texto
    assert not nuvem_groq.called and not nuvem_gemini.called


@respx.mock
async def test_local_only_sem_ollama_falha_com_mensagem_util(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_ONLY", True)
    monkeypatch.setattr(config, "OLLAMA_ENABLED", False)
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "PROVIDERS", ["ollama", "groq"])

    with pytest.raises(brain.BrainError):
        await coletar()


# ── saúde ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_saude_avisa_qual_modelo_falta(local):
    respx.get(TAGS).mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})
    )
    relatorio = await saude.checar_ollama()
    assert relatorio["ok"] is False
    assert "ollama pull qwen3:8b" in relatorio["mensagem"]
    assert "qwen3:8b" in relatorio["faltando"]


@respx.mock
async def test_saude_reconhece_modelo_sem_tag_explicita(local, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_MODEL", "qwen3")
    monkeypatch.setattr(config, "OLLAMA_FAST_MODEL", "")
    monkeypatch.setattr(config, "OLLAMA_EMBED_MODEL", "")
    respx.get(TAGS).mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3:latest"}]})
    )
    relatorio = await saude.checar_ollama()
    assert relatorio["ok"] is True


@respx.mock
async def test_saude_distingue_servidor_fora_de_modelo_faltando(local):
    respx.get(TAGS).mock(side_effect=httpx.ConnectError("recusada"))
    relatorio = await saude.checar_ollama()
    assert relatorio["servidor"] is False
    assert "ollama serve" in relatorio["mensagem"]


async def test_saude_com_provedor_desligado(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_ENABLED", False)
    relatorio = await saude.checar_ollama()
    assert relatorio["ligado"] is False
    assert "LIVIA_OLLAMA=1" in relatorio["mensagem"]


def test_diagnostico_nunca_vaza_chave(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "segredo-absoluto")
    texto = json.dumps(saude.diagnostico())
    assert "segredo-absoluto" not in texto
    assert saude.diagnostico()["ollama"]["local"] is True
