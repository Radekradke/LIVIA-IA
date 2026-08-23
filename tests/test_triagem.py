"""Reescrever a pergunta antes de buscar, e decidir a web na mesma chamada.

O caso central é "e o próximo?": uma frase que não significa nada sozinha e
que, vetorizada crua, devolve ruído. Os testes cobrem os três riscos reais
disso:

  1. a reescrita não acontecer quando precisa;
  2. a reescrita acontecer quando não precisa (chamada desperdiçada);
  3. a reescrita SUBSTITUIR a pergunta na resposta — o pior dos três, porque
     a Livia responderia outra coisa e o André não teria como saber.
"""

from __future__ import annotations

import pytest

from livia import brain, config, triagem


def historico(*pares: tuple[str, str]) -> list[dict[str, str]]:
    saida: list[dict[str, str]] = []
    for pergunta, resposta in pares:
        saida.append({"role": "user", "content": pergunta})
        saida.append({"role": "assistant", "content": resposta})
    return saida


CONVERSA = historico(
    ("o que o capítulo 4 diz sobre cache?", "Ele explica invalidação por TTL."),
)


# ── a heurística que decide se vale a chamada ─────────────────────────────


@pytest.mark.parametrize("pergunta", [
    "e o próximo?",
    "explica isso melhor",
    "e daí?",
    "por quê?",
    "resume isso",
    "quanto custa",
    "e o segundo capítulo, fala disso também?",
    "qual a diferença entre esse e aquele",
])
def test_pergunta_que_depende_do_que_veio_antes(pergunta):
    assert triagem.precisa_reescrever(pergunta, CONVERSA) is True


@pytest.mark.parametrize("pergunta", [
    "como funciona a invalidação de cache por TTL no Redis?",
    "escreva um resumo do documento sobre arquitetura de microsserviços",
    "qual é a capital da Austrália e quantos habitantes ela tem",
])
def test_pergunta_que_ja_se_sustenta_sozinha(pergunta):
    assert triagem.precisa_reescrever(pergunta, CONVERSA) is False


def test_primeira_mensagem_nunca_e_reescrita():
    """Não há o que desatar: uma primeira mensagem é o que é."""
    assert triagem.precisa_reescrever("e o próximo?", None) is False
    assert triagem.precisa_reescrever("e o próximo?", []) is False


def test_comando_nao_e_reescrito():
    """`/buscar`, `/grafo` e companhia já dizem exatamente o que querem."""
    assert triagem.precisa_reescrever("/grafo alice", CONVERSA) is False


# ── a chamada, ou a ausência dela ─────────────────────────────────────────


@pytest.fixture
def sem_web(monkeypatch):
    monkeypatch.setattr(config, "WEB_ENABLED", False)
    monkeypatch.setattr(config, "WEB_AUTO", False)


@pytest.fixture
def com_web(monkeypatch):
    monkeypatch.setattr(config, "WEB_ENABLED", True)
    monkeypatch.setattr(config, "WEB_AUTO", True)


@pytest.fixture
def modelo(monkeypatch):
    """Captura as chamadas e deixa cada teste escolher a resposta."""
    chamadas: list[tuple] = []
    resposta: dict = {}

    async def structured(sistema, texto, schema):
        chamadas.append((sistema, texto, schema))
        return resposta

    monkeypatch.setattr(brain, "structured", structured)
    return chamadas, resposta


async def test_pergunta_completa_e_web_desligada_nao_gasta_chamada(
    sem_web, modelo
):
    """O ganho de latência depende disto: a maioria das mensagens não paga nada."""
    chamadas, _ = modelo

    t = await triagem.avaliar("como funciona a invalidação por TTL?", CONVERSA)

    assert chamadas == []
    assert t.pergunta_busca == "como funciona a invalidação por TTL?"
    assert t.reescrita is False


async def test_reescreve_a_pergunta_dependente(sem_web, modelo):
    chamadas, resposta = modelo
    resposta["pergunta_completa"] = "o que o capítulo 5 diz sobre cache?"

    t = await triagem.avaliar("e o próximo?", CONVERSA)

    assert len(chamadas) == 1
    assert t.pergunta_busca == "o que o capítulo 5 diz sobre cache?"
    assert t.reescrita is True


async def test_o_historico_recente_vai_junto(sem_web, modelo):
    """Sem o histórico o modelo não tem como saber o que é "o próximo"."""
    chamadas, resposta = modelo
    resposta["pergunta_completa"] = "o que o capítulo 5 diz sobre cache?"

    await triagem.avaliar("e o próximo?", CONVERSA)

    _, texto, _ = chamadas[0]
    assert "capítulo 4" in texto
    assert "ÚLTIMA" in texto and "e o próximo?" in texto


async def test_so_os_ultimos_turnos_entram(sem_web, modelo):
    """Mandar a conversa inteira encarece a chamada para piorar o sinal."""
    chamadas, resposta = modelo
    resposta["pergunta_completa"] = "x y z w"
    longa = historico(*[(f"pergunta {i}", f"resposta {i}") for i in range(20)])

    await triagem.avaliar("e o próximo?", longa)

    _, texto, _ = chamadas[0]
    assert "pergunta 19" in texto
    assert "pergunta 3" not in texto


