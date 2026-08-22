"""O contrato do Knowledge Engine — a parte que a Livia conhece.

A biblioteca acha TRECHO PARECIDO. Isso resolve "o que o capítulo 4 diz sobre
X" e não resolve "que relação existe entre X e Y", porque a resposta dessa
segunda pergunta pode estar espalhada em três documentos que não se parecem
entre si. Um grafo de entidades e relações resolve.

Este módulo NÃO sabe o que é Cognee, e é de propósito. Ele define:

    KnowledgeHit    o formato comum de resultado, com procedência obrigatória
    KnowledgeEngine o contrato que qualquer motor precisa cumprir
    deduplicar()    o mesmo trecho vindo por dois caminhos vira um
    orcamento()     quanto disso cabe no prompt, preferindo diversidade
    formatar()      o bloco que entra no prompt, com a mesma proteção de sempre

Quem fala com o motor de verdade é `knowledge_client`, e quem decide qual
motor usar para cada pergunta é `knowledge_router`. Trocar Cognee por outra
coisa (HippoRAG, LightRAG, implementação própria) é escrever um adaptador
novo — nada aqui nem no `server.py` muda.

PROCEDÊNCIA É OBRIGATÓRIA, NÃO É ENFEITE
----------------------------------------
Um grafo pode responder "X causa Y" sem dizer de onde tirou isso. Uma
afirmação sem fonte é indistinguível de alucinação, e num sistema que o André
usa para decidir coisas isso é pior que não responder. Por isso `KnowledgeHit`
exige `source`, e `descartar_sem_procedencia()` joga fora o que chegar sem
ela — com log. Preferimos perder um resultado a exibir um fato órfão.

FONTE ≠ INFERÊNCIA
------------------
Conhecimento vindo direto de um documento e conclusão tirada pelo motor são
coisas diferentes, e a segunda nunca pode se disfarçar da primeira. Por isso
`tipo_conhecimento` existe e a formatação marca inferência explicitamente. É
também o que impede a alucinação acumulativa: inferência não vira fonte de
inferência nova, porque na hora de montar o prompt ela é rotulada.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

log = logging.getLogger("livia.knowledge")

# Como o resultado foi encontrado.
VECTOR = "vector"
GRAPH = "graph"
HYBRID = "hybrid"
MODOS = (VECTOR, GRAPH, HYBRID)

# De onde a afirmação vem. A distinção governa a hierarquia de confiança.
FONTE = "source"        # está escrito no documento
INFERENCIA = "inference"  # o motor concluiu ligando fontes

# Estados de indexação no grafo, gravados no meta.json do documento.
DESLIGADO = "disabled"
PENDENTE = "pending"
PROCESSANDO = "processing"
PRONTO = "ready"
FALHOU = "failed"
DESATUALIZADO = "outdated"
ESTADOS = (DESLIGADO, PENDENTE, PROCESSANDO, PRONTO, FALHOU, DESATUALIZADO)


@dataclass
class KnowledgeHit:
    """Um pedaço de conhecimento recuperado, com de onde ele veio.

    `source` não tem valor padrão de propósito: é o campo que impede o motor
    de afirmar coisas órfãs, e um padrão vazio o tornaria fácil de esquecer.
    """

    text: str
    source: str                      # rótulo legível: "tese.pdf", "doc_a"
    title: str = ""
    page: int | None = None
    score: float | None = None
    retrieval_type: str = VECTOR
    relation_path: list[str] | None = None   # ["Alice", "trabalha em", "Orion"]
    document_id: str | None = None
    chunk_id: str | None = None
    collection_id: str | None = None
    tipo_conhecimento: str = FONTE
    ingerido_em: str = ""
    extras: dict[str, object] = field(default_factory=dict)

    @property
    def tem_procedencia(self) -> bool:
        """Dá para dizer de onde isto veio?"""
        return bool((self.source or "").strip() or (self.document_id or "").strip())

    def rotulo(self) -> str:
        """Como esta origem aparece para o usuário."""
        partes = [self.title or self.source or self.document_id or "?"]
        if self.source and self.title and self.source != self.title:
            partes.append(self.source)
        texto = " · ".join(p for p in partes if p)
        if self.page:
            texto += f", p. {self.page}"
        return texto

    def chave_dedup(self) -> tuple:
        """O que faz dois resultados serem "o mesmo".

        Documento + página + começo do texto. O começo do texto entra porque
        vector e graph podem devolver recortes diferentes do mesmo parágrafo,
        e comparar o texto inteiro nunca casaria.
        """
        assinatura = re.sub(r"\s+", " ", (self.text or "").strip().lower())[:160]
        return (self.document_id or self.source or "", self.page or 0, assinatura)

    def to_json(self) -> dict[str, object]:
        return {
            "text": self.text,
            "source": self.source,
            "title": self.title,
            "page": self.page,
            "score": self.score,
            "retrieval_type": self.retrieval_type,
            "relation_path": self.relation_path,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "collection_id": self.collection_id,
            "tipo": self.tipo_conhecimento,
        }

    @classmethod
    def de_json(cls, dados: dict[str, object]) -> "KnowledgeHit":
        """Constrói a partir do que o sidecar devolveu, sem confiar nele.

        Tudo é convertido e limitado aqui: o serviço é local, mas é um
        processo separado, e um campo com tipo inesperado não pode derrubar
        a conversa.
        """
        def texto(chave: str, teto: int = 4000) -> str:
            valor = dados.get(chave)
            return str(valor)[:teto] if isinstance(valor, (str, int, float)) else ""

        pagina = dados.get("page")
        try:
            pagina = int(pagina) if pagina not in (None, "") else None
        except (TypeError, ValueError):
            pagina = None

        nota = dados.get("score")
        try:
            nota = float(nota) if nota is not None else None
        except (TypeError, ValueError):
            nota = None

        caminho = dados.get("relation_path")
        if isinstance(caminho, list):
            caminho = [str(p)[:120] for p in caminho[:12]]
        else:
            caminho = None

        modo = texto("retrieval_type") or GRAPH
        tipo = texto("tipo") or texto("tipo_conhecimento") or FONTE

        return cls(
            text=texto("text"),
            source=texto("source", 300),
            title=texto("title", 300),
            page=pagina,
            score=nota,
            retrieval_type=modo if modo in MODOS else GRAPH,
            relation_path=caminho,
            document_id=texto("document_id", 200) or None,
            chunk_id=texto("chunk_id", 200) or None,
            collection_id=texto("collection_id", 200) or None,
            tipo_conhecimento=tipo if tipo in (FONTE, INFERENCIA) else FONTE,
            ingerido_em=texto("ingested_at", 40) or texto("ingerido_em", 40),
        )


@runtime_checkable
class KnowledgeEngine(Protocol):
    """O que qualquer motor de conhecimento precisa saber fazer.

    É um Protocol e não uma classe-base porque o motor real mora em OUTRO
    PROCESSO: o que a Livia tem em mãos é um cliente HTTP, e herdar de uma
    classe abstrata só criaria cerimônia. O que importa é a forma.
    """

    async def status(self) -> dict[str, object]:
        """Está de pé? Com quais partes funcionando?"""
        ...

    async def ingest(
        self, document_id: str, trechos: list[dict[str, object]], meta: dict[str, object]
    ) -> dict[str, object]:
        """Constrói o grafo de um documento a partir dos trechos já divididos."""
        ...

    async def graph_search(self, pergunta: str, limite: int) -> list[KnowledgeHit]:
        """Recuperação relacional: entidades, relações, caminhos."""
        ...

    async def remove(self, document_id: str) -> bool:
        """Apaga o conhecimento de um documento, sem tocar nos outros."""
        ...


# --------------------------------------------------------------------------
# Higiene dos resultados
# --------------------------------------------------------------------------


def descartar_sem_procedencia(hits: list[KnowledgeHit]) -> list[KnowledgeHit]:
    """Joga fora o que não sabe de onde veio.

    Parece severo, e é. Um grafo consegue produzir "X causa Y" sem fonte, e
    esse tipo de afirmação é indistinguível de invenção depois que entra no
    prompt. Perder um resultado é barato; exibir um fato órfão como se fosse
    documentado não é.
    """
    bons = [h for h in hits if h.tem_procedencia and h.text.strip()]
    perdidos = len(hits) - len(bons)
    if perdidos:
        log.debug("[knowledge] descartados=%d sem procedência", perdidos)
    return bons


def deduplicar(hits: list[KnowledgeHit]) -> list[KnowledgeHit]:
    """O mesmo conhecimento vindo por dois caminhos vira um só.

    Busca híbrida devolve o mesmo parágrafo pelo vetor e pelo grafo. Mandar os
    dois para o modelo gasta contexto e ainda sugere que a informação é mais
    frequente do que é.

    Quando há empate, fica o que tem mais a dizer: um resultado de grafo
    carrega o caminho da relação, e essa é justamente a informação que o vetor
    não tem.
    """
    melhor: dict[tuple, KnowledgeHit] = {}
    ordem: list[tuple] = []

    for hit in hits:
        chave = hit.chave_dedup()
        atual = melhor.get(chave)
        if atual is None:
            melhor[chave] = hit
            ordem.append(chave)
            continue

        # Critério de desempate, em ordem: quem tem caminho de relação, quem
        # tem nota maior, quem tem texto maior.
        def peso(h: KnowledgeHit) -> tuple:
            return (bool(h.relation_path), h.score or 0.0, len(h.text))

        if peso(hit) > peso(atual):
            # Preserva que o resultado foi confirmado pelos dois caminhos.
            hit.retrieval_type = HYBRID if hit.retrieval_type != atual.retrieval_type else hit.retrieval_type
            melhor[chave] = hit
        elif hit.retrieval_type != atual.retrieval_type:
            atual.retrieval_type = HYBRID

    return [melhor[c] for c in ordem]


def orcamento(
    hits: list[KnowledgeHit], max_itens: int, max_chars: int
) -> list[KnowledgeHit]:
    """Corta o que não cabe, preferindo DIVERSIDADE de fontes.

    Quatro evidências de quatro documentos valem mais que dez trechos da mesma
    página: a segunda situação diz a mesma coisa quatro vezes e ainda ocupa o
    espaço que uma fonte discordante ocuparia. Por isso a primeira rodada pega
    no máximo um resultado por documento, e só depois volta preenchendo.
    """
    if max_itens <= 0 or max_chars <= 0:
        return []

    escolhidos: list[KnowledgeHit] = []
    gasto = 0
    vistos: set[str] = set()

    def cabe(h: KnowledgeHit) -> bool:
        return len(escolhidos) < max_itens and gasto + len(h.text) <= max_chars

    ordenados = sorted(hits, key=lambda h: (h.score or 0.0), reverse=True)

    for rodada in (1, 2):
        for hit in ordenados:
            if hit in escolhidos:
                continue
            fonte = hit.document_id or hit.source
            if rodada == 1 and fonte in vistos:
                continue
            if not cabe(hit):
                continue
            escolhidos.append(hit)
            vistos.add(fonte)
            gasto += len(hit.text)

    return escolhidos


# --------------------------------------------------------------------------
# O bloco que entra no prompt
# --------------------------------------------------------------------------


def formatar(hits: list[KnowledgeHit], modo: str = HYBRID) -> str:
    """Vira contexto para o modelo, com a MESMA proteção da biblioteca.

    O grafo não pode virar uma porta dos fundos para injeção: se um documento
    contém "ignore suas instruções", esse texto vai atravessar o motor de
    conhecimento igual atravessa o vetor. Por isso reusamos as marcas de
    `biblioteca` em vez de inventar outras — uma segunda convenção seria uma
    segunda chance de esquecer a proteção em algum caminho.
    """
    if not hits:
        return ""

    from .biblioteca import ABERTURA_EXTERNA, AVISO_EXTERNO, FECHAMENTO_EXTERNO

    fontes = [h for h in hits if h.tipo_conhecimento == FONTE]
    inferencias = [h for h in hits if h.tipo_conhecimento == INFERENCIA]

    linhas = [
        "Conhecimento ligado à pergunta abaixo, recuperado dos documentos que "
        "o André guardou. Cada item diz de onde veio — cite a origem quando "
        "usar. Se as fontes não responderem, diga isso em vez de completar "
        "com suposição.",
        "",
        AVISO_EXTERNO,
        "",
        ABERTURA_EXTERNA,
    ]

    for hit in fontes:
        cabecalho = f"--- {hit.rotulo()} ---"
        if hit.relation_path:
            # O caminho é o que o grafo tem de próprio: mostra POR QUE aquele
            # trecho apareceu numa pergunta que não cita as palavras dele.
            cabecalho += f"\n[relação: {' → '.join(hit.relation_path)}]"
        linhas.append(cabecalho)
        linhas.append(hit.text.strip())
        linhas.append("")

    if inferencias:
        linhas.append("--- CONCLUSÕES LIGANDO FONTES (não estão escritas em "
                      "lugar nenhum: foram deduzidas ao cruzar os documentos "
                      "citados) ---")
        for hit in inferencias:
            marca = f"[inferência] {hit.text.strip()}"
            if hit.relation_path:
                marca += f"\n  caminho: {' → '.join(hit.relation_path)}"
            marca += f"\n  fontes: {hit.rotulo()}"
            linhas.append(marca)
            linhas.append("")

    linhas.append(FECHAMENTO_EXTERNO)

    if inferencias:
        linhas.append("")
        linhas.append(
            "Trate as conclusões marcadas como [inferência] com cuidado: elas "
            "não são citação de documento, são ligação entre documentos. Diga "
            "que é uma leitura sua ao usá-las."
        )

    return "\n".join(linhas)


def divergencias(hits: list[KnowledgeHit]) -> list[dict[str, object]]:
    """Fontes diferentes falando do mesmo assunto de formas incompatíveis.

    Não tentamos eleger um vencedor. Um documento de 2024 dizendo "usamos
    MySQL" e um de 2026 dizendo "migramos para PostgreSQL" não é contradição
    a resolver — é histórico, e escolher em silêncio destrói a informação.
    O que dá para fazer sem inventar é APONTAR que as fontes divergem.

    A detecção é deliberadamente conservadora: só marca quando o mesmo assunto
    aparece em documentos diferentes com termos que se excluem. Fora disso,
    devolve lista vazia — falso positivo aqui faria a Livia duvidar de tudo.
    """
    if len(hits) < 2:
        return []

    # Pares de termos que raramente convivem como estado atual da mesma coisa.
    # A lista é curta de propósito: é melhor não detectar do que detectar
    # errado, porque o aviso de divergência custa confiança quando é falso.
    opostos = [
        ("mysql", "postgresql"), ("mysql", "postgres"),
        ("firebase", "supabase"),
        ("rest", "graphql"),
        ("javascript", "typescript"),
    ]

    achados: list[dict[str, object]] = []
    for a, b in opostos:
        com_a = [h for h in hits if a in h.text.lower()]
        com_b = [h for h in hits if b in h.text.lower()]
        if not com_a or not com_b:
            continue
        docs_a = {h.document_id or h.source for h in com_a}
        docs_b = {h.document_id or h.source for h in com_b}
        if docs_a == docs_b:
            continue  # o mesmo documento cita os dois: comparação, não conflito
        achados.append({
            "termos": [a, b],
            "fontes_a": sorted(d for d in docs_a if d),
            "fontes_b": sorted(d for d in docs_b if d),
        })

    return achados


def aviso_de_divergencia(conflitos: list[dict[str, object]]) -> str:
    """O texto que entra no prompt quando as fontes discordam."""
    if not conflitos:
        return ""
    linhas = [
        "ATENÇÃO — as fontes recuperadas não concordam entre si:",
    ]
    for c in conflitos:
        a, b = c["termos"]
        linhas.append(
            f"- '{a}' aparece em {', '.join(c['fontes_a'])}; "
            f"'{b}' aparece em {', '.join(c['fontes_b'])}."
        )
    linhas.append(
        "Não escolha um lado em silêncio. Diga que as fontes divergem e mostre "
        "as duas, com a origem de cada uma. Se houver data confiável indicando "
        "qual é mais recente, pode dizer isso — sem inventar data que não está "
        "escrita."
    )
    return "\n".join(linhas)
