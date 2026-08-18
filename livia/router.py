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
CATALOGO: dict[str, ProviderSpec] = {
    "groq": ProviderSpec(
        "groq", frozenset({CHAT, FAST, TOOLS, STRUCTURED}), priority=0
    ),
    "gemini": ProviderSpec(
        "gemini",
        frozenset({CHAT, TOOLS, STRUCTURED, URL_CONTEXT, LONG_CONTEXT, EMBEDDINGS}),
        priority=1,
    ),
    "openrouter": ProviderSpec(
        "openrouter", frozenset({CHAT, SPECIALIST}), priority=2
    ),
}

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
        return TaskProfile("estruturado", frozenset({STRUCTURED}), "groq")

    if precisa_ferramentas:
        return TaskProfile("ferramentas", frozenset({TOOLS}), "groq")

    if _URL.search(texto):
        # Só o Gemini abre links por conta própria.
        return TaskProfile("com_link", frozenset({URL_CONTEXT}), "gemini")

    if tem_documento:
        return TaskProfile("documento", frozenset({LONG_CONTEXT}), "gemini")

    if _TECNICO.search(texto):
        return TaskProfile("tecnico", frozenset({CHAT}), "openrouter")

    if len(texto) <= _CURTO:
        return TaskProfile("conversa", frozenset({CHAT}), "groq")

    return TaskProfile("geral", frozenset({CHAT}), None)


def disponiveis() -> list[str]:
    """Provedores configurados, na ordem que o André pediu no .env."""
    chaves = {
        "gemini": config.GEMINI_API_KEY,
        "groq": config.GROQ_API_KEY,
        "openrouter": getattr(config, "OPENROUTER_API_KEY", ""),
    }
    ordem = [p for p in config.PROVIDERS if chaves.get(p)]
    # Um provedor com chave mas fora de LIVIA_PROVIDERS entra no fim, para
    # configurar a chave já bastar — sem exigir mexer em duas variáveis.
    for nome, chave in chaves.items():
        if chave and nome not in ordem and nome in CATALOGO:
            ordem.append(nome)
    return ordem


def fila(perfil: TaskProfile, candidatos: list[str] | None = None) -> list[str]:
    """A ordem de tentativa para esta tarefa.

    Capacidade é filtro; preferência é ordenação. Um provedor que não atende
    o requisito duro some da fila — não adianta tentar e falhar.
    """
    nomes = candidatos if candidatos is not None else disponiveis()
    aptos = [
        n for n in nomes
        if n in CATALOGO and perfil.required_capabilities <= CATALOGO[n].capabilities
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
    return [n for n in nomes if n in CATALOGO and capacidade in CATALOGO[n].capabilities]
