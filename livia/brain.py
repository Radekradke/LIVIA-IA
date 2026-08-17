"""O cérebro: a única parte que sabe quais modelos de IA existem.

Todo o resto do sistema chama `stream()` e `structured()` e não faz ideia de
quem respondeu. Para acrescentar um provedor novo, escreva as duas funções dele
aqui e coloque o nome em LIVIA_PROVIDERS. Nada mais no projeto muda.

CADEIA DE FALLBACK
------------------
Os provedores são tentados na ordem de LIVIA_PROVIDERS. Se um falhar por motivo
passageiro — cota estourada, servidor fora, timeout — o próximo assume e o
usuário nem percebe. Erros que não adianta repetir (chave errada, modelo
inexistente) não acionam o fallback: eles sobem na hora, porque tentar de novo
só esconderia o problema real.

A troca só acontece enquanto NADA foi entregue. Depois que a primeira palavra
chegou na tela, recomeçar duplicaria o texto que a pessoa já está lendo — nesse
ponto o erro sobe mesmo.

QUEM SABE O QUÊ
---------------
Só o Gemini abre links sozinho (`url_context`). Caindo para a Groq, essa
capacidade some, mas a busca continua funcionando: os resultados do DuckDuckGo
entram como texto no prompt, e isso qualquer modelo lê.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable

import httpx

from . import config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)

TENTATIVAS = 2  # por provedor, antes de passar para o próximo
ESPERA_BASE = 1.5


class BrainError(RuntimeError):
    """Falha final, já em português claro, para mostrar ao usuário."""


class ProvedorIndisponivel(RuntimeError):
    """Falha passageira de um provedor. Aciona o próximo da fila."""


# --------------------------------------------------------------------------
# Diagnóstico compartilhado
# --------------------------------------------------------------------------


def _api_message(payload: str) -> str:
    """A mensagem que a própria API mandou — costuma ser a parte útil."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        erro = data.get("error")
        if isinstance(erro, dict) and isinstance(erro.get("message"), str):
            return erro["message"]
        if isinstance(erro, str):
            return erro
    return ""


def _classificar(provedor: str, status: int, payload: str, modelo: str):
    """Decide se vale tentar outro provedor ou se o erro é definitivo."""
    detalhe = _api_message(payload)

    if status in (401, 403):
        return BrainError(
            f"[{provedor}] A chave da API foi recusada. Confira o valor no .env "
            "e se ela continua ativa no painel do provedor."
        )
    if status == 404:
        base = (
            f"[{provedor}] O modelo '{modelo}' não está disponível para a sua chave. "
            "Troque no .env e reinicie o servidor."
        )
        return BrainError(f"{base}\n\nA API respondeu: {detalhe}" if detalhe else base)
    if status == 429:
        return ProvedorIndisponivel(f"{provedor}: cota/limite atingido")
    if status >= 500:
        return ProvedorIndisponivel(f"{provedor}: servidor fora ({status})")

    return BrainError(
        f"[{provedor}] Erro {status}: {detalhe or payload[:250]}"
    )


def _provedores() -> list[str]:
    """Os provedores configurados que têm chave, na ordem pedida."""
    chaves = {"gemini": config.GEMINI_API_KEY, "groq": config.GROQ_API_KEY}
    return [p for p in config.PROVIDERS if chaves.get(p)]


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------


def _gemini_contents(messages: Iterable[dict[str, str]]) -> list[dict[str, object]]:
    saida: list[dict[str, object]] = []
    for m in messages:
        texto = (m.get("content") or "").strip()
        if not texto:
            continue
        papel = "model" if m.get("role") == "assistant" else "user"
        saida.append({"role": papel, "parts": [{"text": texto}]})
    return saida


def _gemini_texto(chunk: dict[str, object]) -> str:
    candidatos = chunk.get("candidates")
    if not isinstance(candidatos, list) or not candidatos:
        return ""
    primeiro = candidatos[0]
    if not isinstance(primeiro, dict):
        return ""
    conteudo = primeiro.get("content")
    if not isinstance(conteudo, dict):
        return ""
    partes = conteudo.get("parts")
    if not isinstance(partes, list):
        return ""
    return "".join(
        p["text"] for p in partes
        if isinstance(p, dict) and isinstance(p.get("text"), str)
    )


