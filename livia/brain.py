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

O OLLAMA
--------
É o provedor local: sem chave, sem cota, sem nada saindo da máquina. Ele fala
o próprio protocolo (`/api/chat`, NDJSON em vez de SSE), então tem funções
próprias aqui — mas o formato de mensagem é o mesmo da OpenAI, com uma
diferença que quebra em silêncio se você não souber: os argumentos de
ferramenta vêm como OBJETO, não como string JSON.

Ferramenta com modelo local só é oferecida quando LIVIA_OLLAMA_TOOLS=1. Vários
modelos aceitam o parâmetro `tools` e o ignoram — a resposta sai dizendo que
fez, sem ter feito. Preferimos não declarar a capacidade a declará-la e mentir.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable

import httpx

from . import config

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL_SUFIXO = "/chat/completions"
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)

TENTATIVAS = 2  # por provedor, antes de passar para o próximo
ESPERA_BASE = 1.5


class BrainError(RuntimeError):
    """Falha final, já em português claro, para mostrar ao usuário."""


class ProvedorIndisponivel(RuntimeError):
    """Falha passageira de um provedor. Aciona o próximo da fila."""

    def __init__(self, mensagem: str, tipo: str = "servidor") -> None:
        super().__init__(mensagem)
        self.tipo = tipo


class ProvedorQuebrado(ProvedorIndisponivel):
    """Erro de configuração: chave recusada ou modelo inexistente.

    Também aciona o próximo da fila — uma chave errada na Groq não pode
    derrubar a conversa quando o Gemini funciona. A diferença é que este
    provedor sai de circulação até alguém mexer no .env, e o André é
    avisado. A versão anterior derrubava tudo para o erro não passar
    despercebido; resiliência COM aviso é melhor que rigidez.
    """

    def __init__(self, mensagem: str, tipo: str = "chave") -> None:
        super().__init__(mensagem, tipo)


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
        return ProvedorQuebrado(
            f"{provedor}: chave recusada — confira o valor no .env", "chave"
        )
    if status == 404:
        extra = f" A API respondeu: {detalhe}" if detalhe else ""
        return ProvedorQuebrado(
            f"{provedor}: o modelo '{modelo}' não existe para esta chave.{extra}",
            "modelo",
        )
    if status == 429:
        return ProvedorIndisponivel(f"{provedor}: cota/limite atingido", "cota")
    if status >= 500:
        return ProvedorIndisponivel(f"{provedor}: servidor fora ({status})", "servidor")

    return ProvedorQuebrado(
        f"{provedor}: erro {status}. {detalhe or payload[:200]}", "modelo"
    )


def _provedores() -> list[str]:
    """Os provedores configurados que têm chave, na ordem pedida."""
    from . import router
    return [p for p in router.disponiveis() if p in _STREAMS]


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------


def _gemini_contents(messages: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    saida: list[dict[str, object]] = []
    for m in messages:
        papel_bruto = m.get("role")

        # Pedido de ferramenta feito pelo modelo
        if papel_bruto == "assistant" and m.get("ferramentas"):
            saida.append({
                "role": "model",
                "parts": [
                    {"functionCall": {"name": c["nome"], "args": c["args"]}}
                    for c in m["ferramentas"]
                ],
            })
            continue

        # Resultado que devolvemos para ele
        if papel_bruto == "ferramenta":
            saida.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": m.get("nome", ""),
                        "response": {"resultado": m.get("resultado", "")},
                    }
                }],
            })
            continue

        texto = str(m.get("content") or "").strip()
        if not texto:
            continue
        saida.append({
            "role": "model" if papel_bruto == "assistant" else "user",
            "parts": [{"text": texto}],
        })
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
    system_prompt: str, messages: Iterable[dict[str, object]]
) -> list[dict[str, object]]:
    saida: list[dict[str, object]] = []
    if system_prompt.strip():
        saida.append({"role": "system", "content": system_prompt})

    for m in messages:
        papel_bruto = m.get("role")

        if papel_bruto == "assistant" and m.get("ferramentas"):
            saida.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": c["id"] or f"c{i}",
                        "type": "function",
                        "function": {
                            "name": c["nome"],
                            "arguments": json.dumps(c["args"], ensure_ascii=False),
                        },
                    }
                    for i, c in enumerate(m["ferramentas"])
                ],
            })
            continue

        if papel_bruto == "ferramenta":
            saida.append({
                "role": "tool",
                "tool_call_id": m.get("id") or "c0",
                "content": str(m.get("resultado", "")),
            })
            continue

        texto = str(m.get("content") or "").strip()
        if texto:
            saida.append({
                "role": "assistant" if papel_bruto == "assistant" else "user",
                "content": texto,
            })
    return saida


