"""O sono da memória.

Enquanto o André dorme, a Livia faz a revisão que a `manutencao` já sabia
fazer — memória repetida, memória esquecida, lição que se repete — e deixa o
relatório esperando. Quem aplica é ele, com um toque, de manhã.

Por que não aplicar sozinha, se a moda dos "sleep-time agents" é essa: a
memória dela é a memória dele. Um erro de faxina às 3h da manhã que ninguém
viu é exatamente o tipo de erro que só se descobre meses depois, quando a
Livia "esquece" algo que importava. Analisar à noite e decidir de dia dá o
conforto do automático sem tirar o André da decisão.

O que é novo em relação à manutenção por comando: ao aplicar, as memórias
repetidas são FUNDIDAS (um modelo escreve uma versão com o detalhe das duas),
em vez de a mais velha simplesmente ser superada.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from . import config, memoria

log = logging.getLogger("livia.sono")


def _arquivo():
    return config.DATA_DIR / "sono.json"


def _fuso() -> timezone:
    return timezone(timedelta(hours=config.FUSO_HORAS))


def _ler() -> dict[str, object]:
    try:
        return json.loads(_arquivo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar(dados: dict[str, object]) -> None:
    _arquivo().write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def _contagem(rel: dict[str, object]) -> dict[str, int]:
    return {
        "repetidas": len(rel.get("duplicatas") or []),
        "esquecidas": len(rel.get("obsoletas") or []),
        # A consolidação de lições devolve propostas separadas por tipo.
        "licoes": sum(len(v) for k, v in (rel.get("licoes") or {}).items()
                      if k in ("heuristicas", "anti_patterns") and isinstance(v, list))
                  if isinstance(rel.get("licoes"), dict) else 0,
    }


async def revisar() -> dict[str, object]:
    """A rodada da madrugada: só olha, não mexe."""
    rel = await memoria.manutencao(aplicar=False)
    dados = {
        "quando": datetime.now(_fuso()).isoformat(timespec="minutes"),
        "relatorio": rel, "contagem": _contagem(rel), "situacao": "pendente",
    }
    if not any(dados["contagem"].values()):
        dados["situacao"] = "nada"
    _gravar(dados)
    log.info("[sono] revisão: %s", dados["contagem"])
    return dados


def pendente() -> dict[str, object] | None:
    """O relatório que está esperando o André, se houver algo pra decidir."""
    dados = _ler()
    return dados if dados.get("situacao") == "pendente" else None


async def aplicar() -> dict[str, object]:
    """O André disse sim. Refaz a análise na hora (a memória pode ter mudado
    desde a madrugada) e aplica, fundindo as repetidas."""
    rel = await memoria.manutencao(aplicar=True, fundir_textos=True)
    dados = _ler() or {}
    dados.update({"situacao": "aplicado", "aplicado_em": datetime.now(_fuso()).isoformat(timespec="minutes"),
                  "resultado": {"fundidas": rel.get("fundidas", 0), "arquivadas": rel.get("arquivadas", 0),
                                "repetidas": len(rel.get("duplicatas") or [])}})
    _gravar(dados)
    return dados


def dispensar() -> None:
    dados = _ler()
    if dados:
        dados["situacao"] = "dispensado"
        _gravar(dados)


def proxima_rodada(agora: datetime | None = None) -> datetime:
    agora = agora or datetime.now(_fuso())
    try:
        h, m = (int(x) for x in config.SONO_HORA.split(":", 1))
    except ValueError:
        h, m = 3, 30
    alvo = agora.replace(hour=h, minute=m, second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


async def ciclo() -> None:
    """Dorme até a hora marcada, revisa, repete. Erro numa noite não mata as próximas."""
    while True:
        espera = (proxima_rodada() - datetime.now(_fuso())).total_seconds()
        await asyncio.sleep(max(60.0, espera))
        try:
            await revisar()
        except Exception:
            log.exception("[sono] a revisão da madrugada falhou")


def texto(dados: dict[str, object]) -> str:
    """O relatório em português, pro chat."""
    c = dados.get("contagem") or {}
    rel = dados.get("relatorio") or {}
    if not any(c.values()):
        return "🌙 Revisei a memória: tá tudo em ordem, nada repetido nem esquecido."
    linhas = [f"🌙 **Revisão da memória** ({dados.get('quando', '')[:16].replace('T', ' ')})"]
    if c.get("repetidas"):
        linhas.append(f"\n**{c['repetidas']} par(es) quase iguais** — ao aplicar, eu junto o texto dos dois:")
        linhas += [f"- `{d['parecida']}` ≈ `{d['mantida']}` ({d['semelhanca']:.0%})" for d in rel.get("duplicatas", [])[:10]]
    if c.get("esquecidas"):
        linhas.append(f"\n**{c['esquecidas']} esquecida(s)** — sem uso há meses, vão pro arquivo (não somem):")
        linhas += [f"- `{o['nome']}` ({o['dias_sem_uso']} dias)" for o in rel.get("obsoletas", [])[:10]]
    if c.get("licoes"):
        linhas.append(f"\n**{c['licoes']} lição(ões)** que se repetiram o suficiente pra virar regra.")
    linhas.append("\nPra aplicar: `/sono aplicar`. Pra deixar como está: `/sono dispensar`.")
    return "\n".join(linhas)
