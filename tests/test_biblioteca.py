"""Biblioteca: extração e divisão em trechos, sem tocar na rede."""

from __future__ import annotations

import pytest

from livia import biblioteca


def test_extrai_texto_simples():
    paginas = biblioteca.extrair("apostila.txt", "Conteúdo de teste.".encode("utf-8"))
    assert paginas == [(0, "Conteúdo de teste.")]


def test_formato_nao_suportado_avisa_com_clareza():
    with pytest.raises(biblioteca.BibliotecaError) as erro:
        biblioteca.extrair("livro.epub", b"qualquer")
    assert "epub" in str(erro.value).lower() and "PDF" in str(erro.value)


def test_pdf_invalido_nao_quebra():
    with pytest.raises(biblioteca.BibliotecaError):
        biblioteca.extrair("quebrado.pdf", b"isto nao e um pdf")


def test_divisao_respeita_o_tamanho_e_tem_sobreposicao():
    paragrafo = "Frase de teste com algum conteúdo. " * 12   # ~420 caracteres
    texto = "\n\n".join(paragrafo for _ in range(8))
    trechos = biblioteca.dividir([(1, texto)])

    assert len(trechos) > 1, "texto longo deveria virar vários trechos"
    for t in trechos:
        assert len(t["texto"]) <= biblioteca.TAMANHO_TRECHO + biblioteca.SOBREPOSICAO
        assert t["pagina"] == 1


def test_trecho_curto_demais_e_descartado():
    trechos = biblioteca.dividir([(1, "oi")])
    assert trechos == []


def test_formatar_sem_achados_devolve_vazio():
    assert biblioteca.formatar([]) == ""


def test_formatar_cita_livro_e_pagina():
    bloco = biblioteca.formatar(
        [{"livro": "Curso de C", "pagina": 42, "texto": "ponteiros", "nota": 0.8}]
    )
    assert "Curso de C" in bloco and "42" in bloco