async def _openai_compativel_stream(
    provedor: str,
    url: str,
    chave: str,
    modelo: str,
    system_prompt: str,
    messages: Iterable[dict[str, object]],
    temperature: float,
    extra_headers: dict[str, str] | None = None,
) -> AsyncIterator[str]:
    """Motor comum da Groq e do OpenRouter — os dois falam o formato da OpenAI.

    Duplicar isso seria pedir para as duas implementações divergirem com o
    tempo, e a que ninguém olha é a que quebra.
    """
    corpo = {
        "model": modelo,
        "messages": _groq_messages(system_prompt, messages),
        "temperature": temperature,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {chave}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async with client.stream("POST", url, json=corpo, headers=headers) as resp:
            if resp.status_code >= 400:
                detalhe = (await resp.aread()).decode("utf-8", "replace")
                raise _classificar(provedor, resp.status_code, detalhe, modelo)

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


async def _groq_stream(system_prompt, messages, temperature) -> AsyncIterator[str]:
    async for pedaco in _openai_compativel_stream(
        "groq", GROQ_URL, config.GROQ_API_KEY, config.GROQ_MODEL,
        system_prompt, messages, temperature,
    ):
        yield pedaco


async def _openrouter_stream(system_prompt, messages, temperature) -> AsyncIterator[str]:
    """OpenRouter: só texto. Sem ferramentas, sem saída estruturada.

    O modelo padrão (`openrouter/free`) escolhe entre dezenas de gratuitos a
    cada pedido, e nem todos honram esses formatos do mesmo jeito. Melhor não
    prometer do que prometer e falhar em silêncio — ver router.CATALOGO.
    """
    async for pedaco in _openai_compativel_stream(
        "openrouter",
        config.OPENROUTER_BASE_URL + OPENROUTER_URL_SUFIXO,
        config.OPENROUTER_API_KEY,
        config.OPENROUTER_MODEL,
        system_prompt, messages, temperature,
        # Cabeçalhos de cortesia do OpenRouter: identificam a origem no painel
        # deles. Não carregam nada sensível.
        extra_headers={
            "HTTP-Referer": "https://github.com/Radekradke/LIVIA-IA",
            "X-Title": "LIVIA",
        },
    ):
        yield pedaco


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
# Ollama (local)
# --------------------------------------------------------------------------


def _ollama_mensagens(
    system_prompt: str, messages: Iterable[dict[str, object]]
) -> list[dict[str, object]]:
    """Histórico no formato do Ollama.

    Quase igual ao da OpenAI, com uma diferença que custa caro descobrir na
    marra: `arguments` aqui é um OBJETO, não uma string JSON. Mandar string
    faz o modelo receber o nome do argumento como se fosse o valor.
    """
    saida: list[dict[str, object]] = []
    if system_prompt.strip():
        saida.append({"role": "system", "content": system_prompt})

    for m in messages:
        papel = m.get("role")

        if papel == "assistant" and m.get("ferramentas"):
            saida.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": c["nome"], "arguments": c["args"]}}
                    for c in m["ferramentas"]
                ],
            })
            continue

        if papel == "ferramenta":
            saida.append({
                "role": "tool",
                "content": str(m.get("resultado", "")),
            })
            continue

        texto = str(m.get("content") or "").strip()
        if texto:
            saida.append({
                "role": "assistant" if papel == "assistant" else "user",
                "content": texto,
            })
    return saida


def _ollama_timeout() -> httpx.Timeout:
    """Paciência maior que a da nuvem: a máquina é a sua, e ela pensa devagar."""
    return httpx.Timeout(
        connect=5.0, read=config.OLLAMA_TIMEOUT, write=30.0, pool=5.0
    )


def _ollama_erro(status: int, payload: str, modelo: str):
    """Traduz a falha do Ollama para algo acionável.

    Modelo não baixado é o erro nº 1 de quem acabou de instalar, e a mensagem
    crua ("model 'x' not found, try pulling it first") não diz o comando. Aqui
    dizemos — mas NÃO baixamos nada sozinhos: são gigabytes na conexão de
    alguém, e isso não se faz sem pedir.
    """
    detalhe = _api_message(payload) or payload[:200]
    if status == 404 or "not found" in detalhe.lower():
        return ProvedorQuebrado(
            f"ollama: o modelo '{modelo}' não está baixado nesta máquina. "
            f"Rode no terminal:\n\n    ollama pull {modelo}\n\n"
            "Depois é só mandar a mensagem de novo.",
            "modelo",
        )
    if status >= 500:
        return ProvedorIndisponivel(f"ollama: servidor devolveu {status}", "servidor")
    return ProvedorQuebrado(f"ollama: erro {status}. {detalhe}", "modelo")


