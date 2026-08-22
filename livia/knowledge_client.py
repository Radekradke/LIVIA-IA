"""Cliente do Knowledge Engine — o único lugar que fala com o serviço.

O motor de conhecimento roda como PROCESSO SEPARADO. Isso não é preciosismo
de arquitetura: o motor de referência (Cognee) traz 45 dependências
obrigatórias, entre elas `openai`, `litellm` e `lancedb`. Importar isso dentro
da Livia multiplicaria a instalação e arrastaria uma nuvem inteira para um
projeto que usa numpy e SQLite de propósito.

Consequência boa do sidecar: a Livia nunca importa Cognee. `pip install -r
requirements.txt && python run.py` continua funcionando sem nada disso, e
`import livia` jamais levanta `ModuleNotFoundError: cognee`.

Consequência ruim, que este arquivo existe para administrar: um serviço
separado pode estar desligado, lento ou quebrado, e nada disso pode derrubar
o chat. Daí as três defesas:

    TIMEOUT CURTO      recuperar é coisa de segundos; a resposta não espera
    CIRCUIT BREAKER    falhou, fica de castigo — não paga conexão recusada
                       a cada mensagem
    FALHA SILENCIOSA   erro vira lista vazia e log, nunca exceção subindo

A REGRA DO LOCAL_ONLY
---------------------
Com LIVIA_LOCAL_ONLY=1, nenhum conteúdo de documento sai da máquina. O
serviço só é aceito em endereço de loopback, e um endereço remoto é RECUSADO
com explicação em vez de silenciosamente obedecido. Um grafo de conhecimento
apontando para fora seria exatamente o furo que o modo local existe para
impedir — e o mais difícil de perceber, porque acontece na ingestão, longe
dos olhos.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx

from . import config
from .knowledge import KnowledgeHit, descartar_sem_procedencia

log = logging.getLogger("livia.knowledge")

# Endereços que contam como "esta máquina".
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

# Indexar pode levar minutos; recuperar, não. São tetos diferentes porque são
# problemas diferentes: a ingestão roda fora do pedido do chat.
TIMEOUT_INGESTAO = 600.0


class _Disjuntor:
    """Memória curta de falhas, no mesmo espírito do `saude.py`.

    Sem isto, com o serviço desligado, TODA mensagem pagaria uma tentativa de
    conexão antes de cair no vetor. Com dezenas de mensagens por dia isso é
    latência jogada fora para descobrir de novo o que já se sabia.
    """

    def __init__(self) -> None:
        self.ate = 0.0
        self.ultimo_erro = ""
        self.falhas = 0

    def disponivel(self) -> bool:
        return time.time() >= self.ate

    def registrar_falha(self, motivo: str) -> None:
        self.falhas += 1
        self.ultimo_erro = motivo[:200]
        self.ate = time.time() + config.KNOWLEDGE_DESCANSO
        log.debug("[knowledge] indisponível: %s (castigo de %.0fs)",
                  motivo[:120], config.KNOWLEDGE_DESCANSO)

    def registrar_sucesso(self) -> None:
        self.ate = 0.0
        self.ultimo_erro = ""
        self.falhas = 0

    def descanso_restante(self) -> float:
        return max(0.0, round(self.ate - time.time(), 1))


_disjuntor = _Disjuntor()


def limpar() -> None:
    """Zera o disjuntor. Usado pelos testes e ao reconfigurar."""
    _disjuntor.registrar_sucesso()


# --------------------------------------------------------------------------
# Elegibilidade
# --------------------------------------------------------------------------


def endereco_local(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in LOOPBACK


def impedimento() -> str:
    """Por que o Knowledge Engine NÃO pode ser usado agora? Vazio = pode.

    Devolve texto em vez de booleano porque cada motivo pede uma ação
    diferente do André, e "não está funcionando" não diz qual.
    """
    if not config.KNOWLEDGE_ENABLED:
        return "desligado (LIVIA_KNOWLEDGE=0)"
    if config.LOCAL_ONLY and not endereco_local(config.KNOWLEDGE_URL):
        return (
            f"recusado: LIVIA_LOCAL_ONLY=1 e o serviço está em "
            f"{config.KNOWLEDGE_URL}, que não é esta máquina. Nenhum documento "
            "sai daqui nesse modo."
        )
    return ""


def ligado() -> bool:
    return not impedimento()


def disponivel() -> bool:
    """Vale a pena tentar falar com o serviço agora?"""
    return ligado() and _disjuntor.disponivel()


def diagnostico() -> dict[str, object]:
    """Retrato para a interface. Nunca inclui conteúdo de documento."""
    motivo = impedimento()
    return {
        "ligado": bool(config.KNOWLEDGE_ENABLED),
        "url": config.KNOWLEDGE_URL,
        "impedimento": motivo,
        "em_castigo": not _disjuntor.disponivel(),
        "descanso_segundos": _disjuntor.descanso_restante(),
        "falhas": _disjuntor.falhas,
        "ultimo_erro": _disjuntor.ultimo_erro,
        "modelo_llm": config.KNOWLEDGE_LLM_MODEL,
        "modelo_embed": config.KNOWLEDGE_EMBED_MODEL,
    }


# --------------------------------------------------------------------------
# Conversa com o serviço
# --------------------------------------------------------------------------


async def _pedir(
    metodo: str,
    caminho: str,
    *,
    corpo: dict[str, object] | None = None,
    timeout: float | None = None,
    conta_falha: bool = True,
) -> dict[str, object] | None:
    """Uma chamada ao serviço. Devolve None em qualquer problema.

    Nunca levanta exceção: quem chama está no meio de uma resposta ao André,
    e o conhecimento é um bônus. Falhar aqui significa responder sem o grafo,
    não deixar de responder.
    """
    motivo = impedimento()
    if motivo:
        return None

    url = f"{config.KNOWLEDGE_URL}{caminho}"
    limite = timeout if timeout is not None else config.KNOWLEDGE_TIMEOUT

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(limite)) as cliente:
            if metodo == "GET":
                resposta = await cliente.get(url)
            elif metodo == "DELETE":
                resposta = await cliente.delete(url)
            else:
                resposta = await cliente.post(url, json=corpo or {})

        if resposta.status_code >= 500:
            if conta_falha:
                _disjuntor.registrar_falha(f"HTTP {resposta.status_code}")
            return None
        if resposta.status_code >= 400:
            # 4xx é problema do PEDIDO, não do serviço: ele está de pé e
            # respondeu. Colocá-lo de castigo puniria o serviço por um erro
            # nosso, e derrubaria o grafo inteiro por causa de um documento.
            log.debug("[knowledge] %s %s -> %s", metodo, caminho, resposta.status_code)
            return None

        _disjuntor.registrar_sucesso()
        dados = resposta.json()
        return dados if isinstance(dados, dict) else None

    except httpx.TimeoutException:
        if conta_falha:
            _disjuntor.registrar_falha("timeout")
        log.debug("[knowledge] timeout em %s", caminho)
        return None
    except httpx.HTTPError as exc:
        if conta_falha:
            _disjuntor.registrar_falha(f"{type(exc).__name__}")
        return None
    except ValueError:  # JSON inválido
        if conta_falha:
            _disjuntor.registrar_falha("resposta ilegível")
        return None


async def status() -> dict[str, object]:
    """O `/health` do serviço, já interpretado.

    Não passa pelo disjuntor com `conta_falha`: a tela de diagnóstico é
    justamente onde o André vai olhar quando algo está errado, e ela precisa
    poder consultar mesmo durante o castigo.
    """
    motivo = impedimento()
    if motivo:
        return {"status": "off", "motivo": motivo}

    dados = await _pedir("GET", "/health", timeout=3.0, conta_falha=False)
    if dados is None:
        return {
            "status": "offline",
            "motivo": (
                f"Nada respondendo em {config.KNOWLEDGE_URL}. O serviço está "
                "de pé? Suba com `python -m services.knowledge.run`."
            ),
        }
    return dados


async def ingerir(
    document_id: str,
    trechos: list[dict[str, object]],
    meta: dict[str, object],
) -> dict[str, object] | None:
    """Manda um documento para virar grafo.

    Timeout largo: construir grafo é caro. Quem chama isto é a FILA, nunca o
    pedido do chat — ver `db.job_*` e `knowledge_jobs`.
    """
    if not disponivel():
        return None

    corpo = {
        "document_id": document_id,
        "title": meta.get("titulo") or document_id,
        "source": meta.get("arquivo") or meta.get("titulo") or document_id,
        "collection_id": meta.get("collection_id") or "",
        "chunks": [
            {
                "chunk_id": f"{document_id}#{i}",
                "text": str(t.get("texto") or ""),
                "page": t.get("pagina") or None,
                "origin": t.get("origem") or "",
                "type": t.get("tipo") or "text",
            }
            for i, t in enumerate(trechos)
        ],
    }
    return await _pedir("POST", "/documents", corpo=corpo, timeout=TIMEOUT_INGESTAO)


async def remover(document_id: str) -> bool:
    """Apaga o conhecimento de UM documento, sem tocar nos outros.

    É por isso que cada documento tem dataset próprio no motor. Um dataset
    único e gigante tornaria isto impossível sem apagar tudo.
    """
    if not disponivel():
        return False
    resposta = await _pedir("DELETE", f"/documents/{document_id}", timeout=30.0)
    return bool(resposta and resposta.get("ok"))


async def buscar_grafo(pergunta: str, limite: int | None = None) -> list[KnowledgeHit]:
    """Recuperação relacional. Lista vazia é resposta legítima, não erro."""
    if not disponivel() or not pergunta.strip():
        return []

    limite = limite or config.KNOWLEDGE_MAX_RESULTS
    dados = await _pedir(
        "POST", "/search/graph", corpo={"query": pergunta[:2000], "limit": limite}
    )
    if not dados:
        return []
    return _ler_resultados(dados)


def _ler_resultados(dados: dict[str, object]) -> list[KnowledgeHit]:
    brutos = dados.get("results")
    if not isinstance(brutos, list):
        return []

    hits = [
        KnowledgeHit.de_json(item) for item in brutos[:50] if isinstance(item, dict)
    ]
    # A regra dura: sem procedência, não entra. Um grafo sabe afirmar "X causa
    # Y" sem dizer onde leu isso, e essa afirmação é indistinguível de
    # invenção depois que chega no prompt.
    return descartar_sem_procedencia(hits)


async def reconstruir(document_id: str) -> bool:
    """Refaz o grafo de um documento a partir dos trechos já guardados."""
    if not disponivel():
        return False
    resposta = await _pedir(
        "POST", f"/documents/{document_id}/rebuild", timeout=TIMEOUT_INGESTAO
    )
    return bool(resposta and resposta.get("ok"))
