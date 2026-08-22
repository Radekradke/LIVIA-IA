"""Parser avançado: o pypdf continua o caminho rápido.

A regra que estes testes protegem: um PDF de texto normal NUNCA pode passar
pelo parser pesado. Ele lê em milissegundos; o outro custa minutos. Inverter
isso resolveria o problema de poucos arquivos cobrando de todos.
"""

from __future__ import annotations

import httpx
import numpy as np
import pytest
import respx

from livia import biblioteca, config, embeddings, knowledge_client
from services.knowledge import multimodal

SERVICO = "http://127.0.0.1:8110"


@pytest.fixture(autouse=True)
def _limpo():
    knowledge_client.limpar()
    yield
    knowledge_client.limpar()


@pytest.fixture
def bib(tmp_path, monkeypatch):
    pasta = tmp_path / "biblioteca"
    pasta.mkdir()
    monkeypatch.setattr(biblioteca, "PASTA", pasta)

    async def gerar(textos, tarefa=embeddings.DOCUMENTO):
        m = embeddings.normalizar(
            np.array([[1.0, float(len(t) % 5), 0.5] for t in textos], dtype=np.float32)
        )
        return m, "falso:t:3"

    monkeypatch.setattr(embeddings, "gerar", gerar)
    monkeypatch.setattr(embeddings, "compativel", lambda g, a=None: True)
    return pasta


