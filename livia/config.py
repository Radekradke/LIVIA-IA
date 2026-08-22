"""Configuração central. Tudo que muda de máquina para máquina vive aqui."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip() != "0"


# Onde ficam memórias, skills, personalidade e conversas.
# Em hospedagem, aponte para um disco persistente (ex: /data), senão tudo é
# apagado a cada reinício — ver README, seção "Colocar na internet".
DATA_DIR = Path(os.getenv("LIVIA_DATA_DIR", "").strip() or ROOT / "data")
MEMORY_DIR = DATA_DIR / "memory"
SKILLS_DIR = DATA_DIR / "skills"

# Lições: heurísticas e anti-patterns que a Livia deduziu das próprias
# experiências. Ficam em Markdown, como memória e skill, porque entram no
# prompt e o André precisa poder ler, corrigir e apagar o que ela concluiu.
LESSONS_DIR = DATA_DIR / "lessons"

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

# Terceiro provedor: OpenRouter. Só texto, de propósito — ele escolhe entre
# dezenas de modelos gratuitos e nem todos honram ferramentas ou saída
# estruturada do mesmo jeito. Oferecer e quebrar de forma imprevisível seria
# pior que não oferecer.
#
# O modelo padrão `openrouter/free` é o roteador automático deles: escolhe um
# gratuito compatível a cada pedido. Medido em 2026-08-18: ~3s, caiu no
# Nemotron 3 Ultra e respondeu em português correto.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("LIVIA_OPENROUTER_MODEL", "openrouter/free").strip()
OPENROUTER_BASE_URL = os.getenv(
    "LIVIA_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).strip().rstrip("/")
OPENROUTER_ENABLED = (
    os.getenv("LIVIA_OPENROUTER", "1").strip() != "0" and bool(OPENROUTER_API_KEY)
)

# Reservado para o futuro. Ligar isto sem testar modelo a modelo produz falha
# silenciosa: o modelo aceita o parâmetro e ignora a ferramenta.
OPENROUTER_TOOLS = os.getenv("LIVIA_OPENROUTER_TOOLS", "0").strip() != "0"

# --- IA local: Ollama ---------------------------------------------------
# O único provedor que não precisa de chave, não tem cota e não manda nada
# para fora. Desligado por padrão: ligar sem o servidor instalado só faria a
# Livia tentar uma conexão recusada antes de cair no provedor seguinte.
#
# Nenhum modelo é obrigatório aqui. Os padrões abaixo são sugestões que
# rodam em máquina modesta; troque à vontade pelo que você baixou.
OLLAMA_ENABLED = _flag("LIVIA_OLLAMA", "0")
OLLAMA_BASE_URL = os.getenv(
    "LIVIA_OLLAMA_BASE_URL", "http://127.0.0.1:11434"
).strip().rstrip("/")
OLLAMA_MODEL = os.getenv("LIVIA_OLLAMA_MODEL", "qwen3:8b").strip()
OLLAMA_FAST_MODEL = os.getenv("LIVIA_OLLAMA_FAST_MODEL", "qwen3:4b").strip()
OLLAMA_EMBED_MODEL = os.getenv("LIVIA_OLLAMA_EMBED_MODEL", "nomic-embed-text").strip()

# Ferramentas com modelo local: nem todo modelo do Ollama sabe chamar função,
# e os que não sabem ignoram o parâmetro em silêncio — o modelo responde
# "vou listar a pasta" e não lista nada. Por isso a capacidade é declarada
# à mão, e não fingida: com 0, o roteador simplesmente não manda ferramenta
# para o Ollama e usa outro provedor quando a tarefa exigir.
OLLAMA_TOOLS = _flag("LIVIA_OLLAMA_TOOLS", "0")

# Modelo local costuma pensar antes de falar; 180s de leitura é pouco quando
# a máquina é modesta e o modelo é grande.
OLLAMA_TIMEOUT = _float_env("LIVIA_OLLAMA_TIMEOUT", 300.0)

# --- Modo totalmente local ----------------------------------------------
# Com 1, NENHUM serviço externo é chamado: sem Gemini, sem Groq, sem
# OpenRouter, sem embeddings de nuvem. A web também nasce desligada aqui
# (dá para religar explicitamente com LIVIA_WEB=1).
LOCAL_ONLY = _flag("LIVIA_LOCAL_ONLY", "0")

# Provedores considerados locais. Serve para o filtro do modo local-only —
# é uma lista para o dia em que houver um segundo (llama.cpp, LM Studio).
LOCAL_PROVIDERS = ("ollama",)

# Ordem de tentativa. A Groq vem primeiro por três motivos medidos:
# responde em ~1,2s contra ~3,5s do Gemini, publica o quanto resta da cota em
# cabeçalho a cada resposta, e o limite dela (1000 pedidos/dia) é folgado.
#
# Isso não custa a leitura de links: quando a mensagem tem uma URL, o servidor
# manda aquela chamada específica para o Gemini, que é quem sabe abrir páginas
# (ver `preferir` em brain.stream). O roteamento é por capacidade, não por
# ordem fixa.
#
# O `ollama` entra na frente na lista padrão para o arranjo ser local-first
# assim que alguém ligar LIVIA_OLLAMA=1. Enquanto estiver desligado, ele é
# filtrado em router.disponiveis() e a ordem efetiva continua sendo a de
# antes — groq, gemini, openrouter.
PROVIDERS = [
    p.strip().lower()
    for p in os.getenv("LIVIA_PROVIDERS", "ollama,groq,gemini,openrouter").split(",")
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

HISTORY_TURNS = _int_env("LIVIA_HISTORY_TURNS", 20)
MEMORY_BUDGET_CHARS = _int_env("LIVIA_MEMORY_BUDGET", 24_000)
SKILL_BUDGET_CHARS = _int_env("LIVIA_SKILL_BUDGET", 30_000)
AUTO_LEARN = os.getenv("LIVIA_AUTO_LEARN", "1").strip() != "0"

# Acesso à web: ler links citados e buscar quando a pergunta pedir.
# No modo totalmente local ela nasce desligada — quem quiser as duas coisas
# (modelo local + web) põe LIVIA_WEB=1 de propósito.
WEB_ENABLED = _flag("LIVIA_WEB", "0" if LOCAL_ONLY else "1")
WEB_RESULTS = _int_env("LIVIA_WEB_RESULTS", 5)

# Quem faz a busca. `ddg` é o DuckDuckGo de sempre; `searxng` aponta para uma
# instância sua (roda em contêiner, não manda nada para terceiros); `auto`
# usa o SearXNG quando houver URL configurada e cai no DDG quando não houver.
SEARCH_PROVIDER = os.getenv("LIVIA_SEARCH_PROVIDER", "ddg").strip().lower() or "ddg"
SEARXNG_URL = os.getenv("LIVIA_SEARXNG_URL", "").strip().rstrip("/")

# Detecção automática de "essa pergunta precisa da web?".
# Custa UMA chamada de API a mais por mensagem — o que pesa na cota gratuita.
# Desligando, a web continua funcionando, mas só quando você pede: colando um
# link (leitura direta, sem custo extra) ou usando /buscar.
WEB_AUTO = os.getenv("LIVIA_WEB_AUTO", "1").strip() != "0"

# Ferramentas: deixa a Livia ler, escrever e listar arquivos, e calcular.
# TODA operação de arquivo fica confinada a WORKSPACE — caminho para fora é
# recusado. Aponte para uma pasta de projeto se quiser que ela trabalhe nela,
# ciente de que ela poderá sobrescrever arquivos lá dentro (com cópia de
# segurança automática ao lado de cada arquivo alterado).
TOOLS_ENABLED = os.getenv("LIVIA_TOOLS", "1").strip() != "0"
WORKSPACE = Path(os.getenv("LIVIA_WORKSPACE", "").strip() or DATA_DIR / "workspace")
TOOLS_MAX_ROUNDS = _int_env("LIVIA_TOOLS_ROUNDS", 5)

# --- Embeddings ---------------------------------------------------------
# Quem transforma texto em vetor, que é o que faz a busca por significado
# funcionar (memória, lições e biblioteca).
#
#   ollama   só local, nunca sai da máquina
#   gemini   nuvem, gratuito, precisa de chave
#   auto     prefere o local e cai no Gemini quando ele não estiver de pé
EMBED_PROVIDER = os.getenv("LIVIA_EMBED_PROVIDER", "auto").strip().lower() or "auto"

# Dimensão dos vetores do Gemini. Ele entrega 3072, mas é treinado para que
# truncar preserve o essencial — 768 ocupa um quarto do espaço. O Ollama
# entrega o que o modelo dele der, e esse número é guardado no índice.
EMBED_DIMENSOES = _int_env("LIVIA_EMBED_DIM", 768)

# --- Memória adaptativa -------------------------------------------------
# Recuperação semântica: em vez de despejar TODA a memória em todo prompt,
# busca as que têm a ver com a pergunta. Com 0, volta ao comportamento
# antigo (tudo, até estourar o orçamento, aí o índice).
SEMANTIC_MEMORY = _flag("LIVIA_SEMANTIC_MEMORY", "1")

# Orçamento por tipo de contexto. Contar só caracteres no total não serve:
# um documento gigante engoliria a memória inteira sem ninguém perceber.
MEMORY_MAX_ITEMS = _int_env("LIVIA_MEMORY_MAX_ITEMS", 8)
EXPERIENCE_MAX_ITEMS = _int_env("LIVIA_EXPERIENCE_MAX_ITEMS", 5)
SKILL_MAX_ITEMS = _int_env("LIVIA_SKILL_MAX_ITEMS", 4)
LESSON_MAX_ITEMS = _int_env("LIVIA_LESSON_MAX_ITEMS", 4)
RAG_MAX_CHUNKS = _int_env("LIVIA_RAG_MAX_CHUNKS", 5)

# Acima disto, duas memórias falam da mesma coisa e viram uma só.
MEMORY_DUPLICATE_THRESHOLD = _float_env("LIVIA_MEMORY_DUPLICATE", 0.88)
# Abaixo disto, a memória não tem nada a ver com a pergunta.
MEMORY_RELEVANCE_THRESHOLD = _float_env("LIVIA_MEMORY_RELEVANCE", 0.28)

# Quantas experiências parecidas e concordantes até virar heurística.
# Uma só é anedota; o padrão precisa se repetir para valer como regra.
LEARNING_MIN_EXPERIENCES = _int_env("LIVIA_LEARNING_MIN_EXPERIENCES", 3)

# Skills nascidas de padrão observado ficam como CANDIDATAS e esperam o
# André aprovar. Ligar isto deixa ela promover sozinha — não recomendado.
SKILL_AUTO_APPROVE = _flag("LIVIA_SKILL_AUTO_APPROVE", "0")

# Registrar cada tarefa como experiência (o que foi tentado, deu certo ou não).
EXPERIENCE_ENABLED = _flag("LIVIA_EXPERIENCE", "1")

for _d in (DATA_DIR, MEMORY_DIR, SKILLS_DIR, LESSONS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
