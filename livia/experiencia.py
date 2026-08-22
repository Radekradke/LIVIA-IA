"""Memória episódica: o que já foi tentado, e o que aquilo ensinou.

A memória semântica guarda o que é VERDADE ("o CRM usa Supabase"). Aqui fica o
que ACONTECEU ("tentei WPS, falhou; configuração manual, funcionou"). São
coisas diferentes e misturá-las estraga as duas: fato vira anedota, e tentativa
frustrada vira crença.

O CICLO
-------
    registrar    cada tarefa vira uma experiência com resultado
    recuperar    tarefa parecida traz de volta o que aconteceu antes
    consolidar   padrão repetido vira lição; padrão de fracasso vira anti-pattern
    aprovar      procedimento repetido vira SKILL CANDIDATA, e o André decide

O QUE CONTA COMO SUCESSO
------------------------
Este é o ponto onde é fácil se enganar. A tentação é marcar sucesso porque a
IA respondeu — mas responder não é acertar. Só contam evidências operacionais:
a ferramenta rodou sem erro, o arquivo foi criado, o teste passou, o André
confirmou. E o sinal mais forte de FALHA é ele corrigir.

Quando não há evidência de nenhum dos lados, o resultado fica `None`, e a
experiência não pesa em consolidação nenhuma. Um "não sei" honesto vale mais
que um sucesso inventado, porque é o sucesso inventado que vira heurística
errada daqui a três meses.

POR QUE ISTO NÃO É MARKDOWN
---------------------------
Memórias e skills são .md porque o André lê, corrige e apaga. Experiência é
registro de alto volume e valor individual baixo — dezenas por dia, quase
nenhuma interessante sozinha. Vive no SQLite. O que vira REGRA sobe para
data/lessons/ como Markdown, e aí sim é auditável e editável, porque aí sim
entra no prompt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np

from . import config, db, docs, embeddings
from .store import lessons, skills

log = logging.getLogger("livia.experiencia")

HEURISTICA = "heuristica"
ANTI_PATTERN = "anti-pattern"

# Abaixo disto, duas experiências não falam da mesma situação.
LIMIAR_PARECIDA = 0.62

# Para virar regra, o padrão precisa concordar consigo mesmo. 0.75 = três de
# cada quatro ocorrências apontando para o mesmo lado.
CONCORDANCIA_MINIMA = 0.75


# --------------------------------------------------------------------------
# Detecção de sinais
# --------------------------------------------------------------------------

# Correção explícita do André. Deliberadamente específico: "não" sozinho
# aparece em conversa normal o tempo todo.
_CORRIGE = re.compile(
    r"(não\s+é\s+(isso|bem\s+assim)|"
    r"(isso|está|ficou|tá)\s+errad[oa]|"
    r"\bnão\s+funcionou\b|\bnão\s+deu\s+certo\b|"
    r"\bna\s+verdade\b|\bcorrigindo\b|\bcorrig[ei]\b|"
    r"\bnão\s+usamos?\s+mais\b|\bdeixamos?\s+de\s+usar\b|"
    r"\bmigramos?\s+para\b|\btrocamos?\s+(?:o|a|de)\b|"
    r"\bnão\s+foi\s+isso\b|\bpelo\s+contrário\b|"
    r"\brefaz\b|\brefaça\b|\bde\s+novo,?\s+mas\b)",
    re.IGNORECASE,
)

# Preferência declarada — não é correção, mas é sinal forte de memória.
_PREFERE = re.compile(
    r"(\bprefiro\b|\bprefira\b|\bgosto\s+mais\s+de\b|"
    r"\ba\s+partir\s+de\s+agora\b|\bdaqui\s+em\s+diante\b|"
    r"\bsempre\s+que\b.*\bfaça\b|\bnunca\s+mais\b)",
    re.IGNORECASE,
)

# Confirmação. Vale como sinal fraco: "perfeito" pode ser cortesia.
_CONFIRMA = re.compile(
    r"^(perfeito|isso|isso\s+mesmo|exato|exatamente|funcionou|"
    r"deu\s+certo|era\s+isso|obrigad[oa]|valeu|show|boa)\b",
    re.IGNORECASE,
)


def e_correcao(mensagem: str) -> bool:
    """O André está corrigindo alguma coisa?

    Correção é o sinal de aprendizado mais valioso que existe: é a única vez
    em que a resposta certa vem de graça, dita por quem sabe.
    """
    return bool(_CORRIGE.search(mensagem or ""))


def e_preferencia(mensagem: str) -> bool:
    return bool(_PREFERE.search(mensagem or ""))


def e_confirmacao(mensagem: str) -> bool:
    return bool(_CONFIRMA.match((mensagem or "").strip()))


@dataclass
class Sinais:
    """O que dá para afirmar sobre como a tarefa terminou."""

    sucesso: bool | None
    motivo: str
    feedback: str = ""

    def to_json(self) -> dict[str, object]:
        return {"sucesso": self.sucesso, "motivo": self.motivo}


def avaliar(
    acoes: list[dict[str, object]],
    *,
    resposta: str = "",
    proxima_mensagem: str = "",
) -> Sinais:
    """Deu certo? Só responde sim ou não com evidência operacional.

    A ordem das checagens é a ordem de autoridade das evidências: o que o
    André disse vale mais que o que a máquina observou, e o que a máquina
    observou vale mais que a impressão de que a resposta ficou boa.
    """
    if proxima_mensagem and e_correcao(proxima_mensagem):
        return Sinais(False, "o André corrigiu logo depois", proxima_mensagem[:400])

    if proxima_mensagem and e_confirmacao(proxima_mensagem):
        return Sinais(True, "o André confirmou", proxima_mensagem[:400])

    falhas = [a for a in acoes if a.get("ok") is False]
    if falhas:
        nomes = ", ".join(str(a.get("nome") or "?") for a in falhas)
        return Sinais(False, f"ferramenta devolveu erro: {nomes}")

    if acoes and all(a.get("ok") for a in acoes):
        nomes = ", ".join(str(a.get("nome") or "?") for a in acoes)
        return Sinais(True, f"as ações rodaram sem erro: {nomes}")

    # Resposta bonita não é evidência de nada. Sem ação executada e sem
    # palavra do André, o honesto é não saber.
    return Sinais(None, "sem evidência operacional de sucesso ou falha")


# --------------------------------------------------------------------------
# Registro
# --------------------------------------------------------------------------


def registrar(
    tarefa: str,
    *,
    acoes: list[dict[str, object]] | None = None,
    resultado: str = "",
    sinais: Sinais | None = None,
    contexto: str = "",
    licao: str = "",
    escopo: str = "global",
    conversa: int | None = None,
) -> int | None:
    """Grava uma experiência. Devolve o id, ou None quando não vale gravar."""
    if not config.EXPERIENCE_ENABLED or not tarefa.strip():
        return None

    acoes = acoes or []
    sinais = sinais or avaliar(acoes)

    # Conversa sem ação nenhuma e sem veredito não é experiência: é bate-papo.
    # Gravar tudo encheria a tabela de ruído e afogaria os casos que importam.
    if not acoes and sinais.sucesso is None:
        return None

    id_ = db.experiencia_gravar(
        tarefa.strip()[:2000],
        contexto=contexto[:2000],
        acoes=acoes,
        resultado=resultado[:2000],
        sucesso=sinais.sucesso,
        erro="; ".join(
            str(a.get("resultado") or "")[:200] for a in acoes if a.get("ok") is False
        )[:1000],
        feedback=sinais.feedback,
        licao=licao,
        escopo=escopo,
        conversa=conversa,
        hash_texto=embeddings.hash_conteudo(_texto_da_experiencia(tarefa, acoes)),
    )
    log.debug("[experiencia] gravada=%s sucesso=%s", id_, sinais.sucesso)
    return id_


def _texto_da_experiencia(tarefa: str, acoes: list[dict[str, object]]) -> str:
    partes = [tarefa]
    partes += [str(a.get("nome") or "") for a in acoes]
    return "\n".join(p for p in partes if p)


def _texto_de(linha: dict[str, object]) -> str:
    return _texto_da_experiencia(
        str(linha.get("tarefa") or ""),
        list(linha.get("acoes") or []),
    )


# --------------------------------------------------------------------------
# Recuperação
# --------------------------------------------------------------------------


async def indexar(limite: int = 500) -> int:
    """Gera os vetores das experiências que ainda não têm."""
    if not embeddings.disponivel():
        return 0

    pendentes: dict[str, str] = {}
    assinatura_atual = embeddings.assinatura()
    for linha in db.experiencia_listar(limite):
        if linha.get("vetor") is not None and embeddings.compativel(
            str(linha.get("assinatura") or ""), assinatura_atual
        ):
            continue
        pendentes[str(linha["id"])] = _texto_de(linha)

    if not pendentes:
        return 0

    try:
        vetores = await embeddings.com_cache(pendentes, embeddings.DOCUMENTO)
    except embeddings.EmbeddingError:
        return 0

    for id_, vetor in vetores.items():
        db.experiencia_atualizar(int(id_), vetor=vetor, assinatura=assinatura_atual)
    return len(vetores)


async def recuperar(
    tarefa: str, *, limite: int | None = None, limiar: float = LIMIAR_PARECIDA
) -> list[dict[str, object]]:
    """Experiências parecidas com a tarefa de agora, da mais parecida à menos.

    É o que faz "como resolvemos aquele problema parecido?" funcionar meses
    depois, sem o André lembrar das palavras exatas que usou na época.
    """
    limite = limite if limite is not None else config.EXPERIENCE_MAX_ITEMS
    if limite <= 0 or not tarefa.strip():
        return []

    await indexar()
    linhas = [l for l in db.experiencia_listar(500) if l.get("vetor") is not None]
    if not linhas:
        return []

    try:
        alvo, _ = await embeddings.gerar_um(tarefa, embeddings.PERGUNTA)
    except embeddings.EmbeddingError:
        return []

    matriz_linhas = [l for l in linhas if l["vetor"].shape[0] == alvo.shape[0]]
    if not matriz_linhas:
        return []

    matriz = np.vstack([l["vetor"] for l in matriz_linhas])
    notas = embeddings.semelhancas(matriz, alvo)

    achados = []
    for linha, nota in zip(matriz_linhas, notas):
        if float(nota) < limiar:
            continue
        achados.append({**linha, "nota": round(float(nota), 3)})

    achados.sort(key=lambda a: a["nota"], reverse=True)
    log.debug("[experiencia] retrieved=%d", min(len(achados), limite))
    return achados[:limite]


def formatar(achados: list[dict[str, object]]) -> str:
    """O bloco que entra no prompt.

    Cada linha diz o que foi tentado e como terminou. Uma tentativa que falhou
    é tão útil quanto uma que deu certo — talvez mais, porque evita repetir.
    """
    if not achados:
        return ""
    linhas = [
        "# O que já foi tentado em situações parecidas",
        "",
        "Isto é histórico, não regra. Use como pista do que costuma funcionar "
        "e do que já falhou — e diga que está se baseando nisso quando pesar "
        "na resposta.",
        "",
    ]
    for a in achados:
        marca = {True: "funcionou", False: "falhou", None: "sem veredito"}[a.get("sucesso")]
        linhas.append(f"- **{a.get('tarefa')}** → {marca}")
        if a.get("erro"):
            linhas.append(f"  - erro: {str(a['erro'])[:200]}")
        if a.get("licao"):
            linhas.append(f"  - lição: {a['licao']}")
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Consolidação: experiência -> lição
# --------------------------------------------------------------------------


def _agrupar(linhas: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Junta experiências que falam da mesma situação.

    Agrupamento guloso por semelhança: cada experiência entra no primeiro
    grupo cujo representante ela se parece. Não é o melhor algoritmo de
    clusterização do mundo, e não precisa ser — são dezenas de itens, e o
    critério de promoção mais adiante é conservador o bastante para tolerar
    um agrupamento imperfeito.
    """
    grupos: list[list[dict[str, object]]] = []
    for linha in linhas:
        vetor = linha.get("vetor")
        if vetor is None:
            continue
        for grupo in grupos:
            base = grupo[0]["vetor"]
            if base.shape != vetor.shape:
                continue
            if float(np.dot(base, vetor)) >= LIMIAR_PARECIDA:
                grupo.append(linha)
                break
        else:
            grupos.append([linha])
    return grupos


