"""Pedido pra Groq cabe no limite do plano grátis, e 413 não derruba a Groq."""

from __future__ import annotations

from livia import brain


def _conversa(n: int, tam: int = 2000) -> list[dict]:
    msgs = [{"role": "system", "content": "PERSONA: você é a Livia. " + "memória " * 3000}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"pergunta {i} " + "x" * tam})
        msgs.append({"role": "assistant", "content": f"resposta {i} " + "y" * tam})
    msgs.append({"role": "user", "content": "PERGUNTA ATUAL"})
    return msgs


def test_corta_historico_antigo_e_fim_do_sistema_mas_guarda_o_essencial():
    saida = brain.caber(_conversa(20), 5500)
    assert brain._tokens(saida) <= 5500
    assert saida[0]["role"] == "system" and saida[0]["content"].startswith("PERSONA")
    assert saida[-1]["content"] == "PERGUNTA ATUAL"
    assert saida[1]["role"] == "user"            # nunca começa por resposta solta


def test_pedido_pequeno_passa_intacto():
    msgs = [{"role": "system", "content": "oi"}, {"role": "user", "content": "tudo bem?"}]
    assert brain.caber(msgs, 5500) == msgs


def test_413_e_passageiro_e_nao_quebra_a_groq():
    erro = brain._classificar("groq", 413, '{"error":{"message":"Request too large"}}', "m")
    assert isinstance(erro, brain.ProvedorIndisponivel) and erro.tipo == "cota"
