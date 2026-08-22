"""Vetores: o que faz a busca por significado funcionar.

Um embedding é um texto virado em lista de números, arrumada de tal jeito que
textos parecidos ficam perto. É isso que deixa "como reservo memória?" achar o
trecho sobre `malloc` sem a palavra malloc aparecer na pergunta — e é a mesma
peça usada pela biblioteca, pela memória semântica e pelas lições.

POR QUE ISTO VIROU UM MÓDULO
----------------------------
Antes, gerar vetor era uma função privada dentro da biblioteca, amarrada ao
Gemini. Isso tinha duas consequências ruins: a biblioteca não funcionava sem
chave de nuvem, e qualquer outra parte do sistema que quisesse buscar por
significado teria que duplicar o código. Agora é um provedor com dois
implementadores e uma regra de escolha.

    ollama   local, nada sai da máquina, sem cota
    gemini   nuvem, gratuito, precisa de chave
    auto     tenta o local e cai no Gemini quando ele não está de pé

A COMPATIBILIDADE DE ÍNDICE É O DETALHE QUE MORDE
-------------------------------------------------
Vetor do Gemini e vetor do nomic-embed-text são números incomparáveis, mesmo
quando têm a mesma quantidade de dimensões. Comparar os dois não dá erro:
devolve semelhança aleatória, e a busca passa a trazer trecho errado sem
ninguém perceber — o pior tipo de falha, porque parece que funciona.

Por isso todo índice guarda a ASSINATURA de quem o gerou (`provedor:modelo:
dimensões`). Quem lê confere; batendo diferente, o índice é considerado
incompatível e precisa ser reconstruído explicitamente. Nada é apagado
sozinho.

CACHE
-----
Recalcular o vetor de uma memória que não mudou é desperdício puro — e, com
modelo local, desperdício lento. O cache é por hash do conteúdo + assinatura,
no mesmo SQLite do resto. Conteúdo mudou, hash muda, vetor novo.
"""

from __future__ import annotations

import hashlib
import logging

import httpx
import numpy as np

from . import config

log = logging.getLogger("livia.embeddings")

GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_EMBED_MODEL = "gemini-embedding-001"

LOTE = 50            # textos por chamada
LIMITE_TEXTO = 8000  # caracteres por texto; o resto é cortado


class EmbeddingError(RuntimeError):
    """Falha já em português, para mostrar ao usuário."""


# --------------------------------------------------------------------------
# Quem atende
# --------------------------------------------------------------------------


def provedores() -> list[str]:
    """A ordem de tentativa para gerar vetores, com a configuração atual.

    `auto` prefere o local e mantém o Gemini como reserva. Escolher `ollama`
    ou `gemini` na mão é uma decisão dura: nada de fallback silencioso para
    onde o André não pediu — inclusive porque isso mandaria para a nuvem um
    texto que ele quis manter em casa.
    """
    escolha = (config.EMBED_PROVIDER or "auto").lower()
    local = ["ollama"] if config.OLLAMA_ENABLED else []
    nuvem = [] if config.LOCAL_ONLY else (["gemini"] if config.GEMINI_API_KEY else [])

    if escolha == "ollama":
        return local
    if escolha == "gemini":
        return nuvem
    return local + nuvem


def disponivel() -> bool:
    return bool(provedores())


def _modelo(provedor: str) -> str:
    return (
        config.OLLAMA_EMBED_MODEL if provedor == "ollama" else GEMINI_EMBED_MODEL
    )


def assinatura(provedor: str | None = None, dimensoes: int | None = None) -> str:
    """Identidade do índice: quem gerou, com qual modelo, em quantas dimensões.

    Guardada junto de todo índice. É o que permite detectar, meses depois, que
    os vetores gravados não podem ser comparados com os de agora.
    """
    nome = provedor or (provedores()[0] if provedores() else "nenhum")
    dim = dimensoes if dimensoes is not None else _dimensoes_esperadas(nome)
    return f"{nome}:{_modelo(nome)}:{dim}"


def _dimensoes_esperadas(provedor: str) -> int | str:
    # O Gemini aceita a dimensão que pedirmos; o Ollama entrega a do modelo,
    # que só se descobre gerando um vetor. Por isso "?" até a primeira vez.
    return config.EMBED_DIMENSOES if provedor == "gemini" else "?"


# --------------------------------------------------------------------------
# Implementações
# --------------------------------------------------------------------------


