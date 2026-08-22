"""Qual busca atende cada pergunta — e o que fazer quando uma delas falha.

Duas formas de procurar, que respondem coisas diferentes:

    VECTOR   acha TRECHO PARECIDO. "O que o capítulo 4 diz sobre redes
             neurais?" — a resposta está escrita num lugar só, e o problema é
             achar aquele lugar.

    GRAPH    acha RELAÇÃO. "Que banco aparece ligado ao projeto da Alice?" —
             a resposta não está escrita em lugar nenhum: está em `doc_a`
             (Alice → Orion) mais `doc_b` (Orion → PostgreSQL). Nenhum dos
             dois se parece com a pergunta, então o vetor pode não trazer os
             dois, e ainda que traga não diz que estão ligados.

    HYBRID   os dois, deduplicados. "Compare o que os documentos dizem
             sobre X."

A DECISÃO É LOCAL E DETERMINÍSTICA
----------------------------------
Mesma filosofia do `router.py`: nada aqui chama um LLM para descobrir qual
busca usar. Isso dobraria a latência de toda mensagem para responder algo que
uma regex resolve bem. A função `classificar` está isolada justamente para
que um classificador melhor possa substituí-la depois sem tocar em mais nada.

QUEM MANDA NO FIM É A DISPONIBILIDADE
-------------------------------------
A heurística escolhe; a realidade decide. Grafo desligado, quebrado ou de
castigo vira VECTOR, sempre, em silêncio para o usuário e com log para quem
for investigar. Nenhuma pergunta fica sem resposta porque o serviço opcional
não estava lá.
"""

from __future__ import annotations

import logging
import re

from . import biblioteca, config, knowledge, knowledge_client
from .knowledge import GRAPH, HYBRID, VECTOR, KnowledgeHit

log = logging.getLogger("livia.knowledge")

# Perguntas que pedem RELAÇÃO entre coisas. Deliberadamente específico: "e"
# ou "com" sozinhos apareceriam em toda frase, e mandar tudo para o grafo
# gastaria tempo sem ganhar nada.
_RELACIONAL = re.compile(
    r"("
    r"\brelaç\w+|\brelacion\w+|\bconex\w+|\bconect\w+|"
    r"\bligaç\w+|\bligad\w+|\bvincul\w+|"
    r"\bcompar\w+|\bconfront\w+|"
    r"\bentre\s+\w+\s+e\s+\w+|"
    r"\bdepende\s+de\b|\binfluenc\w+|\bimpacto\s+(?:de|em|no|na)\b|"
    r"\bcomo\s+\w+\s+(?:se\s+)?(?:relaciona|conecta|liga)\b|"
    r"\bquem\s+(?:está|esta|aparece)\s+(?:conectad|ligad|relacionad)\w*|"
    r"\bo\s+que\s+liga\b|\bque\s+liga(?:ção)?\b|"
    r"\bjuntando\s+os\s+documentos\b|\bcruzando\b|"
    r"\bem\s+comum\b|\bdiferença\s+entre\b|"
    # Síntese: pedir CONCLUSÃO quase sempre significa cruzar documentos, mesmo
    # sem nenhuma palavra relacional na frase. Era o furo mais visível da
    # heurística — "o que dá para concluir sobre o banco usado no projeto da
    # Alice" não casava com nada e ia para o vetor sozinho.
    r"\bo\s+que\s+(?:dá|da|podemos|posso|se)\s+(?:para\s+)?conclui\w+|"
    r"\bque\s+conclus\w+|\bconclui\w+\s+(?:sobre|que)\b|"
    r"\bresum\w+\s+o\s+que\s+(?:os|as)\s+\w+\s+dizem\b|"
    # Referência indireta: "o banco USADO NO projeto", "a linguagem ESCOLHIDA
    # PELA equipe". A coisa não é nomeada — é alcançada por um caminho, que é
    # exatamente o que um grafo sabe percorrer e o vetor não.
    r"\b(?:usad|utilizad|escolhid|adotad|feit|control|mantid)[oa]s?\s+"
    r"(?:n[oa]s?|pel[oa]s?|em|por)\s+\w+"
    r")",
    re.IGNORECASE,
)

# Perguntas que são SÓ sobre o mapa de relações — não pedem o texto original.
# Aqui o grafo sozinho basta e o vetor só traria ruído.
_SO_GRAFO = re.compile(
    r"("
    r"\bquais\s+conceitos\s+(?:aparecem\s+)?(?:ligad|relacionad|conectad)\w*|"
    r"\bquem\s+(?:está|esta)\s+(?:conectad|ligad)\w*\s+a\b|"
    r"\bmapa\s+de\s+(?:conceitos|relaç\w+)|"
    r"\bque\s+entidades\b|\bquais\s+entidades\b"
    r")",
    re.IGNORECASE,
)

# Perguntas que pedem um trecho literal. Mesmo com palavra relacional na
# frase, o vetor é quem serve — o André quer o texto, não o mapa.
_LITERAL = re.compile(
    r"("
    r"\bencontr\w+\s+o\s+trecho\b|\bcite\s+o\s+trecho\b|"
    r"\bo\s+que\s+(?:o\s+)?(?:capítulo|capitulo|seção|secao|página|pagina)\b|"
    r"\bqual\s+(?:é\s+)?a\s+definição\b|\btranscrev\w+\b|"
    r"\bcopie\b|\bliteralmente\b"
    r")",
    re.IGNORECASE,
)


