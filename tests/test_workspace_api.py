"""Fase 7 — os arquivos saem do disco do servidor e chegam ao navegador.

O teste mais importante daqui é o do `.html`: a Livia escreve HTML, e servir
esse HTML inline seria XSS armazenado na mesma origem do painel.
"""

import pytest
from starlette.testclient import TestClient

from livia import arquivos, config, ferramentas, objetos, server
from livia.ferramentas import FerramentaError

pytestmark = pytest.mark.usefixtures("workspace")


@pytest.fixture
def cliente():
    with TestClient(server.app, base_url="https://testserver") as c:
        assert c.post("/api/entrar", json={"senha": config.PASSWORD}).status_code == 200
        yield c


# --------------------------------------------------------------------------
# Listagem
# --------------------------------------------------------------------------


def test_lista_o_que_existe_na_pasta(cliente, workspace):
    objetos.criar_documento("relatorio.docx", "T", "x", "docx")
    objetos.criar_planilha("sub/custos.xlsx", "a", ["A"], [["1"]], "xlsx")

    dados = cliente.get("/api/workspace").json()
    caminhos = [a["caminho"] for a in dados["arquivos"]]

    assert "relatorio.docx" in caminhos
    assert "sub/custos.xlsx" in caminhos, "subpasta deveria aparecer"
    assert dados["total"] == 2


def test_o_mais_recente_vem_primeiro(cliente, workspace):
    """O caso comum é pegar o que ela acabou de fazer."""
    import os
    import time

    objetos.criar_documento("antigo.md", "T", "x", "md")
    time.sleep(0.02)
    objetos.criar_documento("novo.md", "T", "x", "md")
    os.utime(workspace / "antigo.md", (0, 0))

    dados = cliente.get("/api/workspace").json()
    assert dados["arquivos"][0]["caminho"] == "novo.md"


def test_a_ficha_traz_o_que_a_interface_precisa(cliente, workspace):
    objetos.criar_planilha("c.xlsx", "a", ["A"], [["1"]], "xlsx")
    item = cliente.get("/api/workspace").json()["arquivos"][0]

    assert item["nome"] == "c.xlsx"
    assert item["familia"] == "planilha"
    assert item["bytes"] > 0 and "KB" in item["tamanho"]
    assert item["modificado"].startswith("20")
    assert item["backup"] is False


def test_copia_de_seguranca_vem_marcada(cliente, workspace):
    objetos.criar_documento("n.md", "V1", "a", "md")
    objetos.criar_documento("n.md", "V2", "b", "md")

    itens = {a["caminho"]: a for a in cliente.get("/api/workspace").json()["arquivos"]}
    copia = next(k for k in itens if ".antes-" in k)

    assert itens[copia]["backup"] is True
    assert itens["n.md"]["backup"] is False


def test_arquivo_parcial_nao_aparece(cliente, workspace):
    """`.relatorio.docx.parcial` é escrita em andamento, não entregável."""
    (workspace / ".relatorio.docx.parcial").write_text("meio", encoding="utf-8")
    objetos.criar_documento("pronto.md", "T", "x", "md")

    caminhos = [a["caminho"] for a in cliente.get("/api/workspace").json()["arquivos"]]
    assert caminhos == ["pronto.md"]


def test_pasta_vazia_responde_lista_vazia(cliente):
    dados = cliente.get("/api/workspace").json()
    assert dados["arquivos"] == [] and dados["total"] == 0


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


def test_baixa_o_arquivo_com_o_conteudo_certo(cliente, workspace):
    objetos.criar_planilha("c.xlsx", "Dados", ["Item"], [["Servidor"]], "xlsx")
    esperado = (workspace / "c.xlsx").read_bytes()

    r = cliente.get("/api/workspace/c.xlsx")
    assert r.status_code == 200
    assert r.content == esperado


def test_html_nunca_e_servido_inline(cliente, workspace):
    """A Livia ESCREVE arquivos .html. Inline, o JavaScript deles rodaria na
    mesma origem do painel, com acesso ao cookie de sessão — XSS armazenado
    onde quem escreve o conteúdo é um modelo que leu a internet.
    """
    objetos.criar_documento("p.html", "T", "conteúdo", "html")
    r = cliente.get("/api/workspace/p.html")

    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "text/html" not in r.headers["content-type"]


def test_todo_download_sai_como_anexo(cliente, workspace):
    for nome, formato in (("a.pdf", "pdf"), ("b.md", "md"), ("c.docx", "docx")):
        objetos.criar_documento(nome, "T", "x", formato)
        r = cliente.get(f"/api/workspace/{nome}")
        assert r.headers["content-disposition"].startswith("attachment"), nome
        assert r.headers["content-type"] == "application/octet-stream", nome


def test_nome_com_acento_sobrevive_ao_cabecalho(cliente, workspace):
    objetos.criar_documento("relatório de agosto.docx", "T", "x", "docx")
    r = cliente.get("/api/workspace/relat%C3%B3rio%20de%20agosto.docx")

    assert r.status_code == 200
    assert "utf-8" in r.headers["content-disposition"].lower()


def test_arquivo_em_subpasta_baixa(cliente, workspace):
    objetos.criar_documento("relatorios/2026/agosto.md", "T", "conteúdo", "md")
    r = cliente.get("/api/workspace/relatorios/2026/agosto.md")

    assert r.status_code == 200
    assert "conteúdo" in r.content.decode("utf-8")