async def _ollama(textos: list[str], _tarefa: str) -> list[list[float]]:
    """Embeddings locais via /api/embed.

    O endpoint antigo (`/api/embeddings`, singular, um texto por vez) ainda
    existe em instalações não atualizadas. Cair para ele quando o novo não
    existe custa pouco e evita um "não funciona" sem explicação.
    """
    url = f"{config.OLLAMA_BASE_URL}/api/embed"
    corpo = {
        "model": config.OLLAMA_EMBED_MODEL,
        "input": [t[:LIMITE_TEXTO] for t in textos],
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.OLLAMA_TIMEOUT)) as c:
            resposta = await c.post(url, json=corpo)
            if resposta.status_code == 404 and not _fala_de_modelo(resposta.text):
                # 404 aqui é ambíguo: pode ser "esta rota não existe nesta
                # versão" ou "o modelo não está baixado". Só a primeira merece
                # tentar a rota antiga; a segunda precisa da mensagem que
                # ensina o `ollama pull`.
                return await _ollama_antigo(textos, c)
            if resposta.status_code >= 400:
                raise EmbeddingError(_ollama_recado(resposta.text))
            dados = resposta.json()
    except httpx.ConnectError as exc:
        raise EmbeddingError(
            f"O Ollama não respondeu em {config.OLLAMA_BASE_URL}. "
            "O servidor está de pé? (`ollama serve`)"
        ) from exc
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"Falha de rede falando com o Ollama: {exc}") from exc

    vetores = dados.get("embeddings")
    if not isinstance(vetores, list) or not vetores:
        raise EmbeddingError("O Ollama devolveu uma resposta sem vetores.")
    return vetores


async def _ollama_antigo(textos: list[str], cliente: httpx.AsyncClient) -> list[list[float]]:
    url = f"{config.OLLAMA_BASE_URL}/api/embeddings"
    saida: list[list[float]] = []
    for texto in textos:
        resposta = await cliente.post(
            url, json={"model": config.OLLAMA_EMBED_MODEL, "prompt": texto[:LIMITE_TEXTO]}
        )
        if resposta.status_code >= 400:
            raise EmbeddingError(_ollama_recado(resposta.text))
        vetor = resposta.json().get("embedding")
        if not isinstance(vetor, list):
            raise EmbeddingError("O Ollama devolveu uma resposta sem vetor.")
        saida.append(vetor)
    return saida


def _fala_de_modelo(payload: str) -> bool:
    baixo = payload.lower()
    return "model" in baixo and "not found" in baixo


def _ollama_recado(payload: str) -> str:
    modelo = config.OLLAMA_EMBED_MODEL
    if "not found" in payload.lower():
        return (
            f"O modelo de embeddings '{modelo}' não está baixado. Rode:\n\n"
            f"    ollama pull {modelo}"
        )
    return f"O Ollama recusou o pedido de vetores: {payload[:200]}"