async def consolidar(*, aplicar: bool = False) -> dict[str, object]:
    """Procura padrões nas experiências e propõe lições.

    A regra da promoção, e o motivo dela:

      - mínimo de N experiências (LIVIA_LEARNING_MIN_EXPERIENCES). Uma
        ocorrência é anedota. Virar regra a partir de um caso é como a Livia
        aprenderia superstição.
      - concordância mínima. Se metade funcionou e metade falhou, não há
        padrão — há variabilidade, e a honestidade é não inventar regra.
      - só experiências com veredito. As `None` não votam.
    """
    minimo = max(2, config.LEARNING_MIN_EXPERIENCES)
    await indexar()

    linhas = [
        l for l in db.experiencia_todas()
        if l.get("vetor") is not None and l.get("sucesso") is not None
    ]

    relatorio: dict[str, object] = {
        "heuristicas": [], "anti_patterns": [], "candidatas": [], "aplicado": aplicar
    }

    for grupo in _agrupar(linhas):
        if len(grupo) < minimo:
            continue

        sucessos = [l for l in grupo if l.get("sucesso")]
        falhas = [l for l in grupo if l.get("sucesso") is False]
        total = len(grupo)

        if len(sucessos) / total >= CONCORDANCIA_MINIMA:
            proposta = _propor_heuristica(grupo, sucessos)
            relatorio["heuristicas"].append(proposta)
            if aplicar:
                _gravar_licao(proposta, HEURISTICA)
                candidata = _propor_candidata(grupo, sucessos)
                if candidata:
                    relatorio["candidatas"].append(candidata)

        elif len(falhas) / total >= CONCORDANCIA_MINIMA:
            proposta = _propor_anti_pattern(grupo, falhas)
            relatorio["anti_patterns"].append(proposta)
            if aplicar:
                _gravar_licao(proposta, ANTI_PATTERN)

    return relatorio


