"""Configuração do sidecar — e a blindagem contra o vazamento silencioso.

O Cognee lê a configuração dele de variáveis de ambiente próprias
(`LLM_PROVIDER`, `EMBEDDING_PROVIDER`, ...). Este módulo traduz as variáveis
da Livia (`LIVIA_KNOWLEDGE_*`) para as dele, e é o único lugar do projeto que
conhece esses nomes.

A ARMADILHA QUE ISTO EXISTE PARA FECHAR
---------------------------------------
A documentação do Cognee diz, textualmente:

    "If you configure only LLM or only embeddings, the other defaults to
     OpenAI."

Ou seja: alguém põe `LLM_PROVIDER=ollama`, esquece o embedding, e o conteúdo
dos documentos vai para a OpenAI sem nenhum aviso. Num projeto cujo contrato
é "nada sai da máquina", esse é o pior bug possível — silencioso, na ingestão,
longe dos olhos, e só descoberto quando já vazou.

Por isso `conferir()` roda ANTES de qualquer ingestão e recusa subir quando
os dois lados não estiverem explicitamente locais. Preferimos o serviço não
iniciar a ele iniciar mandando documento para fora.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent.parent

# Onde o grafo e o registro de trechos vivem. Fora do container efêmero:
# reconstruir um grafo custa horas de CPU.
DATA_DIR = Path(
    os.getenv("LIVIA_KNOWLEDGE_DATA", "").strip() or RAIZ / "data" / "knowledge"
)

HOST = os.getenv("LIVIA_KNOWLEDGE_HOST", "127.0.0.1").strip()
PORT = int(os.getenv("LIVIA_KNOWLEDGE_PORT", "8110"))

# O modo local da Livia vale aqui também. O sidecar é um processo separado,
# mas o contrato é do sistema inteiro.
LOCAL_ONLY = os.getenv("LIVIA_LOCAL_ONLY", "0").strip() != "0"

PROVIDER = os.getenv("LIVIA_KNOWLEDGE_PROVIDER", "ollama").strip().lower()

LLM_MODEL = os.getenv("LIVIA_KNOWLEDGE_LLM_MODEL", "llama3.1:8b").strip()
LLM_ENDPOINT = os.getenv(
    "LIVIA_KNOWLEDGE_LLM_ENDPOINT", "http://127.0.0.1:11434/v1"
).strip()

EMBED_MODEL = os.getenv("LIVIA_KNOWLEDGE_EMBED_MODEL", "nomic-embed-text").strip()
EMBED_ENDPOINT = os.getenv(
    "LIVIA_KNOWLEDGE_EMBED_ENDPOINT", "http://127.0.0.1:11434"
).strip()

# Precisa bater com o modelo. `nomic-embed-text` são 768; declarar errado faz
# o Cognee gravar vetor de tamanho inesperado e a busca devolver lixo.
EMBED_DIM = int(os.getenv("LIVIA_KNOWLEDGE_EMBED_DIM", "768"))

# O Cognee exige um tokenizador declarado mesmo usando Ollama (é uma quirk
# conhecida da versão atual). Não baixa modelo: só nomeia o vocabulário.
HF_TOKENIZER = os.getenv(
    "LIVIA_KNOWLEDGE_TOKENIZER", "sentence-transformers/all-MiniLM-L6-v2"
).strip()

# Chave só para provedor de nuvem. Vazia no modo local — e o Cognee aceita
# um valor simbólico quando o provedor é Ollama.
API_KEY = os.getenv("LIVIA_KNOWLEDGE_API_KEY", "").strip()

LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}

PROVEDORES_LOCAIS = {"ollama", "llama_cpp", "lmstudio", "local"}


def _local(url: str) -> bool:
    try:
        return (urlparse(url).hostname or "").lower() in LOOPBACK
    except ValueError:
        return False


def conferir() -> list[str]:
    """Motivos para NÃO subir. Lista vazia = pode.

    Só bloqueia o que é perigoso de verdade. Configuração de nuvem fora do
    modo local é escolha legítima do André, e não cabe a este código impedir.
    """
    problemas: list[str] = []

    if not LOCAL_ONLY:
        return problemas

    if PROVIDER not in PROVEDORES_LOCAIS:
        problemas.append(
            f"LIVIA_LOCAL_ONLY=1 mas LIVIA_KNOWLEDGE_PROVIDER={PROVIDER!r} não é "
            f"local. Use um destes: {', '.join(sorted(PROVEDORES_LOCAIS))}."
        )
    if not _local(LLM_ENDPOINT):
        problemas.append(
            f"LIVIA_LOCAL_ONLY=1 mas o LLM do grafo está em {LLM_ENDPOINT}, "
            "que não é esta máquina."
        )
    if not _local(EMBED_ENDPOINT):
        problemas.append(
            f"LIVIA_LOCAL_ONLY=1 mas os embeddings do grafo estão em "
            f"{EMBED_ENDPOINT}, que não é esta máquina."
        )
    if API_KEY and not API_KEY.lower().startswith(("ollama", "local", "nao", "none")):
        problemas.append(
            "LIVIA_LOCAL_ONLY=1 e há uma LIVIA_KNOWLEDGE_API_KEY de nuvem "
            "configurada. Apague-a ou desligue o modo local."
        )
    return problemas


def aplicar_no_ambiente() -> dict[str, str]:
    """Traduz a configuração da Livia para as variáveis do Cognee.

    Os DOIS lados são sempre escritos, mesmo quando o valor é o padrão. É essa
    a defesa contra o "o outro vira OpenAI": nunca deixamos um dos dois em
    branco para o Cognee decidir sozinho.
    """
    valores = {
        "LLM_PROVIDER": PROVIDER,
        "LLM_MODEL": LLM_MODEL,
        "LLM_ENDPOINT": LLM_ENDPOINT,
        "LLM_API_KEY": API_KEY or ("ollama" if PROVIDER == "ollama" else ""),
        "EMBEDDING_PROVIDER": PROVIDER,
        "EMBEDDING_MODEL": EMBED_MODEL,
        "EMBEDDING_ENDPOINT": (
            f"{EMBED_ENDPOINT.rstrip('/')}/api/embeddings"
            if PROVIDER == "ollama" and "/api/" not in EMBED_ENDPOINT
            else EMBED_ENDPOINT
        ),
        "EMBEDDING_API_KEY": API_KEY or ("ollama" if PROVIDER == "ollama" else ""),
        "EMBEDDING_DIMENSIONS": str(EMBED_DIM),
        "HUGGINGFACE_TOKENIZER": HF_TOKENIZER,
        # O Cognee guarda o grafo e os vetores debaixo destes caminhos.
        "COGNEE_SYSTEM_DIRECTORY": str(DATA_DIR / "sistema"),
        "COGNEE_DATA_DIRECTORY": str(DATA_DIR / "dados"),
        # Sem telemetria: um projeto que promete privacidade não manda
        # estatística de uso para lugar nenhum.
        "TELEMETRY_DISABLED": "1",
    }

    for chave, valor in valores.items():
        # `setdefault`: quem já exportou a variável à mão manda mais que nós.
        os.environ.setdefault(chave, valor)
    return valores


def resumo() -> dict[str, object]:
    """Retrato seguro para o /health. Nunca inclui chave."""
    return {
        "provider": PROVIDER,
        "llm_model": LLM_MODEL,
        "llm_endpoint": LLM_ENDPOINT,
        "embed_model": EMBED_MODEL,
        "embed_endpoint": EMBED_ENDPOINT,
        "embed_dim": EMBED_DIM,
        "local_only": LOCAL_ONLY,
        "data_dir": str(DATA_DIR),
    }
