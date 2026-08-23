"""Busca por palavra, fusão por posição, e a prova de que serve para algo.

O teste que justifica o módulo inteiro é o `test_o_vetor_sozinho_erra_o_codigo`
lá embaixo: uma pergunta sobre um código de erro exato, que a busca densa não
encontra e a fusão encontra. Sem ele, isto aqui seria só mais um índice.

Os outros são sobre o que quebra na prática: a sintaxe do FTS5 (aspas e
operadores em pergunta comum), o acento, e o índice ficar falando de documento
que já foi apagado.
"""

from __future__ import annotations

import numpy as np
import pytest

from livia import biblioteca, config, embeddings, knowledge, knowledge_router, lexical
from livia.knowledge import GRAPH, HYBRID, LEXICAL, VECTOR, KnowledgeHit


@pytest.fixture(autouse=True)
def _indice_limpo():
    """O índice mora no SQLite compartilhado; sem isto um teste vaza no outro."""
    for slug in lexical.slugs_indexados():
        lexical.remover(slug)
    yield
    for slug in lexical.slugs_indexados():
        lexical.remover(slug)


# ── a consulta ────────────────────────────────────────────────────────────


def test_pergunta_comum_com_aspas_nao_e_erro_de_sintaxe():
    """`o que é "cache-control"?` não é consulta ruim, é erro de sintaxe.

    Aspas, parênteses, `*` e `-` são OPERADORES do FTS5. Passar texto do
    usuário direto num MATCH derruba a busca com OperationalError — e derruba
    numa pergunta perfeitamente razoável.
    """
    saida = lexical.consulta_fts('o que é "cache-control"?')
    assert saida == '"cache-control"'


@pytest.mark.parametrize("perigosa", [
    'o que é "cache-control"?',
    "NEAR(a b)",
    "malloc AND free",
    "busca por coisa*",
    "campo:valor -negado",
    "(parenteses) e [colchetes]",
    'aspas "no meio" da frase',
    "ERR_CONN_2043 e o que fazer?",
    "100% de uso — por quê?",
])
def test_o_fts5_aceita_o_que_sai_daqui(perigosa, biblioteca_falsa):
    """A propriedade que importa não é o formato, é o FTS5 não recusar.

    Testar a string gerada seria testar a implementação. O que quebra na
    prática é o OperationalError, então é ele que o teste provoca.
    """
    biblioteca_falsa("doc")
    lexical.indexar("doc", [{"texto": "cache-control malloc free ERR_CONN_2043",
                             "pagina": 1}])

    lexical.buscar(perigosa)  # não pode levantar

    expressao = lexical.consulta_fts(perigosa)
    if expressao:
        import sqlite3
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                "SELECT 1 FROM trechos_fts WHERE trechos_fts MATCH ?", (expressao,)
            ).fetchall()
        finally:
            conn.close()


def test_acento_some_dos_dois_lados():
    """"memória" na pergunta tem que achar "memoria" no documento."""
    assert lexical.consulta_fts("memória") == '"memoria"'


def test_palavras_vazias_saem():
    """Uma pergunta só de palavras comuns casaria com o documento inteiro."""
    assert lexical.consulta_fts("o que é isso?") == ""
    assert lexical.consulta_fts("como funciona o malloc") == '"funciona" OR "malloc"'


def test_termo_repetido_entra_uma_vez():
    assert lexical.consulta_fts("malloc malloc malloc") == '"malloc"'


def test_pergunta_vazia_nao_vira_consulta():
    assert lexical.consulta_fts("") == ""
    assert lexical.consulta_fts("   ?  ") == ""


# ── índice ────────────────────────────────────────────────────────────────


@pytest.fixture
def biblioteca_falsa(tmp_path, monkeypatch):
    """Pasta de biblioteca vazia, com meta.json escrito na mão.

    A busca lexical filtra pelo que existe em disco, então os documentos
    precisam existir para os resultados aparecerem.
    """
    import json

    pasta = tmp_path / "biblioteca"
    pasta.mkdir()
    monkeypatch.setattr(biblioteca, "PASTA", pasta)

    def criar(slug: str, titulo: str = "") -> None:
        destino = pasta / slug
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "meta.json").write_text(
            json.dumps({"slug": slug, "titulo": titulo or slug,
                        "assinatura": "", "trechos": 0}),
            encoding="utf-8",
        )

    monkeypatch.setattr(embeddings, "compativel", lambda g, a=None: True)
    return criar