async def _gemini(textos: list[str], tarefa: str) -> list[list[float]]:
    if not config.GEMINI_API_KEY:
        raise EmbeddingError(
            "Sem GEMINI_API_KEY e sem Ollama, não há como gerar vetores. "
            "Ligue o local com LIVIA_OLLAMA=1 ou ponha a chave no .env."
        )

    corpo = {
        "requests": [
            {
                "model": f"models/{GEMINI_EMBED_MODEL}",
                "content": {"parts": [{"text": t[:LIMITE_TEXTO]}]},
                "taskType": tarefa,
                "outputDimensionality": config.EMBED_DIMENSOES,
            }
            for t in textos
        ]
    }
    headers = {
        "x-goog-api-key": config.GEMINI_API_KEY,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as c:
        resposta = await c.post(
            f"{GEMINI_EMBED_URL}/{GEMINI_EMBED_MODEL}:batchEmbedContents",
            json=corpo,
            headers=headers,
        )
        if resposta.status_code == 429:
            raise EmbeddingError(
                "Bateu o limite da API no meio do processamento. Ele reseta "
                "sozinho — espere alguns minutos e tente de novo."
            )
        if resposta.status_code >= 400:
            try:
                detalhe = resposta.json().get("error", {}).get("message", "")[:200]
            except ValueError:
                detalhe = resposta.text[:200]
            raise EmbeddingError(f"A API recusou o pedido: {detalhe}")
        return [e["values"] for e in resposta.json()["embeddings"]]


_IMPLEMENTACOES = {"ollama": _ollama, "gemini": _gemini}

# O Gemini melhora a busca sabendo se o texto é pergunta ou documento. O
# Ollama ignora — mas o parâmetro atravessa igual, para quem chama não
# precisar saber quem vai atender.
DOCUMENTO = "RETRIEVAL_DOCUMENT"
PERGUNTA = "RETRIEVAL_QUERY"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


async def gerar(
    textos: list[str], tarefa: str = DOCUMENTO
) -> tuple[np.ndarray, str]:
    """Vetores normalizados de uma lista de textos, e a assinatura de quem gerou.

    Normalizados porque, com comprimento 1, o produto escalar já É a
    semelhança do cosseno — a comparação fica uma multiplicação de matriz.
    """
    if not textos:
        raise EmbeddingError("Nada para vetorizar.")

    fila = provedores()
    if not fila:
        raise EmbeddingError(
            "Nenhum gerador de vetores disponível. Ligue o Ollama "
            "(LIVIA_OLLAMA=1) ou configure GEMINI_API_KEY."
        )

    problemas: list[str] = []
    for provedor in fila:
        try:
            bruto: list[list[float]] = []
            for i in range(0, len(textos), LOTE):
                bruto.extend(
                    await _IMPLEMENTACOES[provedor](textos[i : i + LOTE], tarefa)
                )
            matriz = normalizar(np.array(bruto, dtype=np.float32))
            log.debug("[embedding] provider=%s itens=%d", provedor, len(textos))
            return matriz, assinatura(provedor, matriz.shape[1])
        except EmbeddingError as exc:
            problemas.append(f"{provedor}: {exc}")
            continue

    raise EmbeddingError("\n".join(problemas))


async def gerar_um(texto: str, tarefa: str = PERGUNTA) -> tuple[np.ndarray, str]:
    matriz, assin = await gerar([texto], tarefa)
    return matriz[0], assin


def normalizar(m: np.ndarray) -> np.ndarray:
    """Todo vetor com comprimento 1, para comparar por produto escalar."""
    if m.ndim == 1:
        norma = float(np.linalg.norm(m)) or 1.0
        return (m / norma).astype(np.float32)
    normas = np.linalg.norm(m, axis=1, keepdims=True)
    normas[normas == 0] = 1
    return (m / normas).astype(np.float32)


def semelhancas(matriz: np.ndarray, alvo: np.ndarray) -> np.ndarray:
    """Semelhança de cada linha com o alvo. Ambos já normalizados."""
    if matriz.size == 0:
        return np.array([], dtype=np.float32)
    return matriz @ alvo


def compativel(gravada: str, atual: str | None = None) -> bool:
    """Um índice gravado com esta assinatura pode ser comparado com os de agora?

    Índice antigo, de antes deste módulo existir, não tem assinatura nenhuma.
    Nesses casos assumimos Gemini — que era o único jeito de gerá-lo — em vez
    de invalidar o trabalho de quem já usava a biblioteca.
    """
    if not gravada:
        gravada = f"gemini:{GEMINI_EMBED_MODEL}:{config.EMBED_DIMENSOES}"
    esperada = atual or assinatura()
    if esperada.endswith(":?") or gravada.endswith(":?"):
        # Dimensão ainda desconhecida (Ollama nunca chamado): compara só
        # provedor e modelo.
        return gravada.rsplit(":", 1)[0] == esperada.rsplit(":", 1)[0]
    return gravada == esperada


def hash_conteudo(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Cache por hash de conteúdo
# --------------------------------------------------------------------------


def chave_cache(provedor: str) -> str:
    """Identidade do gerador para fins de CACHE — sem a dimensão.

    A assinatura de índice inclui a dimensão porque ela é a checagem final
    contra comparar vetores incomparáveis. Aqui ela atrapalharia: a dimensão
    do Ollama só se descobre gerando o primeiro vetor, então todo processo
    recém-iniciado erraria a chave e recalcularia tudo uma vez. Modelo e
    provedor já determinam a dimensão; a redundância só custava.
    """
    return f"{provedor}:{_modelo(provedor)}"


async def com_cache(itens: dict[str, str], tarefa: str = DOCUMENTO) -> dict[str, np.ndarray]:
    """Vetores de {chave: texto}, reaproveitando o que não mudou.

    Recalcular vetor de memória que ninguém tocou é desperdício — e, com
    modelo local numa máquina modesta, desperdício de segundos por item. O
    cache é por hash do conteúdo: mudou o texto, muda o hash, gera de novo.

    A busca no cache percorre TODOS os provedores configurados, não só o
    preferido. Sem isso, uma temporada com o local fora do ar gravaria tudo
    sob a chave do Gemini e erraria o cache para sempre depois.
    """
    from . import db

    if not itens:
        return {}

    fila = provedores()
    saida: dict[str, np.ndarray] = {}
    faltando: dict[str, str] = {}

    for chave, texto in itens.items():
        digest = hash_conteudo(texto)
        for provedor in fila:
            guardado = db.embedding_em_cache(digest, chave_cache(provedor))
            if guardado is not None:
                saida[chave] = guardado
                break
        else:
            faltando[chave] = texto

    if not faltando:
        return saida

    chaves = list(faltando)
    matriz, assin_real = await gerar([faltando[k] for k in chaves], tarefa)
    # A assinatura devolvida diz quem REALMENTE atendeu — pode não ser o
    # preferido, se ele estava fora.
    quem = assin_real.split(":", 1)[0]

    for chave, vetor in zip(chaves, matriz):
        saida[chave] = vetor
        db.guardar_embedding(hash_conteudo(faltando[chave]), chave_cache(quem), vetor)

    return saida