def _gemini_fontes(chunk: dict[str, object], destino: list[str]) -> None:
    """URLs que o modelo realmente abriu. Vêm em dois campos diferentes."""
    candidatos = chunk.get("candidates")
    if not isinstance(candidatos, list) or not candidatos:
        return
    primeiro = candidatos[0]
    if not isinstance(primeiro, dict):
        return

    grounding = primeiro.get("groundingMetadata")
    if isinstance(grounding, dict):
        for pedaco in grounding.get("groundingChunks") or []:
            if isinstance(pedaco, dict):
                web = pedaco.get("web")
                if isinstance(web, dict) and isinstance(web.get("uri"), str):
                    if web["uri"] not in destino:
                        destino.append(web["uri"])

    url_meta = primeiro.get("urlContextMetadata")
    if isinstance(url_meta, dict):
        for item in url_meta.get("urlMetadata") or []:
            if isinstance(item, dict):
                uri = item.get("retrievedUrl") or item.get("retrieved_url")
                if isinstance(uri, str) and uri not in destino:
                    destino.append(uri)


async def _gemini_stream(
    system_prompt: str,
    messages: Iterable[dict[str, str]],
    temperature: float,
    ler_urls: bool,
    fontes: list[str] | None,
) -> AsyncIterator[str]:
    modelo = config.MODEL
    corpo: dict[str, object] = {
        "contents": _gemini_contents(messages),
        "generationConfig": {"temperature": temperature},
    }
    if system_prompt.strip():
        corpo["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    if ler_urls:
        # Ferramenta nativa e gratuita. (A irmã dela, `google_search`, é paga.)
        corpo["tools"] = [{"url_context": {}}]

    url = f"{GEMINI_URL}/{modelo}:streamGenerateContent?alt=sse"
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", url, json=corpo, headers=headers) as resp:
            if resp.status_code >= 400:
                detalhe = (await resp.aread()).decode("utf-8", "replace")
                raise _classificar("gemini", resp.status_code, detalhe, modelo)

            async for linha in resp.aiter_lines():
                if not linha.startswith("data:"):
                    continue
                bruto = linha[5:].strip()
                if not bruto or bruto == "[DONE]":
                    continue
                try:
                    chunk = json.loads(bruto)
                except json.JSONDecodeError:
                    continue
                if fontes is not None:
                    _gemini_fontes(chunk, fontes)
                texto = _gemini_texto(chunk)
                if texto:
                    yield texto


async def _gemini_structured(
    system_prompt: str, user_prompt: str, schema: dict[str, object], temperature: float
) -> object | None:
    corpo = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    url = f"{GEMINI_URL}/{config.FAST_MODEL}:generateContent"
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, json=corpo, headers=headers)
        if resp.status_code >= 400:
            raise _classificar(
                "gemini", resp.status_code, resp.text, config.FAST_MODEL
            )
        texto = _gemini_texto(resp.json())
        return json.loads(texto) if texto.strip() else None


# --------------------------------------------------------------------------
# Groq (formato compatível com OpenAI)
# --------------------------------------------------------------------------


def _groq_messages(
    system_prompt: str, messages: Iterable[dict[str, str]]
) -> list[dict[str, str]]:
    saida = []
    if system_prompt.strip():
        saida.append({"role": "system", "content": system_prompt})
    for m in messages:
        texto = (m.get("content") or "").strip()
        if texto:
            papel = "assistant" if m.get("role") == "assistant" else "user"
            saida.append({"role": papel, "content": texto})
    return saida