def _resumo_da_situacao(grupo: list[dict[str, object]]) -> str:
    """O texto que descreve a situação do grupo: a tarefa mais curta dele.

    A mais curta costuma ser a mais genérica, e é justamente a generalidade
    que se quer numa regra. Deixar o modelo redigir isso custaria uma chamada
    de API por grupo e abriria espaço para invenção.
    """
    tarefas = sorted((str(l.get("tarefa") or "") for l in grupo), key=len)
    return tarefas[0][:200] if tarefas else "situação recorrente"


def _acoes_do(grupo: list[dict[str, object]]) -> list[str]:
    nomes: list[str] = []
    for linha in grupo:
        for acao in linha.get("acoes") or []:
            nome = str(acao.get("nome") or "")
            if nome and nome not in nomes:
                nomes.append(nome)
    return nomes


def _propor_heuristica(grupo, sucessos) -> dict[str, object]:
    situacao = _resumo_da_situacao(grupo)
    acoes = _acoes_do(sucessos)
    caminho = " → ".join(acoes) if acoes else "o caminho que deu certo"
    return {
        "nome": f"heuristica-{docs.slugify(situacao)[:40]}",
        "situacao": situacao,
        "descricao": f"Em situações como \"{situacao}\", {caminho} costuma resolver.",
        "ocorrencias": len(grupo),
        "acertos": len(sucessos),
        "confianca": round(len(sucessos) / len(grupo), 2),
        "evidencia": [l["id"] for l in grupo],
        "acoes": acoes,
    }


