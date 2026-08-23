"""O harness de avaliação, e o piso de qualidade da recuperação.

Duas coisas diferentes moram aqui, e vale separar:

  1. Testes DO HARNESS — a conta do MRR, o critério de acerto, a recusa de
     caso sem critério. Coisa de código comum.

  2. O teste de REGRESSÃO DA BUSCA — um corpus pequeno e determinístico com
     perguntas cuja resposta certa é conhecida, medindo o pipeline inteiro.

O segundo é o que não existia. Ele tem um limite honesto que precisa estar
escrito: com vetores falsos, ele NÃO mede o modelo de embedding. Mede o que
vem depois — limiar, ordenação, fusão, deduplicação, orçamento. É onde moram
as regressões que um refactor causa sem querer, e é a parte que dá para medir
sem Ollama nenhum.

Para medir o modelo de verdade, com documentos de verdade:

    python -m livia.avaliacao
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from livia import avaliacao, biblioteca, config, embeddings
from livia.avaliacao import Caso, Linha, Resultado
from livia.knowledge import VECTOR, KnowledgeHit


def hit(texto: str, *, doc: str = "", pagina: int | None = None) -> KnowledgeHit:
    return KnowledgeHit(
        text=texto, source=doc or "x", title=doc, page=pagina,
        retrieval_type=VECTOR, document_id=doc or None,
    )


# ── critério de acerto ────────────────────────────────────────────────────


def test_documento_e_pagina_precisam_bater():
    caso = Caso(pergunta="?", documento="manual", pagina=7)
    assert caso.combina(hit("qualquer", doc="manual", pagina=7)) is True
    assert caso.combina(hit("qualquer", doc="manual", pagina=8)) is False
    assert caso.combina(hit("qualquer", doc="outro", pagina=7)) is False


def test_contem_ignora_acento_e_caixa():
    """Senão o conjunto dourado vira exercício de digitação.

    O que ele mede é a busca, não se o André lembrou do acento em "memória".
    """
    caso = Caso(pergunta="?", contem=("memória", "MALLOC"))
    assert caso.combina(hit("A funcao malloc reserva memoria no heap.")) is True
    assert caso.combina(hit("A memória é gerenciada pelo runtime.")) is False


def test_todos_os_criterios_se_somam():
    caso = Caso(pergunta="?", documento="manual", contem=("malloc",))
    assert caso.combina(hit("use malloc", doc="manual")) is True
    assert caso.combina(hit("use malloc", doc="outro")) is False


def test_caso_sem_criterio_e_recusado():
    """Um caso que aceita qualquer resposta sobe a métrica sem melhorar nada.

    Métrica que só sobe é pior que métrica nenhuma: ela dá confiança sem dar
    informação.
    """
    with pytest.raises(ValueError, match="sem critério"):
        avaliacao.montar([{"pergunta": "qualquer coisa"}])


def test_caso_sem_pergunta_e_recusado():
    with pytest.raises(ValueError, match="sem pergunta"):
        avaliacao.montar([{"documento": "manual"}])


def test_contem_aceita_texto_solto_alem_de_lista():
    (caso,) = avaliacao.montar([{"pergunta": "?", "contem": "malloc"}])
    assert caso.contem == ("malloc",)


# ── as contas ─────────────────────────────────────────────────────────────


def _linha(posicao: int | None, devolvidos: int = 5) -> Linha:
    return Linha(caso=Caso(pergunta="?", documento="d"), posicao=posicao,
                 devolvidos=devolvidos)


def test_acerto_e_a_fracao_que_apareceu_entre_os_k():
    r = Resultado(k=5, linhas=[_linha(1), _linha(3), _linha(None), _linha(None)])
    assert r.acerto == 0.5
    assert r.acertos == 2


def test_mrr_separa_achar_em_primeiro_de_achar_em_ultimo():
    """É por isto que o MRR existe: `acerto@k` trata os dois como iguais."""
    primeiro = Resultado(k=5, linhas=[_linha(1), _linha(1)])
    ultimo = Resultado(k=5, linhas=[_linha(5), _linha(5)])

    assert primeiro.acerto == ultimo.acerto == 1.0
    assert primeiro.mrr == 1.0
    assert ultimo.mrr == pytest.approx(0.2)


def test_vazio_e_contado_separado_de_errado():
    """Não devolver nada e devolver o trecho errado são falhas diferentes.

    A primeira costuma ser limiar alto demais; a segunda, ordenação ruim.
    Somar as duas esconderia qual é.
    """
    r = Resultado(k=5, linhas=[_linha(None, devolvidos=0), _linha(None, devolvidos=4)])
    assert r.acerto == 0.0
    assert r.vazios == 1


def test_resultado_vazio_nao_divide_por_zero():
    r = Resultado(k=5, linhas=[])
    assert r.acerto == 0.0 and r.mrr == 0.0


# ── comparação e piso ─────────────────────────────────────────────────────


def test_comparar_so_fala_do_que_se_moveu():
    """Repetir "igual" em toda linha treina qualquer um a não ler a saída."""
    igual = avaliacao.comparar({"acerto": 0.8, "mrr": 0.6, "vazios": 1},
                               {"acerto": 0.8, "mrr": 0.6, "vazios": 1})
    assert igual == []

    piorou = avaliacao.comparar({"acerto": 0.6, "mrr": 0.6, "vazios": 1},
                                {"acerto": 0.8, "mrr": 0.6, "vazios": 1})
    assert len(piorou) == 1 and "CAIU" in piorou[0]


def test_piso_quebrado_aponta_a_metrica():
    problemas = avaliacao.piso_quebrado(
        {"acerto": 0.5, "mrr": 0.7}, {"acerto": 0.7, "mrr": 0.5}
    )
    assert len(problemas) == 1 and problemas[0].startswith("acerto")


def test_piso_ausente_nao_reprova_nada():
    assert avaliacao.piso_quebrado({"acerto": 0.1}, {}) == []


def test_carregar_le_casos_e_piso(tmp_path):
    arquivo = tmp_path / "c.json"
    arquivo.write_text(json.dumps({
        "piso": {"acerto": 0.7},
        "casos": [{"pergunta": "p", "documento": "d"}],
    }), encoding="utf-8")

    casos, piso = avaliacao.carregar(arquivo)
    assert len(casos) == 1 and piso == {"acerto": 0.7}


def test_o_modelo_de_exemplo_e_valido():
    """O modelo que a CLI imprime precisa carregar sem editar nada.

    Um exemplo que não roda ensina errado na primeira tentativa.
    """
    dados = json.loads(avaliacao.MODELO)
    assert len(avaliacao.montar(dados["casos"])) == 2
    assert dados["piso"]["acerto"] > 0


# ── regressão da busca ────────────────────────────────────────────────────
#
# A partir daqui é o teste que não existia: um corpus conhecido, perguntas com
# resposta conhecida, e um piso. Se um refactor no limiar, na fusão ou no
# orçamento piorar a recuperação, isto reprova — que é a única forma de
# descobrir sem ter alguém percebendo meses depois que "ela piorou".

CORPUS = [
    ("alocacao.md", (
        "A funcao malloc reserva memoria no heap e devolve um ponteiro. "
        "Quem chama malloc precisa chamar free depois, senao vaza memoria. "
    ) * 4),
    ("ponteiros.md", (
        "Um ponteiro guarda o endereco de outra variavel. Desreferenciar um "
        "ponteiro nulo derruba o programa na hora. "
    ) * 4),
    ("concorrencia.md", (
        "Uma thread compartilha memoria com as outras do mesmo processo. "
        "Sem mutex, duas threads escrevendo no mesmo lugar corrompem o dado. "
    ) * 4),
]

# As perguntas usam palavras DIFERENTES das do texto sempre que possível: se
# a pergunta repetisse o texto, o teste passaria com qualquer busca, inclusive
# uma quebrada.
PERGUNTAS = [
    {"pergunta": "como reservo espaco no heap?", "documento": "alocacao",
     "contem": ["malloc"], "nota": "sinônimo: reservar espaço vs malloc"},
    {"pergunta": "o que acontece ao usar um ponteiro nulo?",
     "documento": "ponteiros", "contem": ["derruba"]},
    {"pergunta": "duas threads escrevendo ao mesmo tempo, o que protege?",
     "documento": "concorrencia", "contem": ["mutex"]},
    {"pergunta": "quem libera o que foi alocado?", "documento": "alocacao",
     "contem": ["free"]},
    {"pergunta": "por que o programa vaza memoria?", "documento": "alocacao",
     "contem": ["free"], "nota": "o sintoma, não o mecanismo"},
    {"pergunta": "onde fica guardado o endereco de uma variavel?",
     "documento": "ponteiros", "contem": ["ponteiro"]},
    {"pergunta": "o que uma thread divide com as outras do processo?",
     "documento": "concorrencia", "contem": ["compartilha"]},
]

# O piso do acerto é 1.0 de propósito. O corpus é determinístico e o resultado
# de hoje é 100%: qualquer pergunta que pare de ser encontrada é regressão, e
# aceitar "uma pode falhar" num conjunto de sete deixaria passar exatamente o
# tipo de perda que este teste existe para pegar.
#
# O do MRR é mais frouxo porque reordenar não é perder: uma mudança na fusão
# pode legitimamente trocar o primeiro pelo segundo sem piorar nada.
PISO_ACERTO = 1.0
PISO_MRR = 0.80


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """Biblioteca pequena com vetores falsos mas SEMÂNTICOS.

    Os eixos são temas, não palavras da pergunta — é o que faz "reservar
    espaco no heap" cair perto de "malloc" sem que as duas frases se pareçam.
    """
    pasta = tmp_path / "biblioteca"
    pasta.mkdir()
    monkeypatch.setattr(biblioteca, "PASTA", pasta)

    TEMAS = {
        0: ("malloc", "free", "heap", "reserv", "aloc", "libera", "espaco", "vaza"),
        1: ("ponteiro", "endereco", "nulo", "desreferenc", "derruba"),
        2: ("thread", "mutex", "concorr", "compartilha", "protege", "corromp"),
    }

    async def gerar(textos, tarefa=embeddings.DOCUMENTO):
        linhas = []
        for t in textos:
            baixo = t.lower()
            eixos = [
                float(sum(baixo.count(p) for p in palavras))
                for _, palavras in sorted(TEMAS.items())
            ]
            # Eixo neutro: sem ele, um texto sem nenhuma palavra-tema vira
            # vetor zero e a normalização o coloca perto de tudo.
            eixos.append(0.5)
            linhas.append(eixos)
        return embeddings.normalizar(np.array(linhas, dtype=np.float32)), "falso:t:4"

    async def gerar_um(texto, tarefa=embeddings.PERGUNTA):
        m, a = await gerar([texto], tarefa)
        return m[0], a

    monkeypatch.setattr(embeddings, "gerar", gerar)
    monkeypatch.setattr(embeddings, "gerar_um", gerar_um)
    monkeypatch.setattr(embeddings, "compativel", lambda g, a=None: True)
    monkeypatch.setattr(embeddings, "disponivel", lambda: True)
    monkeypatch.setattr(config, "KNOWLEDGE_ENABLED", False)
    return pasta


async def _indexar(corpus_pasta):
    for nome, texto in CORPUS:
        async for _ in biblioteca.adicionar(nome, texto.encode("utf-8")):
            pass


async def test_a_busca_acha_o_trecho_certo(corpus):
    await _indexar(corpus)

    resultado = await avaliacao.avaliar(avaliacao.montar(PERGUNTAS), k=5)

    # A mensagem de falha vale mais que a asserção: sem ela, "assert 0.5 >= 0.75"
    # não diz QUAL pergunta parou de funcionar.
    assert resultado.acerto >= PISO_ACERTO, (
        f"acerto@5 caiu para {resultado.acerto:.0%}\n" + avaliacao.formatar(resultado)
    )
    assert resultado.mrr >= PISO_MRR, (
        f"MRR caiu para {resultado.mrr:.3f}\n" + avaliacao.formatar(resultado)
    )


async def test_pergunta_fora_do_assunto_nao_inventa_acerto(corpus):
    """O limiar existe para isto: nada relevante deve devolver nada.

    Se esta passar a "achar" alguma coisa, o limiar afrouxou e a Livia vai
    responder pergunta de culinária citando o manual de C.
    """
    await _indexar(corpus)

    (linha,) = (await avaliacao.avaliar(
        avaliacao.montar([
            {"pergunta": "qual a receita de bolo de cenoura?",
             "contem": ["cenoura"]}
        ]), k=5,
    )).linhas
    assert linha.acertou is False


async def test_biblioteca_vazia_nao_derruba_a_medicao(corpus):
    resultado = await avaliacao.avaliar(avaliacao.montar(PERGUNTAS), k=5)
    assert resultado.acerto == 0.0
    assert resultado.vazios == len(PERGUNTAS)