async def _groq_stream(
    system_prompt: str,
    messages: Iterable[dict[str, str]],
    temperature: float,
) -> AsyncIterator[str]:
    modelo = config.GROQ_MODEL
    corpo = {
        "model": modelo,
        "messages": _groq_messages(system_prompt, messages),
        "temperature": temperature,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", GROQ_URL, json=corpo, headers=headers) as resp:
            if resp.status_code >= 400:
                detalhe = (await resp.aread()).decode("utf-8", "replace")
                raise _classificar("groq", resp.status_code, detalhe, modelo)

            async for linha in resp.aiter_lines():
                if not linha.startswith("data:"):
                    continue
                bruto = linha[5:].strip()
                if not bruto or bruto == "[DONE]":
                    continue
                try:
                    chunk = json.loads(bruto)
                except json.JSONDecodeError:
                    continue
                escolhas = chunk.get("choices")
                if not isinstance(escolhas, list) or not escolhas:
                    continue
                delta = escolhas[0].get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    if delta["content"]:
                        yield delta["content"]


async def _groq_structured(
    system_prompt: str, user_prompt: str, schema: dict[str, object], temperature: float
) -> object | None:
    # A Groq segue o formato da OpenAI: JSON garantido, mas sem schema tipado.
    # Descrevemos o formato no prompt e validamos o que voltar.
    corpo = {
        "model": config.GROQ_FAST_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n\nResponda SOMENTE com JSON válido "
                    f"neste formato:\n{json.dumps(schema, ensure_ascii=False)}"
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {config.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(GROQ_URL, json=corpo, headers=headers)
        if resp.status_code >= 400:
            raise _classificar("groq", resp.status_code, resp.text, config.GROQ_FAST_MODEL)
        texto = resp.json()["choices"][0]["message"]["content"]
        return json.loads(texto) if texto.strip() else None


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------

_STREAMS = {"gemini": _gemini_stream, "groq": _groq_stream}
_STRUCTURED = {"gemini": _gemini_structured, "groq": _groq_structured}


async def stream(
    system_prompt: str,
    messages: Iterable[dict[str, str]],
    *,
    temperature: float = 0.7,
    ler_urls: bool = False,
    fontes: list[str] | None = None,
    usados: list[str] | None = None,
    preferir: str | None = None,
) -> AsyncIterator[str]:
    """Gera a resposta em pedaços, trocando de provedor se algum falhar.

    Em `usados`, se você passar uma lista, é anotado quem de fato respondeu —
    serve para a interface avisar quando o reserva entrou em campo.

    `preferir` põe um provedor na frente só nesta chamada, sem mexer na
    configuração. Serve para rotear por capacidade: quem abre links é o
    Gemini, então uma mensagem com URL vai para ele mesmo que a Groq seja a
    padrão. Os demais continuam na fila como reserva.
    """
    disponiveis = _provedores()
    if preferir and preferir in disponiveis:
        disponiveis = [preferir] + [p for p in disponiveis if p != preferir]
    if not disponiveis:
        raise BrainError(
            "Nenhuma chave de API configurada. Crie um .env na pasta do projeto "
            "com GEMINI_API_KEY (gratuita em https://aistudio.google.com/apikey) "
            "e, se quiser um reserva, GROQ_API_KEY."
        )

    problemas: list[str] = []

    for indice, provedor in enumerate(disponiveis):
        ultimo_provedor = indice == len(disponiveis) - 1
        funcao = _STREAMS[provedor]

        for tentativa in range(TENTATIVAS):
            ultima_tentativa = tentativa == TENTATIVAS - 1
            entregou_algo = False
            try:
                if provedor == "gemini":
                    gerador = funcao(
                        system_prompt, messages, temperature, ler_urls, fontes
                    )
                else:
                    gerador = funcao(system_prompt, messages, temperature)

                async for pedaco in gerador:
                    if not entregou_algo:
                        entregou_algo = True
                        if usados is not None:
                            usados.append(provedor)
                    yield pedaco
                return

            except ProvedorIndisponivel as exc:
                if entregou_algo:
                    raise BrainError(f"A resposta foi interrompida: {exc}") from exc
                problemas.append(str(exc))
                if not ultima_tentativa:
                    await asyncio.sleep(ESPERA_BASE * (2**tentativa))
                    continue
                break  # próximo provedor

            except BrainError:
                raise  # erro de configuração: trocar de provedor não resolve

            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if entregou_algo:
                    raise BrainError("A resposta foi interrompida no meio.") from exc
                problemas.append(f"{provedor}: {type(exc).__name__}")
                if not ultima_tentativa:
                    await asyncio.sleep(ESPERA_BASE * (2**tentativa))
                    continue
                break

        if ultimo_provedor:
            # As tentativas repetidas do mesmo provedor viram uma linha só —
            # ler "gemini: cota" três vezes não ajuda ninguém.
            unicos = list(dict.fromkeys(problemas))
            raise BrainError(
                "Todos os provedores falharam:\n  - "
                + "\n  - ".join(unicos)
                + "\n\nSe for limite de cota, ele reseta sozinho. "
                "Espere alguns minutos e tente de novo."
            )


async def structured(
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, object],
    *,
    temperature: float = 0.0,
) -> object | None:
    """Pede uma resposta em JSON válido, com a mesma cadeia de fallback.

    Devolve None em qualquer falha. Quem chama isto — o filtro de memória e a
    decisão de buscar na web — é acessório: se falhar, a conversa segue.
    """
    for provedor in _provedores():
        try:
            return await _STRUCTURED[provedor](
                system_prompt, user_prompt, schema, temperature
            )
        except Exception:
            continue
    return None