def _propor_anti_pattern(grupo, falhas) -> dict[str, object]:
    situacao = _resumo_da_situacao(grupo)
    acoes = _acoes_do(falhas)
    erros = [str(l.get("erro") or "") for l in falhas if l.get("erro")]
    return {
        "nome": f"evitar-{docs.slugify(situacao)[:40]}",
        "situacao": situacao,
        "descricao": (
            f"Em situações como \"{situacao}\", "
            + (f"{' → '.join(acoes)} falhou" if acoes else "esse caminho falhou")
            + f" em {len(falhas)} de {len(grupo)} tentativas."
        ),
        "ocorrencias": len(grupo),
        "falhas": len(falhas),
        "confianca": round(len(falhas) / len(grupo), 2),
        "evidencia": [l["id"] for l in grupo],
        "motivo": erros[0][:200] if erros else "",
        "acoes": acoes,
    }


def _gravar_licao(proposta: dict[str, object], tipo: str) -> None:
    """A lição vira Markdown em data/lessons/ — legível e apagável.

    Isto é o que a Livia concluiu SOZINHA, e vai influenciar respostas futuras.
    Deixar essa conclusão só num banco binário, sem o André poder ler e
    discordar, seria dar a ela uma crença fora do alcance dele.
    """
    corpo_linhas = [
        f"**Situação:** {proposta['situacao']}",
        "",
    ]
    if tipo == ANTI_PATTERN:
        corpo_linhas += [
            f"**Evitar:** {' → '.join(proposta.get('acoes') or []) or 'o caminho registrado'}",
            "",
            f"**Motivo:** falhou em {proposta.get('falhas')} de "
            f"{proposta.get('ocorrencias')} ocorrências."
            + (f" Erro típico: {proposta['motivo']}" if proposta.get("motivo") else ""),
        ]
    else:
        corpo_linhas += [
            f"**Fazer:** {' → '.join(proposta.get('acoes') or []) or 'o caminho registrado'}",
            "",
            f"**Base:** funcionou em {proposta.get('acertos')} de "
            f"{proposta.get('ocorrencias')} ocorrências.",
        ]
    corpo_linhas += [
        "",
        "_Deduzido pela Livia a partir das próprias experiências. "
        "Se estiver errado, edite ou apague este arquivo._",
    ]

    lessons.save(
        str(proposta["nome"]),
        str(proposta["descricao"]),
        "\n".join(corpo_linhas),
        kind="lesson",
        extra={
            "scope": "global",
            "status": docs.ATIVA,
            "importance": f"{min(0.85, 0.5 + 0.1 * int(proposta['ocorrencias'])):.2f}",
            "source": f"experiencia:{tipo}",
            "evidence": ",".join(str(i) for i in proposta.get("evidencia") or []),
        },
    )

    for id_ in proposta.get("evidencia") or []:
        db.experiencia_atualizar(int(id_), promovida=1)