def test_indexa_e_acha(biblioteca_falsa):
    biblioteca_falsa("manual", "Manual do sistema")
    lexical.indexar("manual", [
        {"texto": "O erro ERR_CONN_2043 acontece quando o túnel cai.", "pagina": 7},
        {"texto": "Sobre cores e temas da interface.", "pagina": 8},
    ])

    (achado,) = lexical.buscar("ERR_CONN_2043")
    assert achado["pagina"] == 7
    assert achado["livro"] == "Manual do sistema"


def test_indexar_de_novo_substitui_em_vez_de_somar(biblioteca_falsa):
    biblioteca_falsa("manual")
    lexical.indexar("manual", [{"texto": "versão antiga do texto", "pagina": 1}])
    lexical.indexar("manual", [{"texto": "versão nova do texto", "pagina": 1}])

    achados = lexical.buscar("versão")
    assert len(achados) == 1 and "nova" in achados[0]["texto"]


def test_trecho_vazio_nao_entra(biblioteca_falsa):
    biblioteca_falsa("manual")
    lexical.indexar("manual", [
        {"texto": "conteudo real", "pagina": 1},
        {"texto": "   ", "pagina": 2},
        {"texto": "", "pagina": 3},
    ])
    assert lexical.estatisticas()["trechos"] == 1


def test_documento_apagado_do_disco_nao_aparece_mais(biblioteca_falsa):
    """Os dois índices moram em lugares diferentes e podem sair de sincronia.

    Sem o filtro, a Livia citaria um documento que não existe mais — com
    confiança total, que é o pior jeito de estar errado.
    """
    biblioteca_falsa("fantasma")
    lexical.indexar("fantasma", [{"texto": "texto do fantasma", "pagina": 1}])
    assert lexical.buscar("fantasma") != []

    import shutil
    shutil.rmtree(biblioteca.PASTA / "fantasma")

    assert lexical.buscar("fantasma") == []


def test_sincronizar_recupera_o_atraso(biblioteca_falsa, tmp_path):
    """É o que faz a busca por palavra aparecer para quem já tinha biblioteca.

    Índice derivado se reconstrói. Não se pede permissão para refazer o que
    dá para refazer em segundos.
    """
    import json

    biblioteca_falsa("antigo")
    trechos = biblioteca.PASTA / "antigo" / "trechos.jsonl"
    trechos.write_text(
        json.dumps({"texto": "conteudo herdado sobre mutex", "pagina": 1}) + "\n",
        encoding="utf-8",
    )
    assert lexical.buscar("mutex") == []

    estado = lexical.sincronizar()

    assert estado["indexados"] == 1
    assert lexical.buscar("mutex") != []


def test_sincronizar_limpa_o_que_sumiu(biblioteca_falsa):
    biblioteca_falsa("existe")
    lexical.indexar("existe", [{"texto": "aqui", "pagina": 1}])
    lexical.indexar("sumiu", [{"texto": "ali", "pagina": 1}])

    estado = lexical.sincronizar()

    assert estado["removidos"] == 1
    assert lexical.slugs_indexados() == {"existe"}


def test_bm25_poe_o_mais_relevante_primeiro(biblioteca_falsa):
    """bm25() do SQLite devolve NEGATIVO, e mais negativo é melhor.

    Ordenar decrescente traria os piores primeiro — em silêncio, com a busca
    "funcionando".
    """
    biblioteca_falsa("doc")
    lexical.indexar("doc", [
        {"texto": "mutex mutex mutex protege a secao critica", "pagina": 1},
        {"texto": "um texto longo sobre varias coisas que menciona mutex "
                  "uma vez so no meio de muita outra palavra qualquer", "pagina": 2},
    ])

    achados = lexical.buscar("mutex")
    assert achados[0]["pagina"] == 1
    assert achados[0]["nota"] <= achados[1]["nota"]


# ── fusão por posição ─────────────────────────────────────────────────────


