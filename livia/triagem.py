"""Preparar a pergunta antes de buscar — numa chamada só.

O PROBLEMA
----------
A busca recebia a mensagem crua. Isso funciona na primeira pergunta e desanda
na segunda:

    André: "o que o capítulo 4 diz sobre cache?"   → vetor bom
    André: "e o próximo?"                          → vetor de "e o próximo?"

O segundo embedding não carrega informação nenhuma. A busca devolve ruído ou
nada, e o modelo responde sem contexto — provavelmente inventando, porque o
histórico ainda tem o assunto e ele preenche a lacuna.

A saída é reescrever a pergunta para uma versão que se sustenta sozinha,
usando o histórico:

    "e o próximo?"  →  "o que o capítulo 5 diz sobre cache?"

A REESCRITA É PARA BUSCAR, NUNCA PARA RESPONDER
-----------------------------------------------
O modelo continua lendo a pergunta ORIGINAL. Trocar o que o André escreveu
pelo que a máquina achou que ele quis dizer é a receita para responder outra
coisa com confiança — e ele nunca saberia por quê. A reescrita só alimenta a
recuperação, onde errar custa um trecho pior, não uma resposta errada.

POR QUE ISTO E A DECISÃO DE BUSCAR NA WEB MORAM JUNTAS
------------------------------------------------------
São duas perguntas sobre a mesma frase, respondidas pelo mesmo modelo rápido,
com o mesmo custo de ida e volta. Separadas, seriam duas chamadas em toda
mensagem. Juntas, é uma.

E ELA SÓ ACONTECE QUANDO PODE AJUDAR
------------------------------------
Uma pergunta que já se sustenta sozinha não ganha nada com reescrita, e a
maioria se sustenta. `precisa_reescrever` é uma heurística local — sem IA,
como o resto dos roteadores daqui — que decide se vale a chamada. Quando não
vale e a web está desligada, nenhuma chamada é feita: a triagem custa zero.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import brain, config

log = logging.getLogger("livia.triagem")

# Sinais de que a pergunta depende do que veio antes. Deliberadamente
# generosos: quando a heurística dispara à toa, o custo é uma chamada ao
# modelo rápido; quando ela deixa passar, o custo é uma busca cega.
_DEPENDENTE = re.compile(
    r"("
    # Começa emendando na frase anterior.
    r"^\s*(?:e|ok|entao|então|mas|ai|aí|certo|beleza)\b|"
    # Pronome ou advérbio apontando para algo não nomeado aqui.
    r"\b(?:isso|isto|aquilo|esse|essa|este|esta|aquele|aquela|"
    r"dele|dela|deles|delas|nele|nela|disso|nisso|daquilo)\b|"
    r"\b(?:o|a)\s+(?:mesm[oa]|outr[oa]|anterior|seguinte|primeir[oa]|"
    r"segund[oa]|terceir[oa]|ultim[oa]|últim[oa]|próxim[oa]|proxim[oa])\b|"
    # Continuações típicas sem sujeito.
    r"\b(?:e\s+(?:o|a|os|as|no|na|sobre|quanto|se)|"
    r"por\s*qu[eê]\s*\??$|como\s+assim|e\s+da[ií]|tem\s+mais|mais\s+algum)\b|"
    # Pedido sobre a resposta anterior.
    r"\b(?:resum[ae]|explica\s+melhor|detalha|continua|traduz)\b\s*(?:isso|$)"
    r")",
    re.IGNORECASE,
)

# Abaixo disto a frase quase nunca se sustenta sozinha — e vetorizar três
# palavras genéricas devolve qualquer coisa.
_CURTA = 5


@dataclass
class Triagem:
    """O que a preparação descobriu sobre a mensagem."""

    pergunta_busca: str          # o que mandar para a recuperação
    consulta_web: str | None = None
    reescrita: bool = False      # a busca usou outra frase que não a do André

    @property
    def mudou(self) -> bool:
        return self.reescrita


def precisa_reescrever(pergunta: str, historico: list[dict[str, str]] | None) -> bool:
    """Vale gastar uma chamada para desatar esta pergunta?

    Sem histórico não há o que resolver: uma primeira mensagem é o que é.
    """
    if not historico:
        return False
    texto = (pergunta or "").strip()
    if not texto or texto.startswith("/"):
        return False
    if len(texto.split()) <= _CURTA:
        return True
    return bool(_DEPENDENTE.search(texto))


_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "precisa_web": {
            "type": "BOOLEAN",
            "description": "true se responder bem exigir informação atual da web",
        },
        "consulta": {
            "type": "STRING",
            "description": "O que digitar num buscador. Vazio se precisa_web=false.",
        },
        "pergunta_completa": {
            "type": "STRING",
            "description": (
                "A pergunta reescrita de forma que se entenda sozinha, sem o "
                "histórico. Vazio se ela já se entende sozinha."
            ),
        },
    },
    "required": ["precisa_web"],
}

_SISTEMA = """\
Você prepara a última mensagem de uma conversa para ser BUSCADA. Duas coisas:

