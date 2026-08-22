"""Sobe o Knowledge Engine.  Use:  python -m services.knowledge.run

Escuta em 127.0.0.1 por padrão, e essa escolha não é negociável por descuido:
o serviço não tem autenticação nenhuma, então qualquer um que o alcançasse
leria o conteúdo dos documentos do André. Para expor de propósito é preciso
escrever LIVIA_KNOWLEDGE_HOST=0.0.0.0 à mão — e aí a mensagem abaixo avisa,
alto, o que aquilo significa.
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from . import config
from .cognee_engine import dialeto, instalado, versao

LINHA = "=" * 70


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if not instalado():
        print(LINHA)
        print("  O Cognee não está instalado.")
        print()
        print("      pip install -r requirements-knowledge.txt")
        print()
        print("  O serviço sobe assim mesmo e o /health explica o que falta —")
        print("  mas nenhum grafo será construído até você instalar.")
        print(LINHA)
        print()

    problemas = config.conferir()
    if problemas:
        # Recusa, não aviso. Subir aqui significaria mandar documento para
        # fora num modo cujo contrato é justamente que nada sai.
        print(LINHA)
        print("  RECUSANDO SUBIR — o modo totalmente local seria furado")
        print()
        for p in problemas:
            print(f"    - {p}")
        print()
        print("  Ajuste o .env ou desligue LIVIA_LOCAL_ONLY.")
        print(LINHA)
        return 1

    if config.HOST not in ("127.0.0.1", "localhost", "::1"):
        print(LINHA)
        print(f"  ATENÇÃO — escutando em {config.HOST}, não só nesta máquina.")
        print()
        print("  Este serviço NÃO tem senha. Quem alcançar este endereço lê o")
        print("  conteúdo dos seus documentos. Só faça isso atrás de uma rede")
        print("  em que você confia.")
        print(LINHA)
        print()

    print(f"  Knowledge Engine em http://{config.HOST}:{config.PORT}")
    print(f"  Motor: cognee {versao() or '(não instalado)'}"
          + (f" · api {dialeto()}" if dialeto() else ""))
    print(f"  Modelo do grafo: {config.LLM_MODEL} em {config.LLM_ENDPOINT}")
    print(f"  Embeddings: {config.EMBED_MODEL} ({config.EMBED_DIM}d)")
    print(f"  Modo local: {'SIM — nada sai daqui' if config.LOCAL_ONLY else 'não'}")
    print(f"  Dados: {config.DATA_DIR}")
    print("  Ctrl+C para parar.\n")

    uvicorn.run(
        "services.knowledge.app:app",
        host=config.HOST,
        port=config.PORT,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