@pytest.fixture
def parser_ligado(monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_ENABLED", True)
    monkeypatch.setattr(config, "KNOWLEDGE_URL", SERVICO)
    monkeypatch.setattr(config, "LOCAL_ONLY", False)
    monkeypatch.setattr(config, "PARSER_AVANCADO", True)


# ── a regra: texto normal não paga o preço ────────────────────────────────


@respx.mock
async def test_documento_de_texto_nao_chama_o_parser(bib, parser_ligado):
    """O caso comum tem que continuar custando milissegundos."""
    rota = respx.post(f"{SERVICO}/parse").mock(
        return_value=httpx.Response(200, json={"trechos": []})
    )
    texto = ("Este documento tem bastante texto de verdade em cada página. " * 40)
    async for _ in biblioteca.adicionar("normal.txt", texto.encode("utf-8")):
        pass

    assert not rota.called, "arquivo de texto não pode passar pelo parser pesado"
    assert len(biblioteca.listar()) == 1


def test_deteccao_de_texto_insuficiente():
    """A média é POR PÁGINA: um PDF escaneado com capa em texto passaria
    no total e falharia justamente no que importa."""
    assert biblioteca._texto_insuficiente([]) is True
    assert biblioteca._texto_insuficiente([(1, "")]) is True
    assert biblioteca._texto_insuficiente([(1, "x" * 20)]) is True
    assert biblioteca._texto_insuficiente([(1, "x" * 500)]) is False
    # Capa com texto + 9 páginas escaneadas: a média denuncia.
    assert biblioteca._texto_insuficiente(
        [(1, "x" * 400)] + [(p, "") for p in range(2, 11)]
    ) is True


# ── PDF sem texto ─────────────────────────────────────────────────────────


@respx.mock
async def test_sem_parser_a_mensagem_continua_honesta(bib, monkeypatch):
    """O comportamento de hoje, quando não há parser avançado."""
    monkeypatch.setattr(config, "PARSER_AVANCADO", False)
    with pytest.raises(biblioteca.BibliotecaError) as erro:
        async for _ in biblioteca.adicionar("vazio.txt", b"   "):
            pass
    texto = str(erro.value)
    assert "escaneado" in texto
    assert "LIVIA_PARSER_AVANCADO=1" in texto


@respx.mock
async def test_com_parser_o_documento_e_recuperado(bib, parser_ligado):
    respx.post(f"{SERVICO}/parse").mock(
        return_value=httpx.Response(200, json={"trechos": [
            {"texto": "[tabela] Receita 2026 | 1.200", "pagina": 14,
             "origem": "tese.pdf", "tipo": "table"},
            {"texto": "[equação] E = mc²", "pagina": 15,
             "origem": "tese.pdf", "tipo": "equation"},
            {"texto": "Parágrafo comum com bastante conteúdo. " * 6,
             "pagina": 16, "origem": "tese.pdf", "tipo": "text"},
        ]})
    )
    passos = [p async for p in biblioteca.adicionar("tese.txt", b"  ")]
    assert passos[-1]["etapa"] == "pronto"
    assert biblioteca.listar()[0]["trechos"] == 3


@respx.mock
async def test_parser_que_falha_cai_para_a_mensagem_segura(bib, parser_ligado):
    """Falha do parser avançado nunca pode virar stack trace na tela."""
    respx.post(f"{SERVICO}/parse").mock(side_effect=httpx.ConnectError("fora"))
    with pytest.raises(biblioteca.BibliotecaError) as erro:
        async for _ in biblioteca.adicionar("vazio.txt", b"  "):
            pass
    assert "escaneado" in str(erro.value)


@respx.mock
async def test_parser_nao_instalado_no_servico(bib, parser_ligado):
    """501 é a resposta honesta de "existo, mas isso não está instalado"."""
    respx.post(f"{SERVICO}/parse").mock(
        return_value=httpx.Response(501, json={"error": "não instalado"})
    )
    assert await knowledge_client.analisar_documento("x.pdf", b"dados") is None


# ── tipos preservados ─────────────────────────────────────────────────────


def test_tabela_e_equacao_nao_viram_texto_anonimo():
    """Uma equação que vira "x2 + y2 = z2" em silêncio é pior que uma
    equação faltando: a primeira parece certa."""
    trechos = multimodal.para_trechos([
        {"type": "table", "page": 14, "content": "A | B", "caption": "Receita",
         "source": "t.pdf"},
        {"type": "equation", "page": 15, "content": "E = mc^2", "caption": "",
         "source": "t.pdf"},
        {"type": "text", "page": 16, "content": "parágrafo", "caption": "",
         "source": "t.pdf"},
    ])
    assert trechos[0]["texto"].startswith("[tabela]")
    assert "Receita" in trechos[0]["texto"]
    assert trechos[1]["texto"].startswith("[equação]")
    assert not trechos[2]["texto"].startswith("[")
    assert [t["tipo"] for t in trechos] == ["table", "equation", "text"]


def test_figura_sem_legenda_nao_desaparece():
    """Sumir com ela faria a resposta parecer completa quando não está."""
    blocos = multimodal._normalizar(
        [{"type": "image", "page_idx": 3}], "t.pdf", descrever_imagens=False
    )
    assert blocos and blocos[0]["type"] == "image"
    assert "não foi descrita" in blocos[0]["content"]
    assert blocos[0]["page"] == 4          # page_idx é zero-based


def test_normalizar_aceita_formatos_variados():
    blocos = multimodal._normalizar(
        ["string solta", {"type": "table_body", "table_body": "A|B"},
         {"category": "interline_equation", "latex": "x^2"}, 12345],
        "t.pdf", descrever_imagens=False,
    )
    tipos = [b["type"] for b in blocos]
    assert "text" in tipos and "table" in tipos and "equation" in tipos


def test_normalizar_nao_quebra_com_lixo():
    assert multimodal._normalizar(None, "t.pdf", False) == []
    assert multimodal._normalizar({"content_list": []}, "t.pdf", False) == []


# ── LOCAL_ONLY ────────────────────────────────────────────────────────────


@respx.mock
async def test_local_only_nao_manda_documento_para_parser_remoto(monkeypatch):
    monkeypatch.setattr(config, "KNOWLEDGE_ENABLED", True)
    monkeypatch.setattr(config, "LOCAL_ONLY", True)
    monkeypatch.setattr(config, "KNOWLEDGE_URL", "https://parser.nuvem.com")

    fora = respx.post("https://parser.nuvem.com/parse").mock(
        return_value=httpx.Response(200, json={"trechos": [{"texto": "x"}]})
    )
    assert await knowledge_client.analisar_documento("segredo.pdf", b"dados") is None
    assert not fora.called


def test_parser_desligado_por_padrao():
    assert config.PARSER_AVANCADO is False
    assert config.PARSER_DESCREVE_IMAGENS is False


def test_diagnostico_ensina_a_instalar():
    d = multimodal.diagnostico()
    assert d["instalado"] is False
    assert "raganything" in d["mensagem"]
