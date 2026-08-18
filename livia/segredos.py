"""Impedir que chave de API entre na memória.

POR QUE ISTO É CÓDIGO E NÃO INSTRUÇÃO
-------------------------------------
O prompt do `learner.py` já pedia "nunca guarde senhas, chaves de API ou
dados sensíveis". Pedir não é garantir: quem decide é um modelo, e o André
colou três chaves de API no chat só durante o desenvolvimento deste projeto.
A pergunta não é *se* isso vai acontecer — é o que acontece depois.

E depois é ruim. Memória entra em TODA conversa futura, então a chave iria:

    para o prompt        -> enviada a todos os provedores, sempre
    para o backup.zip    -> baixado, copiado, mandado por e-mail
    para o git           -> se a pasta data/ for versionada um dia

Uma verificação determinística não depende de o modelo se comportar bem.

RECUSAMOS, NÃO CENSURAMOS
-------------------------
Não trocamos a chave por `***`. Uma memória cujo miolo foi apagado não serve
para nada, e mantê-la sugere que a informação está lá. A memória inteira é
recusada, com o motivo dito em voz alta.
"""

from __future__ import annotations

import re

# Prefixos que provedores usam de verdade. Reconhecer o formato é mais
# confiável que adivinhar por entropia, que confunde hash de commit e UUID
# com credencial.
_FORMATOS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("chave da OpenAI/OpenRouter", re.compile(r"\bsk-(or-v1-)?[A-Za-z0-9_-]{20,}")),
    ("chave da Groq", re.compile(r"\bgsk_[A-Za-z0-9]{20,}")),
    ("chave do Google/Gemini", re.compile(r"\bAIza[A-Za-z0-9_-]{30,}")),
    ("chave da Anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("token do GitHub", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("token do Slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("chave da AWS", re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("token JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("chave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("senha em URL", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]{4,}@")),
)

# Frase que declara um segredo mesmo sem o valor ter formato conhecido:
# "a senha do banco é boi123". O valor vem depois do separador, e é ele que
# não pode ficar guardado.
#
# O vão de até três palavras entre a palavra-chave e o separador existe porque
# ninguém escreve "senha: x" numa conversa — escreve "a senha do banco é x".
# Exigir o separador colado deixava passar justamente a forma mais comum.
#
# `\be\b` NÃO entra na lista de separadores: é o "e" de ligação do português,
# e com ele "senha e login" viraria falso positivo.
_DECLARACAO = re.compile(
    r"\b(?:senha|password|passwd|secret|token|api[ _-]?key|"
    r"chave\s+(?:de\s+api|secreta|privada)|credencial|client[ _-]?secret)\b"
    r"(?:\s+\w+){0,3}\s*"
    r"(?::|=|\bé\b|\bis\b)\s*"
    r"\S{4,}",
    re.IGNORECASE,
)


def encontrar(texto: str) -> list[str]:
    """Nomes do que parece segredo dentro do texto. Vazio quando está limpo."""
    if not texto:
        return []

    achados: list[str] = []
    for rotulo, padrao in _FORMATOS:
        if padrao.search(texto):
            achados.append(rotulo)
    if _DECLARACAO.search(texto):
        achados.append("senha ou token escrito por extenso")
    return achados


def conferir(*partes: str) -> list[str]:
    """Mesma checagem sobre vários campos — nome, descrição e corpo."""
    achados: list[str] = []
    for parte in partes:
        for achado in encontrar(parte or ""):
            if achado not in achados:
                achados.append(achado)
    return achados


def motivo(achados: list[str]) -> str:
    """Frase para mostrar ao André. Nunca repete o valor encontrado."""
    lista = ", ".join(achados)
    return (
        f"Isso não vai para a memória: parece conter {lista}. "
        "Memória entra em toda conversa e vai junto no backup — segredo ali "
        "acaba vazando. Guarde a chave só no .env."
    )