def _ollama_fora(exc: Exception) -> ProvedorIndisponivel:
    return ProvedorIndisponivel(
        f"ollama: nada respondendo em {config.OLLAMA_BASE_URL} — o servidor "
        "está desligado? Suba com `ollama serve` (ou abra o aplicativo).",
        "servidor",
    )


def _ollama_linhas(bruto: str) -> dict[str, object] | None:
    try:
        dados = json.loads(bruto)
    except json.JSONDecodeError:
        return None
    return dados if isinstance(dados, dict) else None


async def _ollama_stream(
    system_prompt: str,
    messages: Iterable[dict[str, object]],
    temperature: float,
) -> AsyncIterator[str]:
    """Resposta em pedaços. O Ollama manda NDJSON, uma linha por pedaço."""
    modelo = config.OLLAMA_MODEL
    corpo = {
        "model": modelo,
        "messages": _ollama_mensagens(system_prompt, messages),
        "stream": True,
        "options": {"temperature": temperature},
    }

    try:
        async with httpx.AsyncClient(timeout=_ollama_timeout()) as client:
            async with client.stream(
                "POST", f"{config.OLLAMA_BASE_URL}/api/chat", json=corpo
            ) as resp:
                if resp.status_code >= 400:
                    detalhe = (await resp.aread()).decode("utf-8", "replace")
                    raise _ollama_erro(resp.status_code, detalhe, modelo)

                async for linha in resp.aiter_lines():
                    if not linha.strip():
                        continue
                    dados = _ollama_linhas(linha)
                    if dados is None:
                        continue
                    if isinstance(dados.get("error"), str):
                        raise _ollama_erro(404, json.dumps(dados), modelo)
                    mensagem = dados.get("message")
                    if isinstance(mensagem, dict):
                        pedaco = mensagem.get("content")
                        if isinstance(pedaco, str) and pedaco:
                            yield pedaco
    except httpx.ConnectError as exc:
        raise _ollama_fora(exc) from exc


def _para_json_schema(schema: dict[str, object]) -> dict[str, object]:
    """O schema do projeto é escrito no dialeto do Gemini (`"type": "OBJECT"`).

    O Ollama espera JSON Schema padrão, em minúsculas. Converter aqui evita
    reescrever os schemas espalhados pelo projeto — e evita que cada módulo
    precise saber qual provedor vai atender.
    """
    if not isinstance(schema, dict):
        return schema
    saida: dict[str, object] = {}
    for chave, valor in schema.items():
        if chave == "type" and isinstance(valor, str):
            saida[chave] = valor.lower()
        elif chave == "properties" and isinstance(valor, dict):
            saida[chave] = {k: _para_json_schema(v) for k, v in valor.items()}
        elif chave == "items" and isinstance(valor, dict):
            saida[chave] = _para_json_schema(valor)
        else:
            saida[chave] = valor
    return saida


async def _ollama_structured(
    system_prompt: str, user_prompt: str, schema: dict[str, object], temperature: float
) -> object | None:
    """JSON garantido pelo `format` do Ollama, que aceita JSON Schema direto."""
    modelo = config.OLLAMA_FAST_MODEL or config.OLLAMA_MODEL
    corpo = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": _para_json_schema(schema),
        "options": {"temperature": temperature},
    }

    try:
        async with httpx.AsyncClient(timeout=_ollama_timeout()) as client:
            resp = await client.post(
                f"{config.OLLAMA_BASE_URL}/api/chat", json=corpo
            )
            if resp.status_code >= 400:
                raise _ollama_erro(resp.status_code, resp.text, modelo)
            dados = resp.json()
    except httpx.ConnectError as exc:
        raise _ollama_fora(exc) from exc

    texto = ""
    mensagem = dados.get("message")
    if isinstance(mensagem, dict):
        texto = str(mensagem.get("content") or "")
    if not texto.strip():
        return None
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return None