def hit(texto: str, *, doc: str, tipo: str = VECTOR, score: float | None = None,
        relacao: list[str] | None = None) -> KnowledgeHit:
    return KnowledgeHit(text=texto, source=doc, title=doc, document_id=doc,
                        retrieval_type=tipo, score=score, relation_path=relacao)


def test_achado_pelos_dois_caminhos_sobe():
    """É o ponto do RRF: concordância entre buscas independentes vale."""
    so_vetor = hit("só o vetor achou", doc="a")
    nos_dois_v = hit("os dois acharam", doc="b")
    nos_dois_l = hit("os dois acharam", doc="b", tipo=LEXICAL)

    fundidos = knowledge.fundir([[so_vetor, nos_dois_v], [nos_dois_l]])

    assert fundidos[0].text == "os dois acharam"
    assert fundidos[0].retrieval_type == HYBRID


def test_fusao_ignora_a_escala_das_notas():
    """Cosseno vai de 0 a 1; BM25 é negativo. Somar isso desafina em silêncio.

    O lexical aqui tem nota -8 e o vetor 0,9. Por nota, o lexical iria para o
    fim da fila sempre. Por posição, ele é o primeiro da lista dele e compete.
    """
    do_vetor = [hit("assunto geral", doc="a", score=0.9)]
    do_lexical = [hit("o codigo exato", doc="b", tipo=LEXICAL, score=-8.0)]

    fundidos = knowledge.fundir([do_vetor, do_lexical])

    assert {f.text for f in fundidos} == {"assunto geral", "o codigo exato"}
    # Primeiro lugar em cada lista: empatam na fusão.
    assert fundidos[0].fusao == pytest.approx(fundidos[1].fusao)


def test_orcamento_usa_a_nota_de_fusao_e_nao_a_crua():
    """Sem isto o BM25, sendo negativo, seria cortado sempre."""
    lexical_bom = hit("x" * 10, doc="a", tipo=LEXICAL, score=-9.0)
    lexical_bom.fusao = 0.9
    vetor_fraco = hit("y" * 10, doc="b", score=0.6)
    vetor_fraco.fusao = 0.1

    (escolhido,) = knowledge.orcamento([vetor_fraco, lexical_bom], 1, 1000)
    assert escolhido is lexical_bom


def test_fusao_preserva_a_versao_que_diz_mais():
    """Mesmo trecho por dois caminhos: fica o que tem o caminho da relação."""
    simples = hit("Alice trabalha no Orion.", doc="a")
    com_relacao = hit("Alice trabalha no Orion.", doc="a", tipo=GRAPH,
                      relacao=["Alice", "trabalha em", "Orion"])

    (fundido,) = knowledge.fundir([[simples], [com_relacao]])
    assert fundido.relation_path == ["Alice", "trabalha em", "Orion"]


def test_fusao_de_lista_unica_mantem_a_ordem():
    a, b, c = (hit(f"t{i}", doc=f"d{i}") for i in range(3))
    assert [h.text for h in knowledge.fundir([[a, b, c]])] == ["t0", "t1", "t2"]


def test_fusao_sem_nada_devolve_nada():
    assert knowledge.fundir([[], [], []]) == []


# ── a prova ───────────────────────────────────────────────────────────────


CORPUS = [
    ("rede.md",
     "A camada de transporte cuida da entrega dos pacotes entre dois pontos. "
     "Quando a conexao cai no meio, o cliente precisa tentar de novo. "
     "Reconexao automatica evita que o usuario perceba a queda. "),
    ("erros.md",
     "Tabela de codigos. O ERR_CONN_2043 indica que o tunel reverso caiu "
     "durante a transferencia e o pacote parcial foi descartado. "),
]


