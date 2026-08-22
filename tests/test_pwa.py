"""Aplicativo instalável: as rotas que o navegador busca sozinho.

O que estes testes protegem é uma falha que não dá erro nenhum. Se o
manifesto ficar atrás da senha, o navegador simplesmente não oferece
instalar — sem mensagem, sem log, sem nada. Alguém só descobre meses depois
que "o botão de instalar sumiu".

O comportamento do service worker em si (cache, offline, streaming) não dá
para testar aqui: exige navegador de verdade. Foi verificado com o Chromium
derrubando o servidor no meio; o que está aqui é o lado Python.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from livia import auth, config, server


@pytest.fixture
def cliente():
    return TestClient(server.app)


@pytest.fixture
def logado(cliente):
    return cliente, {auth.COOKIE: auth.criar_token()}


# ── o que o navegador busca SEM cookie ────────────────────────────────────


@pytest.mark.parametrize(
    "rota,tipo",
    [
        ("/manifest.webmanifest", "application/manifest+json"),
        ("/sw.js", "application/javascript"),
        ("/icones/icone-192.png", "image/png"),
        ("/icones/icone-512.png", "image/png"),
        ("/icones/icone-192-mask.png", "image/png"),
        ("/icones/apple-touch-icon.png", "image/png"),
        ("/icones/livia.svg", "image/svg+xml"),
    ],
)
def test_rotas_do_app_dispensam_sessao(cliente, rota, tipo):
    """O navegador busca manifesto e ícone sem enviar cookie.

    Deixá-los atrás da senha faz o aplicativo não aparecer como instalável —
    e essa falha é silenciosa, que é o que a torna cara.
    """
    resposta = cliente.get(rota)
    assert resposta.status_code == 200, rota
    assert tipo in resposta.headers["content-type"]


def test_o_resto_continua_protegido(cliente):
    """Abrir as rotas do app não pode ter aberto o resto junto."""
    assert cliente.get("/api/conversations").status_code == 401
    assert cliente.get("/api/store/memories").status_code == 401


# ── manifesto ─────────────────────────────────────────────────────────────


def test_manifesto_usa_o_nome_configurado(cliente, monkeypatch):
    """Quem troca LIVIA_NAME instala um aplicativo com o nome dele."""
    monkeypatch.setattr(config, "ASSISTANT_NAME", "Ada")
    dados = cliente.get("/manifest.webmanifest").json()
    assert dados["short_name"] == "Ada"
    assert "Ada" in dados["name"]


def test_manifesto_tem_o_que_torna_instalavel(cliente):
    dados = cliente.get("/manifest.webmanifest").json()
    assert dados["display"] == "standalone"
    assert dados["start_url"] == "/"
    assert dados["scope"] == "/"

    tamanhos = {i["sizes"] for i in dados["icons"] if i["type"] == "image/png"}
    assert "192x192" in tamanhos and "512x512" in tamanhos


def test_manifesto_tem_icone_recortavel(cliente):
    """O Android recorta o ícone num círculo e comeria as pontas da gema."""
    dados = cliente.get("/manifest.webmanifest").json()
    assert any("maskable" in i.get("purpose", "") for i in dados["icons"])


# ── service worker ────────────────────────────────────────────────────────


def test_worker_vale_para_o_site_inteiro(cliente):
    """Sem escopo na raiz, ele controlaria só a pasta de onde foi servido."""
    resposta = cliente.get("/sw.js")
    assert resposta.headers["service-worker-allowed"] == "/"


def test_worker_nunca_e_cacheado_pelo_navegador(cliente):
    """É justamente o arquivo que precisa mudar para consertar cache errado."""
    controle = cliente.get("/sw.js").headers["cache-control"]
    assert "no-cache" in controle or "no-store" in controle


def test_worker_nao_intercepta_o_chat(cliente):
    """A regra que protege o streaming e a honestidade das respostas.

    Uma resposta de chat vinda do cache seria a Livia repetindo conversa
    antiga como se fosse nova — e interceptar SSE sem necessidade é a receita
    conhecida de quebrar streaming.
    """
    fonte = cliente.get("/sw.js").text
    assert "/api/chat" in fonte
    trecho = fonte[fonte.index("const INTOCAVEIS"):]
    assert "/api/chat" in trecho.split("]")[0]
    assert "/api/entrar" in trecho.split("]")[0]


def test_worker_so_mexe_em_get(cliente):
    """POST/DELETE offline criariam memória que existe na tela e não no disco."""
    fonte = cliente.get("/sw.js").text
    assert 'pedido.method !== "GET"' in fonte


# ── segurança ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "nome",
    [
        "..%2F..%2F.env",      # travessia escapada: chega inteira no parâmetro
        "..%5C..%5C.env",      # a mesma coisa com barra invertida
        "livia.svg.bak",
        "qualquer.png",
        "sw.js",
        "index.html",
    ],
)
def test_rota_de_icone_so_serve_o_que_esta_na_lista(cliente, nome):
    """O nome vem da URL, então quem confere é o código — não o sistema de
    arquivos. Montar o caminho com o que veio de fora deixaria
    `/icones/..%2F..%2F.env` valer.

    (`../../.env` sem escapar nem chega aqui: o cliente HTTP normaliza para
    `/.env` antes de mandar, e aquilo vira 404 de rota inexistente. Quem
    precisa de defesa é a forma escapada, que atravessa intacta.)
    """
    resposta = cliente.get(f"/icones/{nome}", follow_redirects=False)
    assert resposta.status_code == 404, f"'{nome}' não podia ser servido"


def test_a_rota_de_icone_nunca_devolve_conteudo_de_arquivo_de_fora(cliente, tmp_path):
    """A prova direta: mesmo existindo um .env ao lado, ele não sai por aqui."""
    alvo = config.WEB_DIR.parent / ".env"
    criei = False
    if not alvo.exists():
        alvo.write_text("GEMINI_API_KEY=nao-deveria-vazar\n", encoding="utf-8")
        criei = True
    try:
        for tentativa in ("..%2F..%2F.env", "..%2F.env", ".env"):
            resposta = cliente.get(f"/icones/{tentativa}", follow_redirects=False)
            assert resposta.status_code == 404
            assert "nao-deveria-vazar" not in resposta.text
    finally:
        if criei:
            alvo.unlink()


def test_o_manifesto_nao_vaza_nada(cliente, monkeypatch):
    """Ele é público: não pode carregar chave, caminho de disco ou senha."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "segredo-absoluto")
    monkeypatch.setattr(config, "PASSWORD", "senha-secreta")
    texto = cliente.get("/manifest.webmanifest").text
    assert "segredo-absoluto" not in texto
    assert "senha-secreta" not in texto
    assert str(config.DATA_DIR) not in texto


# ── a interface conta a verdade ───────────────────────────────────────────


def test_a_pagina_declara_o_manifesto_e_o_icone(cliente, logado):
    c, cookie = logado
    html = c.get("/", cookies=cookie).text
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'name="theme-color"' in html


def test_a_pagina_sabe_ficar_sem_servidor(cliente, logado):
    """O indicador dizia 'online' escrito na mão — mentia com o Python fora."""
    c, cookie = logado
    html = c.get("/", cookies=cookie).text
    assert 'id="conexao"' in html
    assert "sem-servidor" in html
    assert "conferirServidor" in html
    # E a faixa explica o que fazer, em vez de só dizer que falhou.
    assert "python run.py" in html
