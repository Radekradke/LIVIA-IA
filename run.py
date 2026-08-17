"""Sobe a Livia. Use:  python run.py"""

from __future__ import annotations

import sys
import webbrowser
from threading import Timer

import uvicorn

from livia import auth, config

LINHA = "=" * 70


def _so_local() -> bool:
    return config.HOST in ("127.0.0.1", "localhost", "::1")


def _checar_seguranca() -> bool:
    """Sem senha, ninguém escuta fora do 127.0.0.1. Devolve False para abortar.

    Esta trava existe porque a falha aqui é silenciosa e cara: o servidor sobe,
    tudo parece certo, e qualquer um na rede (ou na internet, se houver túnel)
    lê suas conversas e gasta sua cota. É melhor recusar a subir.
    """
    if _so_local() or auth.protegido():
        return True

    print(LINHA)
    print("  RECUSANDO SUBIR — servidor exposto sem senha")
    print()
    print(f"  LIVIA_HOST={config.HOST} aceita conexões de fora desta máquina,")
    print("  e não há senha configurada. Qualquer um que alcançar o endereço")
    print("  leria suas conversas e memórias, e gastaria sua cota de API.")
    print()
    print("  Ponha uma senha no .env:")
    print()
    print(f"      LIVIA_PASSWORD={auth.sugerir_senha()}")
    print()
    print("  (essa aí foi gerada agora, pode usar)")
    print()
    print("  Ou volte para LIVIA_HOST=127.0.0.1 para uso só nesta máquina.")
    print(LINHA)
    return False


def main() -> int:
    if not _checar_seguranca():
        return 1

    url = f"http://{config.HOST}:{config.PORT}"

    if not config.GEMINI_API_KEY and not config.GROQ_API_KEY:
        print(LINHA)
        print("  Nenhuma chave de API configurada.")
        print()
        print("  1. Chave gratuita em https://aistudio.google.com/apikey")
        print("  2. Copie .env.example para .env  (copie, não mova)")
        print("  3. Cole a chave em GEMINI_API_KEY")
        print()
        print("  A interface abre mesmo assim, mas o chat vai dar erro.")
        print(LINHA)
        print()

    print(f"  {config.ASSISTANT_NAME} rodando em {url}")
    print(f"  Provedores: {' -> '.join(config.PROVIDERS)}")
    print(f"  Modelo: {config.MODEL}")
    print(f"  Senha: {'sim' if auth.protegido() else 'NÃO (só acesso local)'}")
    print(f"  Dados: {config.DATA_DIR}")
    print("  Ctrl+C para parar.\n")

    if _so_local():
        Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "livia.server:app",
        host=config.HOST,
        port=config.PORT,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
