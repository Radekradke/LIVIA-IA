"""Voz → texto, pelo Whisper do Groq.

A mesma chave do Groq que a Livia já usa pra conversar serve pra transcrever:
o plano grátis tem cota diária de áudio de sobra pra ideia falada e recado.
Nada de modelo rodando na VM (1 GB de RAM não aguenta Whisper decente) e nada
de áudio guardado: chega, vira texto, some.

O navegador manda o que o microfone gravou como está: webm/opus no Chrome e
no Android, mp4/aac no Safari do iPhone. O Whisper aceita os dois.
"""

from __future__ import annotations

import logging

import httpx

from . import config

log = logging.getLogger("livia.voz")

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELO = "whisper-large-v3-turbo"
LIMITE = 24 * 1024 * 1024  # o grátis aceita 25 MB; fica uma folga

_EXTENSOES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
              "audio/aac": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav"}


class VozError(Exception):
    """Algo que dá pra dizer ao André em português."""


def disponivel() -> bool:
    return bool(config.GROQ_API_KEY)


def _vocabulario() -> str:
    """Palavras que o Whisper não tem como adivinhar a grafia.

    O "prompt" do Whisper não é instrução: é texto que ele trata como o que
    veio antes do áudio. Pôr ali os nomes das ideias quentes faz "Ghostline"
    sair Ghostline e não "góstilaine".
    """
    nomes = ["Livia", "Nebulosa", config.USER_NAME or "André"]
    try:
        from . import nebulosa

        quentes = sorted(nebulosa.listar(incluir_arquivadas=False), key=lambda i: -float(i["calor"]))[:12]
        nomes += [str(i["titulo"])[:40] for i in quentes]
        nomes += [str(s["nome"]) for s in nebulosa.sistemas()[:8]]
    except Exception:
        pass
    return ", ".join(dict.fromkeys(n for n in nomes if n))[:600]


async def transcrever(audio: bytes, tipo: str = "audio/webm") -> str:
    if not disponivel():
        raise VozError("Pra transcrever, a Livia precisa da GROQ_API_KEY no .env.")
    if not audio:
        raise VozError("Não chegou áudio nenhum.")
    if len(audio) > LIMITE:
        raise VozError("Áudio grande demais — o limite é uns 24 MB (mais ou menos 25 min).")
    tipo = (tipo or "audio/webm").split(";")[0].strip().lower()
    nome = f"fala.{_EXTENSOES.get(tipo, 'webm')}"
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                URL, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
                files={"file": (nome, audio, tipo)},
                data={"model": MODELO, "language": "pt", "response_format": "json",
                      "temperature": "0", "prompt": _vocabulario()},
            )
    except httpx.HTTPError as exc:
        raise VozError(f"Não consegui falar com o Groq: {exc.__class__.__name__}") from exc
    if r.status_code == 429:
        raise VozError("Cota de voz do Groq estourou por agora. Tenta de novo daqui a pouco.")
    if r.status_code >= 400:
        log.warning("[voz] groq %s: %s", r.status_code, r.text[:300])
        raise VozError(f"O Groq recusou o áudio ({r.status_code}).")
    texto = str((r.json() or {}).get("text") or "").strip()
    if not texto:
        raise VozError("Não entendi nada no áudio — tava mudo?")
    return texto


# --------------------------------------------------------------------------
# Texto → voz (Edge-TTS)
# --------------------------------------------------------------------------

LIMITE_FALA = 1500  # caracteres por pedido; o navegador manda frase a frase


def fala_disponivel() -> bool:
    if not config.VOZ_EDGE:
        return False
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


async def falar(texto: str, voz: str = "francisca", velocidade: int = 4):
    """Gera a fala e devolve os pedaços de mp3 conforme chegam.

    Streaming de verdade: o primeiro áudio sai antes de a frase inteira
    ficar pronta, então o navegador começa a tocar quase na hora.
    """
    if not fala_disponivel():
        raise VozError("Voz Edge desligada ou não instalada.")
    texto = (texto or "").strip()[:LIMITE_FALA]
    if not texto:
        raise VozError("Nada pra falar.")
    import edge_tts

    nome = config.VOZES_EDGE.get(voz, config.VOZES_EDGE["francisca"])
    velocidade = max(-30, min(30, int(velocidade)))
    comunicar = edge_tts.Communicate(texto, nome, rate=f"{velocidade:+d}%",
                                     connect_timeout=8, receive_timeout=30)
    try:
        async for pedaco in comunicar.stream():
            if pedaco.get("type") == "audio" and pedaco.get("data"):
                yield pedaco["data"]
    except Exception as exc:  # rede, bloqueio da Microsoft, mudança de protocolo
        log.warning("[voz] edge-tts falhou: %s", exc.__class__.__name__)
        raise VozError("A voz da Microsoft não respondeu.") from exc
