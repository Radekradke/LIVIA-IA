"""Medir se a busca acha a coisa certa — e não só se o código roda.

O PROBLEMA QUE ISTO RESOLVE
---------------------------
Tinha 530 testes e nenhum media QUALIDADE de recuperação. Eles provam que a
busca não quebra; nenhum prova que ela acha. A diferença aparece na hora de
mexer nos números que governam o sistema:

    biblioteca.LIMIAR = 0.55        escolhido no olho
    biblioteca.TAMANHO_TRECHO       escolhido no olho
    memoria.PESOS                   escolhidos no olho
    memoria.MEIA_VIDA_DIAS = 90     escolhido no olho

Nenhum deles é errado. O problema é que não havia como descobrir, e portanto
nenhum ia melhorar nunca. Mexer em `PESOS` era um ato de fé nos dois sentidos:
nem quem mexe nem quem revisa sabe dizer se ficou melhor.

A MÉTRICA QUE IMPORTA PRIMEIRO
------------------------------
`acerto@k`: em que fração das perguntas o trecho certo apareceu entre os k
primeiros. Se ele não aparece, nenhum modelo salva a resposta — não há o que
o LLM faça com contexto que não chegou. É barato de rodar, não precisa de
resposta de referência, e por isso dá para rodar a cada mudança.

O MRR vem junto porque `acerto@k` sozinho esconde uma coisa: achar em primeiro
e achar em quinto contam igual, e não são a mesma qualidade de busca.

O QUE ESTE MÓDULO NÃO MEDE
--------------------------
Não mede se a RESPOSTA ficou boa. Isso dependeria de julgar texto, o que exige
outro modelo julgando, o que traz variação e custo — e resolveria depois um
problema que quase sempre está aqui: o trecho certo não chegou.

Também não mede a qualidade do modelo de embedding quando roda com vetores
falsos, no pytest. Ali ele mede o PIPELINE — limiar, fusão, deduplicação,
orçamento. Para medir o modelo de verdade é preciso rodar na linha de comando,
com o Ollama de pé e os documentos reais:

    python -m livia.avaliacao
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("livia.avaliacao")

CAMINHO_PADRAO = Path("data/avaliacao/conjunto.json")
K_PADRAO = 5

# Um modelo para copiar. Fica aqui, e não num arquivo de exemplo no
# repositório, porque `data/` é ignorado pelo git — um arquivo de exemplo lá
# não sobreviveria ao clone, e num diretório novo ninguém acharia.
MODELO = """\
{
  "piso": {"acerto": 0.70, "mrr": 0.50},
  "casos": [
    {
      "pergunta": "como o projeto trata documento escaneado?",
      "documento": "manual-livia",
      "contem": ["parser avançado"],
      "nota": "resposta literal, num lugar só — o vetor deveria bastar"
    },
    {
      "pergunta": "qual banco aparece ligado ao projeto da Alice?",
      "contem": ["PostgreSQL"],
      "nota": "cruza dois documentos; sem grafo isto tende a falhar"
    }
  ]
}
"""


# --------------------------------------------------------------------------
# O conjunto dourado
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Caso:
    """Uma pergunta e como reconhecer que a busca acertou.

    Os três critérios se somam (todos precisam bater). Um caso sem critério
    nenhum é rejeitado no carregamento: ele passaria sempre, e uma métrica que
    só sobe é pior que métrica nenhuma.
    """

    pergunta: str
    documento: str = ""            # slug do documento onde está a resposta
    pagina: int | None = None      # página exata, quando importa
    contem: tuple[str, ...] = ()   # trechos de texto que o achado deve conter
    nota: str = ""                 # por que esta pergunta está no conjunto

    def combina(self, achado) -> bool:
        if self.documento and (achado.document_id or "") != self.documento:
            return False
        if self.pagina is not None and achado.page != self.pagina:
            return False
        if self.contem:
            texto = _simplificar(achado.text)
            if not all(_simplificar(c) in texto for c in self.contem):
                return False
        return True


def _simplificar(texto: str) -> str:
    """Minúsculas e sem acento — para "memória" casar com "memoria".

    Comparação de substring exata transformaria o conjunto dourado num
    exercício de digitação, e o que ele mede é a busca, não o acento.
    """
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in sem_acento if not unicodedata.combining(c)).lower()


def carregar(caminho: Path | str = CAMINHO_PADRAO) -> tuple[list[Caso], dict]:
    """Devolve (casos, piso). Piso é o orçamento de regressão, se houver."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    return montar(dados.get("casos") or []), dict(dados.get("piso") or {})