def classificar(pergunta: str) -> str:
    """VECTOR, GRAPH ou HYBRID — sem chamar IA.

    A ordem das checagens é a ordem das certezas: pedido literal é o sinal
    mais forte e vence tudo; depois pergunta puramente estrutural; depois
    qualquer cheiro de relação, que cai em HYBRID por ser a escolha segura —
    ela inclui o vetor, então errar aqui custa um pouco de latência, não uma
    resposta pior.
    """
    texto = (pergunta or "").strip()
    if not texto:
        return VECTOR

    if _LITERAL.search(texto):
        return VECTOR
    if _SO_GRAFO.search(texto):
        return GRAPH
    if _RELACIONAL.search(texto):
        return HYBRID
    return VECTOR


def _do_vetor(achados: list[dict[str, object]]) -> list[KnowledgeHit]:
    """Converte o resultado da biblioteca para o formato comum.

    A biblioteca continua devolvendo o dicionário dela, intacto — quem chama
    `biblioteca.buscar` direto (testes antigos, outros pontos) não vê
    diferença. A tradução mora aqui.
    """
    hits: list[KnowledgeHit] = []
    for a in achados:
        titulo = str(a.get("livro") or "")
        hits.append(
            KnowledgeHit(
                text=str(a.get("texto") or ""),
                source=str(a.get("origem") or "") or titulo,
                title=titulo,
                page=int(a["pagina"]) if a.get("pagina") else None,
                score=float(a["nota"]) if a.get("nota") is not None else None,
                retrieval_type=VECTOR,
                document_id=str(a.get("slug") or "") or None,
            )
        )
    return hits


async def buscar(
    pergunta: str, *, modo: str | None = None
) -> tuple[list[KnowledgeHit], dict[str, object]]:
    """A busca de conhecimento inteira. Devolve (resultados, procedência).

    A procedência não é enfeite: é o que o `/porque` mostra depois, e é a
    única forma de o André saber se uma resposta veio de um trecho literal ou
    de uma ligação que a máquina fez entre dois documentos.
    """
    escolhido = modo or classificar(pergunta)
    grafo_ok = knowledge_client.disponivel()

    # A heurística propõe; a disponibilidade dispõe.
    if escolhido in (GRAPH, HYBRID) and not grafo_ok:
        motivo = knowledge_client.impedimento() or "serviço indisponível"
        log.debug("[knowledge] %s indisponível (%s), caindo para vector",
                  escolhido, motivo)
        escolhido = VECTOR

    procedencia: dict[str, object] = {
        "modo": escolhido,
        "vector_hits": 0,
        "graph_hits": 0,
        "descartados": 0,
        "grafo_disponivel": grafo_ok,
    }

    do_vetor: list[KnowledgeHit] = []
    do_grafo: list[KnowledgeHit] = []

    if escolhido in (VECTOR, HYBRID):
        try:
            if not biblioteca.vazia():
                do_vetor = _do_vetor(await biblioteca.buscar(pergunta))
        except Exception as exc:            # a biblioteca é bônus, como sempre
            log.debug("[knowledge] busca vetorial falhou: %s", exc)
            do_vetor = []
        procedencia["vector_hits"] = len(do_vetor)

    if escolhido in (GRAPH, HYBRID):
        do_grafo = await knowledge_client.buscar_grafo(pergunta)
        procedencia["graph_hits"] = len(do_grafo)

        # O serviço pode ter caído ENTRE a checagem lá em cima e a chamada.
        # Nesse caso a busca foi vetorial na prática, e dizer "híbrida" no
        # /porque seria a Livia se atribuindo um trabalho que não fez.
        if not do_grafo and not knowledge_client.disponivel():
            procedencia["modo"] = VECTOR
            procedencia["grafo_disponivel"] = False
            procedencia["grafo_caiu_no_meio"] = True
            escolhido = VECTOR if escolhido == HYBRID else escolhido

        # Grafo não trouxe nada numa pergunta que foi para ele sozinha: sem
        # rede de segurança, a pergunta ficaria sem contexto nenhum.
        if escolhido == GRAPH and not do_grafo:
            try:
                if not biblioteca.vazia():
                    do_vetor = _do_vetor(await biblioteca.buscar(pergunta))
            except Exception:
                do_vetor = []
            procedencia["vector_hits"] = len(do_vetor)
            procedencia["modo"] = VECTOR if not do_grafo else escolhido
            log.debug("[knowledge] grafo vazio, complementando com vector")

    antes = len(do_vetor) + len(do_grafo)
    juntos = knowledge.deduplicar([*do_vetor, *do_grafo])
    procedencia["duplicados"] = antes - len(juntos)

    finais = knowledge.orcamento(
        juntos, config.KNOWLEDGE_MAX_RESULTS, config.KNOWLEDGE_MAX_CHARS
    )
    procedencia["descartados"] = len(juntos) - len(finais)
    procedencia["fontes"] = sorted({h.rotulo() for h in finais})
    procedencia["relacoes"] = [
        h.relation_path for h in finais if h.relation_path
    ]

    conflitos = knowledge.divergencias(finais)
    if conflitos:
        procedencia["divergencias"] = conflitos

    log.debug(
        "[knowledge] mode=%s vector_hits=%d graph_hits=%d deduplicated=%d final=%d",
        procedencia["modo"], procedencia["vector_hits"], procedencia["graph_hits"],
        procedencia["duplicados"], len(finais),
    )
    return finais, procedencia


def formatar(hits: list[KnowledgeHit], procedencia: dict[str, object]) -> str:
    """O bloco de contexto, já com o aviso de divergência quando houver."""
    if not hits:
        return ""
    bloco = knowledge.formatar(hits, str(procedencia.get("modo") or HYBRID))
    aviso = knowledge.aviso_de_divergencia(
        list(procedencia.get("divergencias") or [])
    )
    return f"{bloco}\n\n{aviso}" if aviso else bloco
