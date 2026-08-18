"""Confinamento de caminhos e calculadora.

O confinamento é a defesa mais importante do projeto: o caminho vem do
modelo, e modelo erra. Um `../.env` pedido sem má intenção nenhuma leria a
chave de API do André. Estes testes existem para que essa garantia não se
perca numa refatoração futura.
"""

from __future__ import annotations

import pytest

from livia import ferramentas


# ── confinamento ──────────────────────────────────────────────────────────

ESCAPES = [
    "../.env",
    "../../.env",
    "../../../Windows/System32/drivers/etc/hosts",
    r"..\..\.env",
    "subpasta/../../.env",
    r"C:\Windows\win.ini",
    "/etc/shadow",
    "pasta/../../../fora.txt",
]


@pytest.mark.parametrize("caminho", ESCAPES)
def test_leitura_fora_do_workspace_e_recusada(workspace, caminho):
    resultado, ok = ferramentas.executar("ler_arquivo", {"caminho": caminho})
    assert not ok, f"'{caminho}' deveria ter sido recusado"


@pytest.mark.parametrize("caminho", ESCAPES)
def test_escrita_fora_do_workspace_e_recusada(workspace, caminho):
    resultado, ok = ferramentas.executar(
        "escrever_arquivo", {"caminho": caminho, "conteudo": "invasao"}
    )
    assert not ok, f"'{caminho}' deveria ter sido recusado"
    # E o mais importante: nada foi escrito fora.
    fora = workspace.parent / ".env"
    assert not fora.exists()


def test_link_simbolico_para_fora_e_recusado(workspace, tmp_path):
    alvo = tmp_path / "segredo.txt"
    alvo.write_text("GEMINI_API_KEY=nao-deveria-vazar", encoding="utf-8")
    link = workspace / "atalho.txt"
    try:
        link.symlink_to(alvo)
    except (OSError, NotImplementedError):
        pytest.skip("criar link simbólico exige privilégio elevado neste sistema")

    resultado, ok = ferramentas.executar("ler_arquivo", {"caminho": "atalho.txt"})
    assert not ok
    assert "nao-deveria-vazar" not in resultado


def test_caminho_vazio_e_recusado(workspace):
    _, ok = ferramentas.executar("ler_arquivo", {"caminho": "   "})
    assert not ok


# ── uso legítimo ──────────────────────────────────────────────────────────


def test_escrever_ler_e_listar(workspace):
    _, ok = ferramentas.executar(
        "escrever_arquivo", {"caminho": "notas/plano.md", "conteudo": "# Plano\n"}
    )
    assert ok
    assert (workspace / "notas" / "plano.md").exists()

    conteudo, ok = ferramentas.executar("ler_arquivo", {"caminho": "notas/plano.md"})
    assert ok and "# Plano" in conteudo

    listagem, ok = ferramentas.executar("listar_arquivos", {"pasta": "notas"})
    assert ok and "plano.md" in listagem


def test_sobrescrever_guarda_copia(workspace):
    ferramentas.executar("escrever_arquivo", {"caminho": "a.txt", "conteudo": "versao 1"})
    ferramentas.executar("escrever_arquivo", {"caminho": "a.txt", "conteudo": "versao 2"})

    assert (workspace / "a.txt").read_text(encoding="utf-8") == "versao 2"
    copias = [p for p in workspace.glob("a.antes-*.txt")]
    assert len(copias) == 1, "a versão anterior deveria ter sido guardada"
    assert copias[0].read_text(encoding="utf-8") == "versao 1"


def test_ler_arquivo_inexistente_da_erro_util(workspace):
    resultado, ok = ferramentas.executar("ler_arquivo", {"caminho": "fantasma.txt"})
    assert not ok
    assert "listar_arquivos" in resultado, "o erro deveria orientar o próximo passo"


def test_ler_binario_avisa_em_vez_de_quebrar(workspace):
    (workspace / "imagem.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    resultado, ok = ferramentas.executar("ler_arquivo", {"caminho": "imagem.png"})
    assert not ok
    assert "texto" in resultado.lower()


def test_ferramenta_inexistente_nao_quebra(workspace):
    resultado, ok = ferramentas.executar("apagar_tudo", {})
    assert not ok
    assert "não existe" in resultado


# ── calculadora ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "expressao,esperado",
    [
        ("2 + 3", "5"),
        ("(1250 * 0.87) / 3", "362.5"),
        ("2 ** 10", "1024"),
        ("17 % 5", "2"),
        ("-7 + 2", "-5"),
    ],
)
def test_calculadora_acerta(expressao, esperado):
    resultado, ok = ferramentas.executar("calcular", {"expressao": expressao})
    assert ok and resultado.endswith(esperado)


def test_divisao_por_zero(workspace):
    resultado, ok = ferramentas.executar("calcular", {"expressao": "10 / 0"})
    assert not ok and "zero" in resultado.lower()


def test_expoente_excessivo_e_recusado(workspace):
    _, ok = ferramentas.executar("calcular", {"expressao": "9 ** 99999"})
    assert not ok, "expoente enorme travaria o processo"


@pytest.mark.parametrize(
    "ataque",
    [
        "__import__('os').system('dir')",
        "open('/etc/passwd').read()",
        "[].__class__.__base__",
        "eval('1+1')",
        "lambda: 1",
    ],
)
def test_calculadora_recusa_codigo(ataque):
    _, ok = ferramentas.executar("calcular", {"expressao": ataque})
    assert not ok, f"'{ataque}' não é uma conta e deveria ser recusado"