async def _ollama_ferramentas(system_prompt, messages, catalogo):
    """Uma rodada com ferramentas — só quando o modelo local declara suportar.

    Sem LIVIA_OLLAMA_TOOLS=1 nem chegamos aqui: o roteador não lista o Ollama
    para tarefas que exigem `tools`.
    """
    modelo = config.OLLAMA_MODEL
    corpo = {
        "model": modelo,
        "messages": _ollama_mensagens(system_prompt, messages),
        "stream": False,
        "tools": [{"type": "function", "function": f} for f in catalogo],
        "options": {"temperature": 0.4},
    }

    try:
        async with httpx.AsyncClient(timeout=_ollama_timeout()) as client:
            resp = await client.post(f"{config.OLLAMA_BASE_URL}/api/chat", json=corpo)
            if resp.status_code >= 400:
                raise _ollama_erro(resp.status_code, resp.text, modelo)
            dados = resp.json()
    except httpx.ConnectError as exc:
        raise _ollama_fora(exc) from exc

    mensagem = dados.get("message")
    brutas = mensagem.get("tool_calls") if isinstance(mensagem, dict) else None
    if not brutas:
        return [], []

    chamadas = []
    for i, c in enumerate(brutas):
        fn = c.get("function", {}) if isinstance(c, dict) else {}
        args = fn.get("arguments")
        if isinstance(args, str):          # alguns modelos mandam string mesmo
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        chamadas.append({
            "id": f"o{i}",
            "nome": fn.get("name", ""),
            "args": args if isinstance(args, dict) else {},
        })

    return chamadas, [{"role": "assistant", "ferramentas": chamadas}]


async def ollama_modelos() -> list[str]:
    """Modelos baixados na máquina. Lista vazia quando o servidor não responde."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{config.OLLAMA_BASE_URL}/api/tags")
            if resp.status_code >= 400:
                return []
            modelos = resp.json().get("models")
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(modelos, list):
        return []
    return [
        str(m.get("name") or m.get("model") or "")
        for m in modelos
        if isinstance(m, dict)
    ]


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------

_STREAMS = {
    "ollama": _ollama_stream,
    "gemini": _gemini_stream,
    "groq": _groq_stream,
    "openrouter": _openrouter_stream,
}
_STRUCTURED = {
    "ollama": _ollama_structured,
    "gemini": _gemini_structured,
    "groq": _groq_structured,
}


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
    from . import saude

    configurados = _provedores()
    if preferir and preferir in configurados:
        configurados = [preferir] + [p for p in configurados if p != preferir]

    # Quem está de castigo sai da vez. Se TODOS estiverem, tentamos mesmo
    # assim — uma chance contra a parede é melhor que recusar de cara.
    disponiveis, de_castigo = saude.filtrar(configurados)
    if not disponiveis:
        disponiveis = [
            p for p in configurados if not saude._de(p).quebrado
        ] or configurados
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
                        saude.registrar_sucesso(provedor)
                        if usados is not None:
                            usados.append(provedor)
                    yield pedaco
                return

            except ProvedorQuebrado as exc:
                # Configuração errada: este provedor sai de circulação, mas a
                # conversa segue nos outros. Repetir não adiantaria.
                saude.registrar_falha(provedor, exc.tipo, str(exc))
                if entregou_algo:
                    raise BrainError(f"A resposta foi interrompida: {exc}") from exc
                problemas.append(str(exc))
                break

            except ProvedorIndisponivel as exc:
                saude.registrar_falha(provedor, exc.tipo, str(exc))
                if entregou_algo:
                    raise BrainError(f"A resposta foi interrompida: {exc}") from exc
                problemas.append(str(exc))
                if not ultima_tentativa:
                    await asyncio.sleep(ESPERA_BASE * (2**tentativa))
                    continue
                break  # próximo provedor

            except BrainError:
                raise

            except (httpx.TimeoutException, httpx.RequestError) as exc:
                if entregou_algo:
                    raise BrainError("A resposta foi interrompida no meio.") from exc
                tipo = "timeout" if isinstance(exc, httpx.TimeoutException) else "rede"
                saude.registrar_falha(provedor, tipo, f"{provedor}: {type(exc).__name__}")
                problemas.append(f"{provedor}: {type(exc).__name__}")
                if not ultima_tentativa:
                    await asyncio.sleep(ESPERA_BASE * (2**tentativa))
                    continue
                break

        if ultimo_provedor:
            # As tentativas repetidas do mesmo provedor viram uma linha só —
            # ler "gemini: cota" três vezes não ajuda ninguém.
            unicos = list(dict.fromkeys(problemas))
            texto = "Todos os provedores falharam:\n  - " + "\n  - ".join(unicos)
            if de_castigo:
                texto += (
                    "\n\n(descansando por falha recente: " + ", ".join(de_castigo) + ")"
                )
            precisam = saude.quebrados()
            if precisam:
                texto += (
                    "\n\nPrecisam de correção no .env: " + ", ".join(precisam)
                    + ". Depois de ajustar, reinicie o servidor."
                )
            texto += (
                "\n\nSe for limite de cota, ele reseta sozinho. "
                "Espere alguns minutos e tente de novo."
            )
            raise BrainError(texto)


async def com_ferramentas(
    system_prompt: str,
    messages: list[dict[str, object]],
    catalogo: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Uma rodada de conversa em que o modelo pode pedir ferramentas.

    Devolve (chamadas pedidas, mensagens a acrescentar ao histórico). Lista de
    chamadas vazia significa que o modelo não quis ferramenta nenhuma — hora
    de gerar a resposta final, em streaming.

    Não é streaming de propósito: pedidos de ferramenta chegam picotados no
    stream e remontá-los é frágil nos dois formatos. Como esta etapa não
    produz texto para o usuário ler, o ganho seria zero e o risco real.

    Quem entra na fila é decidido por CAPACIDADE, não por nome. Provedor que
    não sabe chamar função fica de fora — mandar mesmo assim produz o pior
    resultado possível: o modelo diz que fez e não fez.
    """
    from . import router, saude

    candidatos = router.quem_tem(router.TOOLS, _provedores())
    if not candidatos:
        raise BrainError(
            "Nenhum provedor disponível sabe usar ferramentas agora."
        )

    for provedor in candidatos:
        funcao = _FERRAMENTAS.get(provedor)
        if funcao is None:
            continue
        try:
            return await funcao(system_prompt, messages, catalogo)
        except ProvedorIndisponivel as exc:
            saude.registrar_falha(provedor, exc.tipo, str(exc))
            continue
        except httpx.HTTPError:
            continue
        except BrainError:
            raise
    raise BrainError("Nenhum provedor conseguiu responder agora.")