async def test_uma_chamada_responde_as_duas_perguntas(com_web, modelo):
    """É o que faz a reescrita sair de graça em latência."""
    chamadas, resposta = modelo
    resposta.update({
        "precisa_web": True,
        "consulta": "preço do bitcoin hoje",
        "pergunta_completa": "quanto custa o bitcoin agora?",
    })

    t = await triagem.avaliar("e quanto custa?", CONVERSA)

    assert len(chamadas) == 1
    assert t.consulta_web == "preço do bitcoin hoje"
    assert t.pergunta_busca == "quanto custa o bitcoin agora?"


async def test_link_colado_dispensa_a_busca(com_web, modelo):
    chamadas, resposta = modelo
    resposta.update({"precisa_web": True, "consulta": "algo"})

    t = await triagem.avaliar("resume https://exemplo.com/artigo", CONVERSA,
                              tem_link=True)

    assert t.consulta_web is None


async def test_web_desligada_ignora_o_que_o_modelo_respondeu(sem_web, modelo):
    _, resposta = modelo
    resposta.update({"precisa_web": True, "consulta": "algo",
                     "pergunta_completa": "uma pergunta bem mais longa que a outra"})

    t = await triagem.avaliar("e daí?", CONVERSA)

    assert t.consulta_web is None
    assert t.reescrita is True


# ── as recusas ────────────────────────────────────────────────────────────
#
# Um modelo pequeno erra de formas conhecidas. Em todas elas o original é uma
# escolha melhor que a proposta — ele é, no mínimo, o que o André escreveu.


@pytest.mark.parametrize("proposta,motivo", [
    ("", "vazio"),
    ("e o próximo?", "devolveu a mesma frase"),
    ("curto", "menor que o original — reescrita tem que dizer MAIS"),
    ("Claro! Entendi que você quer saber sobre o próximo capítulo. " * 12,
     "um parágrafo é o modelo conversando, não uma pergunta"),
])
async def test_reescrita_ruim_e_descartada(sem_web, modelo, proposta, motivo):
    _, resposta = modelo
    resposta["pergunta_completa"] = proposta

    t = await triagem.avaliar("e o próximo?", CONVERSA)

    assert t.pergunta_busca == "e o próximo?", motivo
    assert t.reescrita is False


async def test_modelo_fora_do_ar_nao_derruba_nada(sem_web, monkeypatch):
    """Falhar aqui responde com a pergunta como ela veio, e mais nada."""
    async def explode(*_a, **_k):
        raise RuntimeError("provedor fora")

    monkeypatch.setattr(brain, "structured", explode)

    t = await triagem.avaliar("e o próximo?", CONVERSA)

    assert t.pergunta_busca == "e o próximo?"
    assert t.reescrita is False


async def test_resposta_sem_formato_nao_derruba(sem_web, monkeypatch):
    async def torto(*_a, **_k):
        return "isto não é um dicionário"

    monkeypatch.setattr(brain, "structured", torto)

    t = await triagem.avaliar("e o próximo?", CONVERSA)
    assert t.pergunta_busca == "e o próximo?"


# ── o caminho inteiro, pelo servidor ──────────────────────────────────────


def test_a_reescrita_chega_na_busca_e_nao_na_resposta(monkeypatch):
    """O teste que amarra tudo, e o risco que ele cobre.

    A busca precisa receber a pergunta DESATADA. O modelo precisa receber a
    pergunta ORIGINAL. Trocar a segunda faria a Livia responder outra coisa
    com confiança total, e o André não teria como descobrir por quê.
    """
    import json as _json

    import httpx
    import respx
    from starlette.testclient import TestClient

    from livia import auth, db, knowledge_router, server

    GROQ = "https://api.groq.com/openai/v1/chat/completions"

    monkeypatch.setattr(config, "PROVIDERS", ["groq"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OLLAMA_ENABLED", False)
    monkeypatch.setattr(config, "WEB_ENABLED", False)
    monkeypatch.setattr(config, "WEB_AUTO", False)
    monkeypatch.setattr(config, "AUTO_LEARN", False)
    monkeypatch.setattr(config, "TOOLS_ENABLED", False)

    async def structured(*_a, **_k):
        return {"pergunta_completa": "o que o capítulo 5 diz sobre cache?"}

    monkeypatch.setattr(brain, "structured", structured)

    perguntas_buscadas: list[str] = []

    async def buscar(pergunta, *, modo=None):
        perguntas_buscadas.append(pergunta)
        return [], {}

    monkeypatch.setattr(knowledge_router, "buscar", buscar)
    monkeypatch.setattr(server.biblioteca, "vazia", lambda: False)

    conversa = db.create_conversation()
    db.add_message(conversa, "user", "o que o capítulo 4 diz sobre cache?")
    db.add_message(conversa, "assistant", "Ele explica invalidação por TTL.")

    cliente = TestClient(server.app)
    cookie = {auth.COOKIE: auth.criar_token()}

    with respx.mock:
        respx.post(GROQ).mock(return_value=httpx.Response(
            200,
            text="data: " + _json.dumps(
                {"choices": [{"delta": {"content": "ok"}}]}
            ) + "\n\ndata: [DONE]\n\n",
        ))
        resposta = cliente.post(
            "/api/chat",
            json={"message": "e o próximo?", "conversation_id": conversa},
            cookies=cookie,
        )
        enviado = _json.loads(respx.calls[-1].request.content)

    assert resposta.status_code == 200
    # A BUSCA recebeu a versão desatada.
    assert perguntas_buscadas == ["o que o capítulo 5 diz sobre cache?"]
    # O MODELO recebeu a pergunta como o André a escreveu.
    ultima = enviado["messages"][-1]["content"]
    assert "e o próximo?" in ultima
    assert "capítulo 5" not in ultima
