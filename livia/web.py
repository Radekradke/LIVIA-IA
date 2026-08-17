"""Acesso à web: buscar e ler páginas.

São dois mecanismos diferentes, com custos diferentes:

  LER UMA URL  → ferramenta nativa do Gemini (`url_context`). Grátis. É o
                 próprio modelo que baixa e lê a página; não passa por aqui.

  BUSCAR       → DuckDuckGo, por este módulo. A busca nativa do Google
                 (`google_search`) existe na API mas devolve 429 em conta
                 gratuita — é recurso pago. O DDG faz o mesmo papel de graça:
                 devolve títulos, links e resumos, que entram no prompt como
                 contexto. Se o modelo quiser o texto completo de algum
                 resultado, ele usa o `url_context` naquele link.

Ressalva honesta sobre o DDG: não é API oficial, é raspagem da página de
resultados. Funciona bem, mas pode quebrar quando o DuckDuckGo mudar o HTML.
Quando quebrar, a busca falha sozinha e a conversa continua sem ela — nunca
derruba a resposta.
"""

from __future__ import annotations

import asyncio
import re

from . import brain, config

_URL = re.compile(r"https?://[^\s<>\"'()]+", re.IGNORECASE)


def urls_em(texto: str) -> list[str]:
    """URLs escritas na mensagem. Se houver alguma, o modelo pode lê-las."""
    return _URL.findall(texto or "")


# --------------------------------------------------------------------------
# Busca
# --------------------------------------------------------------------------


def _buscar_sincrono(consulta: str, n: int) -> list[dict[str, str]]:
    try:
        from ddgs import DDGS
    except ImportError:
        return []

    try:
        with DDGS() as ddgs:
            brutos = list(ddgs.text(consulta, region="br-pt", max_results=n))
    except Exception:
        # Rede fora, bloqueio, mudança de HTML... a busca é um bônus.
        return []

    saida: list[dict[str, str]] = []
    for r in brutos:
        titulo = (r.get("title") or "").strip()
        link = (r.get("href") or "").strip()
        resumo = " ".join((r.get("body") or "").split())
        if titulo and link:
            saida.append({"titulo": titulo, "url": link, "resumo": resumo[:400]})
    return saida


async def buscar(consulta: str, n: int = 5) -> list[dict[str, str]]:
    """Busca no DuckDuckGo sem travar o servidor (a lib é síncrona)."""
    return await asyncio.to_thread(_buscar_sincrono, consulta, n)


def formatar(consulta: str, resultados: list[dict[str, str]]) -> str:
    """Vira o bloco de contexto que entra no prompt."""
    if not resultados:
        return ""
    linhas = [
        f"Resultados de uma busca na web por \"{consulta}\", "
        "feita agora para responder à pergunta abaixo.",
        "Use-os como fonte. Cite de onde tirou quando o dado for específico "
        "(preço, data, número, notícia). Se quiser o texto completo de algum, "
        "leia a URL — você consegue abrir links.",
        "",
    ]
    for i, r in enumerate(resultados, 1):
        linhas.append(f"[{i}] {r['titulo']}")
        linhas.append(f"    {r['url']}")
        if r["resumo"]:
            linhas.append(f"    {r['resumo']}")
        linhas.append("")
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Decidir se vale buscar
# --------------------------------------------------------------------------

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "precisa": {
            "type": "BOOLEAN",
            "description": "true se responder bem exigir informação atual da web",
        },
        "consulta": {
            "type": "STRING",
            "description": "O que digitar num buscador. Vazio se precisa=false.",
        },
    },
    "required": ["precisa"],
}

_SISTEMA = """\
Você decide se uma pergunta precisa de busca na web para ser bem respondida.

PRECISA de busca:
- Qualquer coisa de hoje ou recente: preço, cotação, clima, notícia, placar,
  lançamento, versão atual de alguma coisa.
- Fatos sobre pessoas, empresas ou produtos que mudam com o tempo.
- Quando a pessoa pede explicitamente para pesquisar ou procurar.

NÃO PRECISA:
- Conhecimento estável: matemática, como programar, história, gramática,
  conceitos, "como funciona X".
- Pedidos sobre o texto da própria conversa (resumir, reescrever, traduzir).
- Bate-papo, opinião, tarefa criativa.
- Quando a pessoa já colou a URL na mensagem — o modelo lê o link direto,
  sem busca.

Na dúvida, responda false: buscar à toa deixa a resposta mais lenta e pior.

A consulta deve ser o que uma pessoa digitaria num buscador — curta e sem
firula. Escreva na mesma língua da pergunta.
"""


async def precisa_buscar(pergunta: str) -> str | None:
    """Devolve a consulta a pesquisar, ou None se não vale a pena.

    Custa uma chamada extra ao modelo rápido. Em conta gratuita isso dobra o
    consumo de cota, então dá para desligar em LIVIA_WEB_AUTO=0 e usar só
    /buscar e links colados.
    """
    if not (config.WEB_ENABLED and config.WEB_AUTO):
        return None

    resposta = await brain.structured(_SISTEMA, pergunta.strip()[:2000], _SCHEMA)
    if not isinstance(resposta, dict) or not resposta.get("precisa"):
        return None

    consulta = str(resposta.get("consulta") or "").strip()
    return consulta or pergunta.strip()[:200]
