"""Roteador: qual provedor atende cada tarefa.

A decisão é LOCAL e DETERMINÍSTICA. Nada aqui chama uma IA para descobrir
qual IA usar — isso dobraria a latência e o consumo de cota para responder
uma pergunta que regras simples respondem bem.

Duas coisas decidem o destino:

  CAPACIDADE   requisito duro. Pedido com ferramentas só pode ir para quem
               declara `tools`; JSON estruturado, para quem declara
               `structured`. Provedor sem a capacidade sai da fila.

  PREFERÊNCIA  requisito mole. Mensagem com link prefere quem sabe abrir
               páginas; conversa curta prefere o mais rápido. Se o preferido
               estiver fora, o próximo compatível assume.

O resultado é sempre uma FILA, nunca uma escolha única — quem chama tenta em
ordem e cai para o seguinte quando um falha.

LOCAL PRIMEIRO
--------------
Quando o Ollama está ligado, ele encabeça a fila de tudo que sabe fazer:
é grátis, não tem cota e nada sai da máquina. Isso NÃO é regra fixa — a
ordem sai de LIVIA_PROVIDERS, e quem quiser a nuvem na frente é só escrever
`LIVIA_PROVIDERS=groq,ollama`. O que o código garante é a capacidade: o
Ollama nunca recebe uma tarefa que o modelo local não sabe cumprir.

Com LIVIA_LOCAL_ONLY=1, os provedores de nuvem somem da fila inteira. Não
é preferência: é filtro. Nenhuma linha de conversa sai da máquina.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config

# ── capacidades ───────────────────────────────────────────────────────────

CHAT = "chat"
FAST = "fast"
TOOLS = "tools"
STRUCTURED = "structured"
URL_CONTEXT = "url_context"
LONG_CONTEXT = "long_context"
EMBEDDINGS = "embeddings"
SPECIALIST = "specialist"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    capabilities: frozenset[str]
    priority: int          # menor = tentado antes, em empate


@dataclass(frozen=True)
class TaskProfile:
    kind: str
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    preferred_provider: str | None = None


# O OpenRouter fica fora de `tools` e `structured` de propósito: o modelo
# gratuito que ele escolhe varia, e nem todos honram esses formatos. Melhor
# não oferecer do que oferecer e quebrar de forma imprevisível.
#
# O Ollama declara FAST porque o modelo pequeno local resolve triagem bem, e
# EMBEDDINGS porque serve vetor local. Não declara TOOLS aqui: essa depende do
# modelo que o André baixou, e é acrescentada em `capacidades()` só quando ele
# confirma em LIVIA_OLLAMA_TOOLS=1.
CATALOGO: dict[str, ProviderSpec] = {
    "ollama": ProviderSpec(
        "ollama",
        frozenset({CHAT, FAST, STRUCTURED, EMBEDDINGS, LONG_CONTEXT}),
        priority=0,
    ),
    "groq": ProviderSpec(
        "groq", frozenset({CHAT, FAST, TOOLS, STRUCTURED}), priority=1
    ),
    "gemini": ProviderSpec(
        "gemini",
        frozenset({CHAT, TOOLS, STRUCTURED, URL_CONTEXT, LONG_CONTEXT, EMBEDDINGS}),
        priority=2,
    ),
    "openrouter": ProviderSpec(
        "openrouter", frozenset({CHAT, SPECIALIST}), priority=3
    ),
}

def capacidades(nome: str) -> frozenset[str]:
    """O que este provedor sabe fazer AGORA, com a configuração atual.

    Existe porque o Ollama é o único cujo repertório muda conforme a máquina:
    o modelo que o André baixou pode ou não saber chamar função, e essa
    resposta está no .env, não no catálogo. Todos os outros são estáticos.
    """
    spec = CATALOGO.get(nome)
    if spec is None:
        return frozenset()
    if nome == "ollama" and config.OLLAMA_TOOLS:
        return spec.capabilities | {TOOLS}
    return spec.capabilities


def local(nome: str) -> bool:
    return nome in config.LOCAL_PROVIDERS


_URL = re.compile(r"https?://\S+", re.IGNORECASE)

# Sinais de tarefa técnica. Deliberadamente específicos: "código" pega, mas
# "como funciona" não — senão toda pergunta viraria tarefa de especialista.
_TECNICO = re.compile(
    r"\b(refatora\w*|arquitetura|algoritmo|complexidade|"
    r"stack\s?trace|traceback|depura\w*|debug\w*|"
    r"revis\w+\s+(?:o\s+)?c[óo]digo|code\s?review|"
    r"otimiz\w+\s+(?:o\s+|a\s+)?(?:c[óo]digo|consulta|query|performance)|"
    r"por\s?que\s+(?:esse|este|o)\s+c[óo]digo)\b",
    re.IGNORECASE,
)

_CURTO = 220   # caracteres; abaixo disso é conversa, não tarefa


def _preferido(padrao: str | None, *exigidas: str) -> str | None:
    """Quem deveria atender primeiro, dando a vez ao local quando ele serve.

    A regra é uma só: se existe um provedor local ligado e ele tem TODAS as
    capacidades que a tarefa exige, ele passa na frente — custa zero e nada
    sai da máquina. Não tendo, vale o padrão de sempre.

    Isto é só preferência. A fila continua completa atrás, e o `brain` cai
    para o próximo quando o local falha ou está fora do ar.
    """
    for nome in config.LOCAL_PROVIDERS:
        if configurado(nome) and set(exigidas) <= capacidades(nome):
            return nome
    return padrao


def classificar(
    mensagem: str,
    *,
    precisa_ferramentas: bool = False,
    precisa_estruturado: bool = False,
    tem_documento: bool = False,
) -> TaskProfile:
    """Lê a mensagem e devolve o perfil da tarefa. Sem chamar IA."""
    texto = (mensagem or "").strip()

    if precisa_estruturado:
        return TaskProfile(
            "estruturado", frozenset({STRUCTURED}), _preferido("groq", STRUCTURED)
        )

    if precisa_ferramentas:
        return TaskProfile(
            "ferramentas", frozenset({TOOLS}), _preferido("groq", TOOLS)
        )

    if _URL.search(texto):
        # Só o Gemini abre links por conta própria. Nem o modelo local: ele
        # não busca a página, ele inventa o que acha que estava nela.
        return TaskProfile("com_link", frozenset({URL_CONTEXT}), "gemini")

    if tem_documento:
        return TaskProfile(
            "documento", frozenset({LONG_CONTEXT}), _preferido("gemini", LONG_CONTEXT)
        )

    if _TECNICO.search(texto):
        return TaskProfile(
            "tecnico", frozenset({CHAT}), _preferido("openrouter", CHAT)
        )

    if len(texto) <= _CURTO:
        return TaskProfile("conversa", frozenset({CHAT}), _preferido("groq", CHAT))

    return TaskProfile("geral", frozenset({CHAT}), _preferido(None, CHAT))


def configurado(nome: str) -> bool:
    """Este provedor tem o que precisa para ser tentado?

    Para os de nuvem, é ter chave. Para o Ollama, é estar ligado — ele não usa
    chave nenhuma. Se o servidor local estiver desligado, quem descobre é a
    primeira tentativa, e o `saude` tira ele da fila por alguns segundos.
    """
    if nome == "ollama":
        return bool(config.OLLAMA_ENABLED)
    return bool({
        "gemini": config.GEMINI_API_KEY,
        "groq": config.GROQ_API_KEY,
        "openrouter": getattr(config, "OPENROUTER_API_KEY", ""),
    }.get(nome))


def disponiveis() -> list[str]:
    """Provedores configurados, na ordem que o André pediu no .env."""
    ordem = [p for p in config.PROVIDERS if p in CATALOGO and configurado(p)]
    # Um provedor configurado mas fora de LIVIA_PROVIDERS entra no fim, para
    # configurar a chave já bastar — sem exigir mexer em duas variáveis.
    for nome in CATALOGO:
        if nome not in ordem and configurado(nome):
            ordem.append(nome)

    # Modo totalmente local: a nuvem não é despriorizada, é removida.
    if config.LOCAL_ONLY:
        ordem = [n for n in ordem if local(n)]
    return ordem


def fila(perfil: TaskProfile, candidatos: list[str] | None = None) -> list[str]:
    """A ordem de tentativa para esta tarefa.

    Capacidade é filtro; preferência é ordenação. Um provedor que não atende
    o requisito duro some da fila — não adianta tentar e falhar.
    """
    nomes = candidatos if candidatos is not None else disponiveis()
    aptos = [
        n for n in nomes
        if n in CATALOGO and perfil.required_capabilities <= capacidades(n)
    ]

    def peso(nome: str) -> tuple[int, int, int]:
        preferido = 0 if nome == perfil.preferred_provider else 1
        # Empate desfeito pela ordem do .env; a prioridade do catálogo é o
        # último critério, para a escolha do André prevalecer.
        return (preferido, nomes.index(nome), CATALOGO[nome].priority)

    return sorted(aptos, key=peso)


def escolher(
    mensagem: str,
    *,
    precisa_ferramentas: bool = False,
    precisa_estruturado: bool = False,
    tem_documento: bool = False,
    candidatos: list[str] | None = None,
) -> tuple[TaskProfile, list[str]]:
    """Atalho: classifica e já devolve a fila."""
    perfil = classificar(
        mensagem,
        precisa_ferramentas=precisa_ferramentas,
        precisa_estruturado=precisa_estruturado,
        tem_documento=tem_documento,
    )
    return perfil, fila(perfil, candidatos)


def quem_tem(capacidade: str, candidatos: list[str] | None = None) -> list[str]:
    """Provedores configurados que declaram uma capacidade."""
    nomes = candidatos if candidatos is not None else disponiveis()
    return [n for n in nomes if capacidade in capacidades(n)]
