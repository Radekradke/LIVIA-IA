"""Saúde dos provedores: quem está de pé, quem está de castigo.

O problema que isto resolve: uma chave errada da Groq não pode derrubar a
conversa quando o Gemini está funcionando. Mas também não pode ficar escondida
— senão o André descobre semanas depois que vinha pagando latência de reserva
sem motivo.

A resposta é lembrar. Cada falha marca o provedor, e a marca dura conforme o
tipo do problema:

    cota / servidor / timeout   passageiro  → alguns segundos de descanso
    chave recusada              nosso erro  → fora até reconfigurar
    modelo inexistente          nosso erro  → fora até reconfigurar

A distinção é a mesma de sempre: o que passa sozinho ganha nova chance
automática; o que exige uma decisão humana fica registrado até alguém agir.

O estado vive só na memória. Reiniciar limpa tudo, e isso é proposital —
depois de mexer no .env, o certo é dar chance nova a todo mundo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Quanto tempo cada tipo de falha tira o provedor de circulação.
DESCANSO = {
    "cota": 45.0,
    "servidor": 30.0,
    "timeout": 20.0,
}
PERMANENTE = ("chave", "modelo")

# Timeout isolado é azar; três seguidos é sintoma.
TIMEOUTS_ATE_CASTIGO = 3
CASTIGO_POR_TIMEOUT = 120.0


@dataclass
class Estado:
    configurado: bool = False
    ate: float = 0.0                 # descansando até este instante
    motivo: str = ""                 # tipo da última falha
    ultimo_erro: str = ""            # texto curto, sem segredo
    timeouts_seguidos: int = 0
    quebrado: bool = False           # exige intervenção humana

    def disponivel(self, agora: float | None = None) -> bool:
        if self.quebrado:
            return False
        return (agora or time.time()) >= self.ate


_estados: dict[str, Estado] = {}


def _de(provedor: str) -> Estado:
    return _estados.setdefault(provedor, Estado())


def registrar_falha(provedor: str, tipo: str, detalhe: str = "") -> None:
    """Anota uma falha. `tipo`: cota, servidor, timeout, chave, modelo, rede."""
    e = _de(provedor)
    e.motivo = tipo
    # O detalhe é truncado e nunca inclui cabeçalho: só a mensagem que a API
    # devolveu. Chave nenhuma passa por aqui.
    e.ultimo_erro = (detalhe or tipo)[:200]

    if tipo in PERMANENTE:
        e.quebrado = True
        e.ate = float("inf")
        return

    if tipo == "timeout":
        e.timeouts_seguidos += 1
        if e.timeouts_seguidos >= TIMEOUTS_ATE_CASTIGO:
            e.ate = time.time() + CASTIGO_POR_TIMEOUT
            return
    else:
        e.timeouts_seguidos = 0

    e.ate = time.time() + DESCANSO.get(tipo, 20.0)


def registrar_sucesso(provedor: str) -> None:
    """Funcionou. Limpa o histórico — menos a marca de quebrado.

    Provedor quebrado não se cura sozinho: se a chave estava errada e passou
    a funcionar, foi porque alguém mexeu no .env, e aí o servidor reinicia.
    """
    e = _de(provedor)
    if e.quebrado:
        return
    e.ate = 0.0
    e.motivo = ""
    e.ultimo_erro = ""
    e.timeouts_seguidos = 0


def disponivel(provedor: str) -> bool:
    return _de(provedor).disponivel()


def quebrados() -> list[str]:
    """Provedores que precisam de intervenção — para avisar o André."""
    return [n for n, e in _estados.items() if e.quebrado]


def descansando() -> dict[str, float]:
    """Quem está de castigo e por quantos segundos ainda."""
    agora = time.time()
    return {
        n: round(e.ate - agora, 1)
        for n, e in _estados.items()
        if not e.quebrado and e.ate > agora
    }


def filtrar(provedores: list[str]) -> tuple[list[str], list[str]]:
    """Separa quem pode tentar de quem está fora. Preserva a ordem."""
    prontos = [p for p in provedores if disponivel(p)]
    fora = [p for p in provedores if not disponivel(p)]
    return prontos, fora


def limpar() -> None:
    """Zera tudo. Usado pelos testes e ao recarregar configuração."""
    _estados.clear()


def diagnostico() -> dict[str, dict[str, object]]:
    """Retrato seguro para a interface. NUNCA inclui chave, senha ou token."""
    from . import config, router

    agora = time.time()
    modelos = {
        "gemini": config.MODEL,
        "groq": config.GROQ_MODEL,
        "openrouter": config.OPENROUTER_MODEL,
    }
    chaves = {
        "gemini": config.GEMINI_API_KEY,
        "groq": config.GROQ_API_KEY,
        "openrouter": config.OPENROUTER_API_KEY,
    }

    saida: dict[str, dict[str, object]] = {}
    for nome, spec in router.CATALOGO.items():
        e = _de(nome)
        configurado = bool(chaves.get(nome))
        saida[nome] = {
            "configurado": configurado,
            "disponivel": configurado and e.disponivel(agora),
            "modelo": modelos.get(nome, ""),
            "capacidades": sorted(spec.capabilities),
            "quebrado": e.quebrado,
            "descanso_segundos": max(0, round(e.ate - agora, 1)) if e.ate != float("inf") else None,
            "ultimo_erro": e.ultimo_erro,
        }
    return saida