# --------------------------------------------------------------------------
# Skills candidatas
# --------------------------------------------------------------------------


def _propor_candidata(grupo, sucessos) -> dict[str, object] | None:
    """Uma sequência de ações que deu certo várias vezes pode virar skill.

    CANDIDATA, não skill. A Livia não promove nada sozinha por padrão, e isso
    é decisão de segurança, não de rigor: uma skill entra em todo prompt
    futuro como procedimento a seguir. Deixá-la escrever isso sem ninguém ler
    é deixá-la mudar o próprio comportamento em silêncio — que é exatamente o
    que a arquitetura inteira existe para evitar.
    """
    acoes = _acoes_do(sucessos)
    if len(acoes) < 2:
        return None  # uma ação só não é procedimento

    situacao = _resumo_da_situacao(grupo)
    nome = f"como-{docs.slugify(situacao)[:40]}"
    if skills.get(nome) is not None:
        return None

    passos = "\n".join(f"{i}. {a}" for i, a in enumerate(acoes, 1))
    confianca = round(len(sucessos) / len(grupo), 2)

    id_ = db.candidata_gravar(
        nome=nome,
        descricao=f"Procedimento observado em \"{situacao}\".",
        passos=passos,
        evidencia=[l["id"] for l in grupo],
        confianca=confianca,
        origem="experiencia",
    )

    if config.SKILL_AUTO_APPROVE and confianca >= 0.95 and len(grupo) >= 5:
        # Só com autorização explícita no .env, e ainda assim só para o que é
        # muito repetido e nunca falhou.
        aprovar(id_)

    return {"id": id_, "nome": nome, "confianca": confianca, "passos": passos}


def candidatas() -> list[dict[str, object]]:
    return db.candidata_listar("pendente")


def aprovar(id_: int) -> dict[str, object] | None:
    """Vira skill de verdade, em data/skills/, e passa a entrar no prompt."""
    candidata = db.candidata_obter(id_)
    if candidata is None or candidata.get("situacao") != "pendente":
        return None

    doc = skills.save(
        str(candidata["nome"]),
        str(candidata["descricao"]),
        str(candidata["passos"]),
        kind="skill",
        extra={"source": "experiencia (aprovada pelo André)"},
    )
    db.candidata_situacao(id_, "aprovada")
    return doc.to_json()


def rejeitar(id_: int) -> bool:
    """Recusada. Fica registrada como recusada para não ser proposta de novo."""
    return db.candidata_situacao(id_, "rejeitada")


def estatisticas() -> dict[str, int]:
    todas = db.experiencia_listar(1000)
    return {
        "total": len(todas),
        "sucessos": sum(1 for e in todas if e.get("sucesso") is True),
        "falhas": sum(1 for e in todas if e.get("sucesso") is False),
        "sem_veredito": sum(1 for e in todas if e.get("sucesso") is None),
        "licoes": lessons.count(),
        "candidatas": len(candidatas()),
    }