def montar(bruto: list[dict]) -> list[Caso]:
    casos: list[Caso] = []
    for i, item in enumerate(bruto):
        pergunta = str(item.get("pergunta") or "").strip()
        if not pergunta:
            raise ValueError(f"caso {i}: sem pergunta")

        contem = item.get("contem") or []
        if isinstance(contem, str):
            contem = [contem]

        caso = Caso(
            pergunta=pergunta,
            documento=str(item.get("documento") or "").strip(),
            pagina=int(item["pagina"]) if item.get("pagina") is not None else None,
            contem=tuple(str(c) for c in contem),
            nota=str(item.get("nota") or ""),
        )
        if not (caso.documento or caso.pagina is not None or caso.contem):
            raise ValueError(
                f"caso {i} ({pergunta[:40]}…): sem critério de acerto. "
                "Um caso que aceita qualquer resposta sobe a métrica sem "
                "melhorar nada."
            )
        casos.append(caso)
    return casos


# --------------------------------------------------------------------------
# A medição
# --------------------------------------------------------------------------


@dataclass
class Linha:
    """O resultado de uma pergunta, com o suficiente para investigar."""

    caso: Caso
    posicao: int | None            # 1 = achou em primeiro; None = não achou
    devolvidos: int
    rotulos: list[str] = field(default_factory=list)
    modo: str = ""

    @property
    def acertou(self) -> bool:
        return self.posicao is not None


@dataclass
class Resultado:
    k: int
    linhas: list[Linha]

    @property
    def total(self) -> int:
        return len(self.linhas)

    @property
    def acertos(self) -> int:
        return sum(1 for l in self.linhas if l.acertou)

    @property
    def acerto(self) -> float:
        """Fração das perguntas cujo trecho certo apareceu entre os k."""
        return self.acertos / self.total if self.total else 0.0

    @property
    def mrr(self) -> float:
        """Média de 1/posição. Achar em primeiro vale 1; em quinto, 0,2.

        Existe porque `acerto@k` trata primeiro lugar e último lugar como a
        mesma coisa, e uma mudança que só reordena não aparece nele.
        """
        if not self.linhas:
            return 0.0
        return sum(1 / l.posicao for l in self.linhas if l.posicao) / self.total

    @property
    def vazios(self) -> int:
        """Perguntas que não devolveram nada. Falha diferente de trazer errado."""
        return sum(1 for l in self.linhas if l.devolvidos == 0)

    def resumo(self) -> dict[str, object]:
        return {
            "k": self.k,
            "total": self.total,
            "acerto": round(self.acerto, 4),
            "mrr": round(self.mrr, 4),
            "vazios": self.vazios,
        }

    def falhas(self) -> list[Linha]:
        return [l for l in self.linhas if not l.acertou]


async def avaliar(casos: list[Caso], k: int = K_PADRAO) -> Resultado:
    """Roda o conjunto contra o pipeline REAL de recuperação.

    Passa por `knowledge_router.buscar`, e não direto pela biblioteca, de
    propósito: é ele que o chat usa. Medir um caminho que ninguém percorre
    daria um número bonito e inútil.
    """
    from . import knowledge_router

    linhas: list[Linha] = []
    for caso in casos:
        try:
            achados, procedencia = await knowledge_router.buscar(caso.pergunta)
        except Exception as exc:
            log.warning("[avaliacao] %r falhou: %s", caso.pergunta[:40], exc)
            achados, procedencia = [], {}

        achados = achados[:k]
        posicao = next(
            (i for i, a in enumerate(achados, 1) if caso.combina(a)), None
        )
        linhas.append(
            Linha(
                caso=caso,
                posicao=posicao,
                devolvidos=len(achados),
                rotulos=[a.rotulo() for a in achados],
                modo=str(procedencia.get("modo") or ""),
            )
        )
    return Resultado(k=k, linhas=linhas)


# --------------------------------------------------------------------------
# Comparação com a última medição
# --------------------------------------------------------------------------


def comparar(agora: dict[str, object], antes: dict[str, object]) -> list[str]:
    """As diferenças que valem ser ditas, já em português.

    Só métrica que se move. Repetir "acerto: igual" em toda linha treinaria
    qualquer um a não ler a saída.
    """
    frases: list[str] = []
    for chave, rotulo, casas in (("acerto", "acerto@k", 3), ("mrr", "MRR", 3)):
        a, b = float(agora.get(chave, 0)), float(antes.get(chave, 0))
        if abs(a - b) < 10 ** -casas:
            continue
        seta = "subiu" if a > b else "CAIU"
        frases.append(f"{rotulo} {seta}: {b:.{casas}f} → {a:.{casas}f}")

    va, vb = int(agora.get("vazios", 0)), int(antes.get("vazios", 0))
    if va != vb:
        frases.append(f"perguntas sem resposta: {vb} → {va}")
    return frases


