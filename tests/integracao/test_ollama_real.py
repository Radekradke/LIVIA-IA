"""Testes contra um Ollama de VERDADE. Não rodam no `pytest` normal.

Por que ficam separados: a suíte padrão precisa passar numa máquina limpa,
offline, sem 5 GB de modelo baixado. Um teste que exige serviço externo dentro
da coleta padrão transforma "os testes falharam" em "talvez o ambiente esteja
diferente" — e a partir daí ninguém confia mais no vermelho.

Para rodar, com o Ollama de pé e os modelos baixados:

    ollama pull qwen3:4b
    ollama pull nomic-embed-text
    LIVIA_OLLAMA=1 python -m pytest tests/integracao -p no:cacheprovider

Sem `LIVIA_OLLAMA=1` tudo aqui é pulado, então rodar a pasta por engano não
quebra nada.
"""

from __future__ import annotations

import os

import pytest

from livia import brain, config, embeddings, saude

pytestmark = pytest.mark.skipif(
    os.getenv("LIVIA_OLLAMA", "0") == "0",
    reason="precisa de um servidor Ollama de verdade (LIVIA_OLLAMA=1)",
)


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "PROVIDERS", ["ollama"])
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "EMBED_PROVIDER", "ollama")


async def test_o_servidor_esta_de_pe_com_os_modelos():
    relatorio = await saude.checar_ollama()
    assert relatorio["ok"] is True, relatorio["mensagem"]


async def test_conversa_de_verdade():
    partes = []
    async for pedaco in brain.stream(
        "Responda em uma palavra só, em português.",
        [{"role": "user", "content": "Qual a capital da França?"}],
        temperature=0.0,
    ):
        partes.append(pedaco)

    resposta = "".join(partes)
    assert resposta.strip(), "o modelo devolveu resposta vazia"
    assert "paris" in resposta.lower()


async def test_saida_estruturada_de_verdade():
    schema = {
        "type": "OBJECT",
        "properties": {"cidade": {"type": "STRING"}},
        "required": ["cidade"],
    }
    resultado = await brain.structured(
        "Você extrai dados. Responda só o JSON pedido.",
        "A capital da França é Paris.",
        schema,
    )
    assert isinstance(resultado, dict)
    assert "paris" in str(resultado.get("cidade", "")).lower()


async def test_embeddings_de_verdade_separam_assuntos():
    """O que a suíte mockada NÃO consegue provar: que o modelo real agrupa bem."""
    matriz, assinatura = await embeddings.gerar([
        "O banco de dados do sistema é PostgreSQL.",
        "Usamos Postgres para guardar os dados.",
        "A impressora Epson não conecta na rede sem fio.",
    ])

    assert assinatura.startswith("ollama:")

    parecidas = float(matriz[0] @ matriz[1])
    diferentes = float(matriz[0] @ matriz[2])
    assert parecidas > diferentes, (
        f"as duas frases sobre banco ({parecidas:.3f}) deveriam ficar mais "
        f"próximas entre si do que da frase sobre impressora ({diferentes:.3f})"
    )