@pytest.fixture
def biblioteca_real(tmp_path, monkeypatch):
    """Vetores falsos guiados por TEMA — como um modelo denso de verdade.

    O ponto é que os eixos são conceitos, não palavras literais. Cada tema tem
    duas listas: as palavras que aparecem no documento e SINÔNIMOS que só
    aparecem em pergunta. É isso que faz "ligação entre as pontas" cair perto
    do texto sobre conexão sem repetir nenhuma palavra dele — que é
    exatamente o que um modelo de embedding faz e a busca por palavra não faz.

    E é o que torna o teste do código de erro honesto: `ERR_CONN_2043` não
    entra em tema nenhum, porque um código não tem vizinhança semântica.
    """
    pasta = tmp_path / "biblioteca"
    pasta.mkdir()
    monkeypatch.setattr(biblioteca, "PASTA", pasta)
    monkeypatch.setattr(config, "KNOWLEDGE_ENABLED", False)

    TEMAS = (
        # no documento                                   | só em pergunta
        ("conexao", "pacote", "transporte", "entrega", "reconexao", "tunel",
         "ligacao", "pontas", "enlace", "perde"),
        ("codigo", "erro", "tabela", "descartado", "indica"),
    )

    async def gerar(textos, tarefa=embeddings.DOCUMENTO):
        linhas = []
        for t in textos:
            baixo = t.lower()
            linhas.append(
                [float(sum(baixo.count(p) for p in tema)) for tema in TEMAS] + [0.5]
            )
        return embeddings.normalizar(np.array(linhas, dtype=np.float32)), "falso:t:3"

    async def gerar_um(texto, tarefa=embeddings.PERGUNTA):
        m, a = await gerar([texto], tarefa)
        return m[0], a

    monkeypatch.setattr(embeddings, "gerar", gerar)
    monkeypatch.setattr(embeddings, "gerar_um", gerar_um)
    monkeypatch.setattr(embeddings, "compativel", lambda g, a=None: True)
    monkeypatch.setattr(embeddings, "disponivel", lambda: True)
    return pasta


async def _subir(_pasta):
    for nome, texto in CORPUS:
        async for _ in biblioteca.adicionar(nome, texto.encode("utf-8")):
            pass


async def test_o_vetor_sozinho_erra_o_codigo(biblioteca_real, monkeypatch):
    """A razão de existir do módulo, em um teste.

    A pergunta é a que qualquer um faria: o usuário conta o CONTEXTO ("a
    ligação caiu") e cita o CÓDIGO. O contexto tem vizinhança semântica e
    puxa a busca densa para o documento sobre conexão — que é o assunto certo
    e a resposta errada. O código não se parece com nada, e é ele que está na
    tabela.

    Este é o formato de pergunta em que a busca densa falha de um jeito que
    parece sucesso: ela devolve algo relevante, sobre o tema certo, sem a
    informação pedida.
    """
    await _subir(biblioteca_real)
    pergunta = "a ligacao entre as pontas caiu e apareceu ERR_CONN_2043"

    monkeypatch.setattr(config, "LEXICAL_ENABLED", False)
    so_vetor, _ = await knowledge_router.buscar(pergunta)
    assert not any(h.document_id == "erros" for h in so_vetor), (
        "o corpus foi montado para o vetor ERRAR aqui; se ele acerta, o teste "
        "não prova mais nada e precisa de um caso mais difícil"
    )

    monkeypatch.setattr(config, "LEXICAL_ENABLED", True)
    com_lexical, proc = await knowledge_router.buscar(pergunta)

    assert any(h.document_id == "erros" for h in com_lexical)
    assert proc["lexical_hits"] > 0
    assert proc["so_lexical"] > 0


async def test_a_busca_semantica_continua_funcionando(biblioteca_real):
    """O lexical entra ao lado do vetor, não no lugar dele.

    Esta pergunta não repete nenhuma palavra do documento: só o vetor resolve.
    """
    await _subir(biblioteca_real)

    achados, proc = await knowledge_router.buscar(
        "o que acontece quando a ligacao entre as pontas se perde?"
    )
    assert any(h.document_id == "rede" for h in achados)


async def test_desligar_o_lexical_volta_ao_comportamento_anterior(
    biblioteca_real, monkeypatch
):
    await _subir(biblioteca_real)
    monkeypatch.setattr(config, "LEXICAL_ENABLED", False)

    achados, proc = await knowledge_router.buscar("reconexao automatica")

    assert proc["lexical_hits"] == 0
    assert all(h.retrieval_type == VECTOR for h in achados)


async def test_apagar_o_documento_tira_dos_dois_indices(biblioteca_real):
    await _subir(biblioteca_real)
    assert lexical.buscar("ERR_CONN_2043") != []

    biblioteca.remover("erros")

    assert lexical.buscar("ERR_CONN_2043") == []
    assert "erros" not in lexical.slugs_indexados()