def piso_quebrado(resumo: dict[str, object], piso: dict[str, object]) -> list[str]:
    """Métricas abaixo do orçamento de regressão declarado no conjunto."""
    problemas: list[str] = []
    for chave in ("acerto", "mrr"):
        if chave not in piso:
            continue
        atual, minimo = float(resumo.get(chave, 0)), float(piso[chave])
        if atual < minimo:
            problemas.append(f"{chave} {atual:.3f} abaixo do piso {minimo:.3f}")
    return problemas


# --------------------------------------------------------------------------
# Linha de comando
# --------------------------------------------------------------------------


def formatar(resultado: Resultado) -> str:
    r = resultado.resumo()
    linhas = [
        f"{r['total']} perguntas, k={r['k']}",
        "",
        f"  acerto@{r['k']}  {r['acerto']:.1%}   ({resultado.acertos}/{r['total']})",
        f"  MRR        {r['mrr']:.3f}",
        f"  sem nada   {r['vazios']}",
    ]

    falhas = resultado.falhas()
    if falhas:
        linhas += ["", f"Não achou ({len(falhas)}):"]
        for l in falhas:
            alvo = l.caso.documento or (l.caso.contem[0] if l.caso.contem else "?")
            veio = ", ".join(l.rotulos[:3]) or "nada"
            linhas.append(f"  · {l.caso.pergunta[:58]}")
            linhas.append(f"      queria: {alvo}   veio: {veio}")
    return "\n".join(linhas)


async def _rodar(args) -> int:
    from . import biblioteca, embeddings

    caminho = Path(args.conjunto)
    if not caminho.exists():
        if args.criar:
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_text(MODELO, encoding="utf-8")
            print(f"Criei {caminho} com dois exemplos. Troque pelas suas "
                  "perguntas — 30 a 50 já dão um número em que dá para confiar.")
            return 0
        print(f"Conjunto não encontrado: {caminho}\n")
        print("Rode com --criar para gerar um modelo, ou escreva o arquivo:\n")
        print(MODELO)
        return 2

    casos, piso = carregar(caminho)

    # Sem gerador de vetores, o número que sairia mediria a ausência dele.
    if not embeddings.disponivel():
        print("Sem gerador de vetores (Ollama fora e sem chave do Gemini).")
        print("A medição não teria sentido — ligue um provedor e rode de novo.")
        return 2
    if biblioteca.vazia():
        print("A biblioteca está vazia. Não há onde procurar.")
        return 2

    # A CLI roda fora do servidor, então o alinhamento que o arranque faz não
    # aconteceu. Medir com o índice de palavras vazio culparia a busca lexical
    # por um número que é culpa da medição.
    from . import lexical

    try:
        lexical.sincronizar()
    except Exception as exc:
        print(f"AVISO: índice de palavras não pôde ser alinhado ({exc}).")

    pendentes = biblioteca.incompativeis()
    if pendentes:
        print(f"AVISO: {len(pendentes)} documento(s) com vetores de outro "
              "gerador ficam de fora da busca e vão puxar o número para baixo.")
        print("       Reindexe antes de tirar conclusão.\n")

    resultado = await avaliar(casos, args.k)
    print(formatar(resultado))

    resumo = resultado.resumo()
    base = Path(args.base)
    if base.exists():
        try:
            antes = json.loads(base.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            antes = {}
        if antes:
            mudancas = comparar(resumo, antes)
            print("\nContra a última medição:")
            print("\n".join(f"  {m}" for m in mudancas) if mudancas
                  else "  nada se moveu.")

    if args.salvar:
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(json.dumps(resumo, indent=2), encoding="utf-8")
        print(f"\nMedição guardada em {base}")

    quebrados = piso_quebrado(resumo, piso)
    if quebrados:
        print("\nABAIXO DO PISO:")
        print("\n".join(f"  {q}" for q in quebrados))
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m livia.avaliacao",
        description="Mede se a busca acha o trecho certo.",
    )
    p.add_argument("conjunto", nargs="?", default=str(CAMINHO_PADRAO))
    p.add_argument("-k", type=int, default=K_PADRAO,
                   help="quantos resultados contam como 'achou'")
    p.add_argument("--base", default="data/avaliacao/ultima.json",
                   help="medição anterior, para comparar")
    p.add_argument("--salvar", action="store_true",
                   help="guarda esta medição como a nova referência")
    p.add_argument("--criar", action="store_true",
                   help="escreve um conjunto de exemplo e sai")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_rodar(args))


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
