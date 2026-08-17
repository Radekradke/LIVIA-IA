"""Senha de acesso — obrigatória antes de expor isto para fora da máquina.

Sem isto, quem descobrir o endereço lê todas as conversas, todas as memórias,
muda a personalidade e gasta a sua cota de API. Não existe "ninguém vai achar":
a internet é varrida por robôs o tempo todo.

O modelo aqui é o mais simples que resolve: uma senha só, um cookie assinado.
Não é sistema de contas — é a fechadura de uma porta que só você atravessa.
Para 1-2 pessoas isso basta e não tem o que dar errado.

Não guardamos a senha em lugar nenhum além do .env, e a comparação é feita em
tempo constante para não vazar informação pelo tempo de resposta.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from . import config

COOKIE = "livia_sessao"
DURACAO = 30 * 24 * 3600  # 30 dias; some quando você não usa por um mês


def _segredo() -> bytes:
    """Chave usada para assinar o cookie.

    Derivada da senha, então trocar a senha invalida todas as sessões abertas —
    que é exatamente o que se espera de uma troca de senha.
    """
    base = (config.PASSWORD or "sem-senha") + "|livia|assinatura-de-sessao"
    return hashlib.sha256(base.encode("utf-8")).digest()


def criar_token() -> str:
    """Cookie de sessão: validade + assinatura."""
    expira = int(time.time()) + DURACAO
    assinatura = hmac.new(_segredo(), str(expira).encode(), hashlib.sha256).hexdigest()
    return f"{expira}.{assinatura}"


def token_valido(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expira_txt, _, assinatura = token.partition(".")
    try:
        expira = int(expira_txt)
    except ValueError:
        return False
    if expira < time.time():
        return False
    esperada = hmac.new(_segredo(), expira_txt.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(assinatura, esperada)


def senha_confere(enviada: str) -> bool:
    """Comparação em tempo constante — não entrega a senha pelo cronômetro."""
    if not config.PASSWORD:
        return False
    return hmac.compare_digest(enviada.encode("utf-8"), config.PASSWORD.encode("utf-8"))


def protegido() -> bool:
    """Há senha configurada?"""
    return bool(config.PASSWORD)


def sugerir_senha() -> str:
    """Uma senha decente, para quem não quiser inventar."""
    return secrets.token_urlsafe(18)