async def _groq_ferramentas(system_prompt, messages, catalogo):
    corpo = {
        "model": config.GROQ_MODEL,
        "messages": _groq_messages(system_prompt, messages),
        "temperature": 0.4,
        "tools": [{"type": "function", "function": f} for f in catalogo],
        "tool_choice": "auto",
    }
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(GROQ_URL, json=corpo, headers=headers)
        if resp.status_code >= 400:
            raise _classificar("groq", resp.status_code, resp.text, config.GROQ_MODEL)
        msg = resp.json()["choices"][0]["message"]

    brutas = msg.get("tool_calls") or []
    if not brutas:
        return [], []

    chamadas = []
    for c in brutas:
        fn = c.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        chamadas.append({"id": c.get("id", ""), "nome": fn.get("name", ""), "args": args})

    # Formato neutro: o histórico não fica preso ao fornecedor que atendeu.
    # Se a Groq cair no meio, o Gemini continua de onde ela parou.
    return chamadas, [{"role": "assistant", "ferramentas": chamadas}]


async def _gemini_ferramentas(system_prompt, messages, catalogo):
    corpo: dict[str, object] = {
        "contents": _gemini_contents(messages),
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "tools": [{"functionDeclarations": catalogo}],
        "generationConfig": {"temperature": 0.4},
    }
    url = f"{GEMINI_URL}/{config.MODEL}:generateContent"
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, json=corpo, headers=headers)
        if resp.status_code >= 400:
            raise _classificar("gemini", resp.status_code, resp.text, config.MODEL)
        dados = resp.json()

    partes = []
    try:
        partes = dados["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return [], []

    chamadas = []
    for i, parte in enumerate(partes):
        fc = parte.get("functionCall") if isinstance(parte, dict) else None
        if fc:
            chamadas.append({"id": f"g{i}", "nome": fc.get("name", ""), "args": fc.get("args") or {}})

    if not chamadas:
        return [], []
    return chamadas, [{"role": "assistant", "ferramentas": chamadas}]


# O OpenRouter não aparece aqui de propósito: ver o comentário em config.
_FERRAMENTAS = {
    "ollama": _ollama_ferramentas,
    "gemini": _gemini_ferramentas,
    "groq": _groq_ferramentas,
}


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
    from . import router

    for provedor in router.quem_tem(router.STRUCTURED, _provedores()):
        funcao = _STRUCTURED.get(provedor)
        if funcao is None:
            continue
        try:
            return await funcao(system_prompt, user_prompt, schema, temperature)
        except Exception:
            continue
    return None
