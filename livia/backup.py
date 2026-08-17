"""Backup e restauração de tudo que a Livia sabe.

Existe por um motivo concreto: hospedagem gratuita costuma ter disco efêmero.
O app sobe, funciona por dias, e numa atualização qualquer o disco volta ao
estado inicial — memórias, skills, personalidade e conversas somem sem aviso.

Com isto você baixa um .zip com tudo e restaura depois. Não é elegante, mas é
a diferença entre perder meses de contexto e perder cinco minutos.

O zip NÃO leva o .env: chaves de API não devem passear em arquivo de backup.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from . import config

# Só o que é seu. O banco entra inteiro; os .md entram um a um.
_PASTAS = ("memory", "skills")
_ARQUIVOS = ("personalidade.md", "livia.db")


def exportar() -> tuple[bytes, str]:
    """Devolve (conteúdo do zip, nome sugerido do arquivo)."""
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome in _ARQUIVOS:
            caminho = config.DATA_DIR / nome
            if caminho.exists():
                zf.write(caminho, nome)

        for pasta in _PASTAS:
            origem = config.DATA_DIR / pasta
            if not origem.exists():
                continue
            for arquivo in sorted(origem.glob("*.md")):
                zf.write(arquivo, f"{pasta}/{arquivo.name}")

        zf.writestr(
            "LEIA-ME.txt",
            "Backup da Livia\n"
            f"Gerado em: {datetime.now().isoformat(timespec='seconds')}\n\n"
            "Para restaurar: painel lateral > aba Jeito > Restaurar backup,\n"
            "ou descompacte por cima da pasta data/ com o servidor parado.\n\n"
            "Este arquivo NAO contem chaves de API (elas ficam so no .env).\n",
        )

    carimbo = datetime.now().strftime("%Y-%m-%d-%H%M")
    return buffer.getvalue(), f"livia-backup-{carimbo}.zip"


def importar(dados: bytes) -> dict[str, int]:
    """Restaura um zip gerado por `exportar`. Devolve o que foi restaurado.

    Sobrescreve o que existir com o mesmo nome, e mantém o que não estiver no
    backup — restaurar nunca apaga memória que você criou depois.
    """
    contagem = {"memorias": 0, "skills": 0, "personalidade": 0, "conversas": 0}

    with zipfile.ZipFile(io.BytesIO(dados)) as zf:
        for item in zf.namelist():
            # Defesa contra zip malicioso: nada de subir de diretório.
            if item.startswith("/") or ".." in item.replace("\\", "/").split("/"):
                continue

            if item == "personalidade.md":
                destino = config.DATA_DIR / item
                contagem["personalidade"] = 1
            elif item == "livia.db":
                destino = config.DATA_DIR / item
                contagem["conversas"] = 1
            elif item.startswith("memory/") and item.endswith(".md"):
                destino = config.DATA_DIR / item
                contagem["memorias"] += 1
            elif item.startswith("skills/") and item.endswith(".md"):
                destino = config.DATA_DIR / item
                contagem["skills"] += 1
            else:
                continue  # LEIA-ME.txt e qualquer coisa inesperada

            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(zf.read(item))

    return contagem