1) A pergunta se entende SOZINHA?

Se ela depende do que veio antes ("e o próximo?", "explica isso melhor",
"quanto custa?"), reescreva trocando os pronomes e os subentendidos pelo que
eles significam, olhando o histórico. Se ela já se entende sozinha, devolva
`pergunta_completa` vazio.

Regras da reescrita:
- Só use o que está no histórico. Não acrescente detalhe que ninguém disse.
- Mantenha a língua original e o sentido exato. Você está desatando uma
  referência, não melhorando a pergunta.
- Uma frase. Sem preâmbulo, sem explicação.

2) Precisa de busca na web?

PRECISA:
- Qualquer coisa de hoje ou recente: preço, cotação, clima, notícia, placar,
  lançamento, versão atual de alguma coisa.
- Fatos sobre pessoas, empresas ou produtos que mudam com o tempo.
- Quando a pessoa pede explicitamente para pesquisar ou procurar.

NÃO PRECISA:
- Conhecimento estável: matemática, como programar, história, gramática,
  conceitos, "como funciona X".
- Pedidos sobre o texto da própria conversa (resumir, reescrever, traduzir).
- Bate-papo, opinião, tarefa criativa.
- Quando a pessoa já colou a URL na mensagem.

Na dúvida, `precisa_web` false: buscar à toa deixa a resposta mais lenta e pior.
A consulta deve ser o que uma pessoa digitaria num buscador — curta e sem
firula, na língua da pergunta.
"""

# Quantas mensagens do histórico o modelo rápido vê. Poucas de propósito: o
# que desata "e o próximo?" está nas últimas trocas, e mandar a conversa
# inteira encareceria a chamada para piorar o sinal.
TURNOS_DE_CONTEXTO = 6


def _conversa(historico: list[dict[str, str]] | None, pergunta: str) -> str:
    linhas: list[str] = []
    for m in (historico or [])[-TURNOS_DE_CONTEXTO:]:
        papel = "André" if m.get("role") == "user" else "Livia"
        linhas.append(f"{papel}: {str(m.get('content') or '')[:600]}")
    linhas.append(f"André (ÚLTIMA): {pergunta[:2000]}")
    return "\n".join(linhas)


async def avaliar(
    pergunta: str,
    historico: list[dict[str, str]] | None = None,
    *,
    tem_link: bool = False,
) -> Triagem:
    """Uma chamada ao modelo rápido, ou nenhuma.

    Nunca levanta: falhar aqui responde com a pergunta como ela veio, que é o
    comportamento de antes desta função existir.
    """
    original = (pergunta or "").strip()
    quer_reescrita = precisa_reescrever(original, historico)
    quer_web = config.WEB_ENABLED and config.WEB_AUTO and not tem_link

    if not (quer_reescrita or quer_web):
        return Triagem(pergunta_busca=original)

    try:
        resposta = await brain.structured(
            _SISTEMA, _conversa(historico, original), _SCHEMA
        )
    except Exception as exc:
        log.debug("[triagem] falhou: %s", exc)
        return Triagem(pergunta_busca=original)

    if not isinstance(resposta, dict):
        return Triagem(pergunta_busca=original)

    consulta = None
    if quer_web and resposta.get("precisa_web"):
        consulta = str(resposta.get("consulta") or "").strip() or original[:200]

    busca, reescrita = original, False
    if quer_reescrita:
        proposta = str(resposta.get("pergunta_completa") or "").strip()
        if _aceitavel(proposta, original):
            busca, reescrita = proposta, True
            log.debug("[triagem] %r → %r", original[:50], proposta[:50])

    return Triagem(pergunta_busca=busca, consulta_web=consulta, reescrita=reescrita)


def _aceitavel(proposta: str, original: str) -> bool:
    """A reescrita é boa o bastante para substituir a pergunta na busca?

    Um modelo pequeno erra de formas conhecidas: devolve a pergunta igual,
    devolve um parágrafo explicando o que fez, ou devolve algo curto demais
    para ser mais informativo que o original. Nenhum desses casos melhora a
    busca, e o original nunca é uma escolha ruim — é o que o André escreveu.
    """
    if not proposta or proposta == original:
        return False
    # Um parágrafo não é uma pergunta reescrita: é o modelo conversando.
    if len(proposta) > max(300, len(original) * 4):
        return False
    # Reescrita precisa DIZER mais que o original; menor que ele é perda.
    return len(proposta) >= len(original)