def test_arquivo_inexistente_da_404(cliente):
    r = cliente.get("/api/workspace/nao-existe.docx")
    assert r.status_code == 404
    assert "error" in r.json()


def test_pasta_nao_e_baixavel(cliente, workspace):
    (workspace / "uma-pasta").mkdir()
    assert cliente.get("/api/workspace/uma-pasta").status_code == 404


# --------------------------------------------------------------------------
# Confinamento
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tentativa",
    [
        "../../.env",
        "..%2f..%2f.env",
        "....//....//.env",
        "%2e%2e%2f%2e%2e%2f.env",
        "/etc/passwd",
    ],
)
def test_nao_da_para_sair_da_pasta_pela_url(cliente, workspace, tentativa):
    """O `.env` fica um nível acima e tem as chaves de API."""
    r = cliente.get(f"/api/workspace/{tentativa}")

    assert r.status_code in (307, 400, 404), f"{tentativa} devolveu {r.status_code}"
    if r.status_code == 200:
        pytest.fail(f"escapou com {tentativa}")


def test_link_para_fora_nao_entra_na_lista(cliente, workspace, tmp_path):
    """`rglob` atravessa link de pasta sem avisar."""
    fora = tmp_path / "segredos"
    fora.mkdir()
    (fora / "chaves.txt").write_text("segredo", encoding="utf-8")

    try:
        (workspace / "atalho").symlink_to(fora, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("esta máquina não deixa criar link simbólico sem admin")

    caminhos = [a["caminho"] for a in cliente.get("/api/workspace").json()["arquivos"]]
    assert not any("chaves.txt" in c for c in caminhos)


def test_download_sem_sessao_e_recusado(workspace):
    objetos.criar_documento("segredo.md", "T", "conteúdo", "md")

    with TestClient(server.app, base_url="https://testserver") as anonimo:
        r = anonimo.get("/api/workspace/segredo.md")
    assert r.status_code == 401, "arquivo da pasta não pode ser público"


def test_listagem_sem_sessao_e_recusada(workspace):
    with TestClient(server.app, base_url="https://testserver") as anonimo:
        assert anonimo.get("/api/workspace").status_code == 401


# --------------------------------------------------------------------------
# O aviso para a interface
# --------------------------------------------------------------------------


def test_ferramenta_que_grava_e_reconhecida():
    assert ferramentas.arquivo_gravado("criar_documento", {"caminho": "a.docx"}) == "a.docx"
    assert ferramentas.arquivo_gravado("criar_planilha", {"caminho": "b.xlsx"}) == "b.xlsx"
    assert ferramentas.arquivo_gravado("escrever_arquivo", {"caminho": "c.md"}) == "c.md"


def test_ferramenta_que_so_le_nao_anuncia_arquivo():
    assert ferramentas.arquivo_gravado("ler_arquivo", {"caminho": "a.docx"}) is None
    assert ferramentas.arquivo_gravado("listar_arquivos", {}) is None
    assert ferramentas.arquivo_gravado("calcular", {"expressao": "2+2"}) is None


def test_so_anuncia_arquivo_que_existe_no_disco(workspace):
    """Anunciar um arquivo que a gravação não produziu é pior que não anunciar."""
    assert arquivos.sobre("nunca-criado.docx") is None

    objetos.criar_documento("existe.docx", "T", "x", "docx")
    ficha = arquivos.sobre("existe.docx")
    assert ficha and ficha["nome"] == "existe.docx" and ficha["familia"] == "documento"


def test_sobre_nao_atravessa_para_fora(workspace):
    assert arquivos.sobre("../../.env") is None


def test_para_download_recusa_caminho_de_fora(workspace):
    with pytest.raises(FerramentaError):
        arquivos.para_download("../../.env")


def test_familia_classifica_pelo_uso():
    assert arquivos.familia("a.docx") == "documento"
    assert arquivos.familia("a.csv") == "planilha"
    assert arquivos.familia("a.py") == "codigo"
    assert arquivos.familia("a.png") == "imagem"
    assert arquivos.familia("a.qualquercoisa") == "arquivo"


def test_nome_de_arquivo_nao_e_escapado_no_servidor(cliente, workspace):
    """A API devolve o nome cru; quem escapa é a interface, na hora de montar
    o HTML. Escapar aqui corromperia o nome de quem baixa o arquivo."""
    (workspace / "custos & receitas.md").write_text("x", encoding="utf-8")
    item = cliente.get("/api/workspace").json()["arquivos"][0]

    assert item["nome"] == "custos & receitas.md"
    assert "&amp;" not in item["nome"]

    r = cliente.get("/api/workspace/custos%20%26%20receitas.md")
    assert r.status_code == 200


def test_nome_de_arquivo_com_html_nao_derruba_a_api(cliente, workspace):
    """O nome vem do modelo. No Linux — onde isto vai rodar — `<` e `>` são
    caracteres válidos em nome de arquivo, e a listagem cai direto no painel.

    O escape acontece no `escapar()` da interface; aqui o que se garante é que
    o servidor entrega e serve esse nome sem se perder. No Windows o próprio
    sistema recusa o nome, e o teste pula.
    """
    nome = "<img src=x onerror=alert(1)>.md"
    try:
        (workspace / nome).write_text("x", encoding="utf-8")
    except OSError:
        pytest.skip("NTFS não aceita '<' em nome de arquivo; no Linux aceita")

    assert cliente.get("/api/workspace").json()["arquivos"][0]["nome"] == nome

    r = cliente.get("/api/workspace/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E.md")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
