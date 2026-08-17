"""Configuração central. Tudo que muda de máquina para máquina vive aqui."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# Onde ficam memórias, skills, personalidade e conversas.
# Em hospedagem, aponte para um disco persistente (ex: /data), senão tudo é
# apagado a cada reinício — ver README, seção "Colocar na internet".
DATA_DIR = Path(os.getenv("LIVIA_DATA_DIR", "").strip() or ROOT / "data")
MEMORY_DIR = DATA_DIR / "memory"
SKILLS_DIR = DATA_DIR / "skills"
DB_PATH = DATA_DIR / "livia.db"
WEB_DIR = ROOT / "web"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("LIVIA_MODEL", "gemini-3.6-flash").strip()
FAST_MODEL = os.getenv("LIVIA_FAST_MODEL", "gemini-3.5-flash-lite").strip()

# Provedor reserva. Se o Gemini cair ou estourar a cota, a conversa continua
# aqui sem você perceber. Chave gratuita em https://console.groq.com/keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("LIVIA_GROQ_MODEL", "openai/gpt-oss-120b").strip()
GROQ_FAST_MODEL = os.getenv("LIVIA_GROQ_FAST_MODEL", "openai/gpt-oss-20b").strip()

# Ordem de tentativa. O Gemini vem primeiro porque é o único que sabe abrir
# links sozinho; a Groq é bem mais rápida. Inverta se preferir velocidade a
# leitura de páginas.
PROVIDERS = [
    p.strip().lower()
    for p in os.getenv("LIVIA_PROVIDERS", "gemini,groq").split(",")
    if p.strip()
]
USER_NAME = os.getenv("LIVIA_USER", "").strip()

# O nome dela. Troque à vontade — a interface, o ícone da aba e o prompt
# se ajustam sozinhos ao que estiver aqui.
ASSISTANT_NAME = os.getenv("LIVIA_NAME", "Livia").strip() or "Livia"

HOST = os.getenv("LIVIA_HOST", "127.0.0.1").strip()

# Hospedagens injetam a porta na variável PORT e esperam que você a use.
# Ignorar isso é o motivo nº 1 de "deploy funcionou mas o site não abre".
PORT = int(os.getenv("PORT") or os.getenv("LIVIA_PORT") or 8100)

# Senha de acesso. Vazia = sem proteção, e nesse caso o servidor se recusa a
# escutar fora de 127.0.0.1 (ver run.py). Obrigatória para expor na internet.
PASSWORD = os.getenv("LIVIA_PASSWORD", "").strip()

# HTTPS não é mais configurado à mão: o servidor detecta pelo próprio pedido
# (ver _via_https em server.py). Marcar isso errado causava loop de login —
# entra, é redirecionado, cai na tela de senha de novo, sem mensagem nenhuma.
# A variável LIVIA_HTTPS continua sendo aceita e ignorada, para não quebrar
# quem já a tinha no .env.


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


HISTORY_TURNS = _int_env("LIVIA_HISTORY_TURNS", 20)
MEMORY_BUDGET_CHARS = _int_env("LIVIA_MEMORY_BUDGET", 24_000)
SKILL_BUDGET_CHARS = _int_env("LIVIA_SKILL_BUDGET", 30_000)
AUTO_LEARN = os.getenv("LIVIA_AUTO_LEARN", "1").strip() != "0"

# Acesso à web: ler links citados e buscar quando a pergunta pedir.
WEB_ENABLED = os.getenv("LIVIA_WEB", "1").strip() != "0"
WEB_RESULTS = _int_env("LIVIA_WEB_RESULTS", 5)

# Detecção automática de "essa pergunta precisa da web?".
# Custa UMA chamada de API a mais por mensagem — o que pesa na cota gratuita.
# Desligando, a web continua funcionando, mas só quando você pede: colando um
# link (leitura direta, sem custo extra) ou usando /buscar.
WEB_AUTO = os.getenv("LIVIA_WEB_AUTO", "1").strip() != "0"

for _d in (DATA_DIR, MEMORY_DIR, SKILLS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
