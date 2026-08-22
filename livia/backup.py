"""Backup e restauração de tudo que a Livia sabe.

Existe por um motivo concreto: hospedagem gratuita costuma ter disco efêmero.
O app sobe, funciona por dias, e numa atualização qualquer o disco volta ao
estado inicial — memórias, skills, personalidade e conversas somem sem aviso.

Com isto você baixa um .zip com tudo e restaura depois. Não é elegante, mas é
a diferença entre perder meses de contexto e perder cinco minutos.

O zip NÃO leva o .env: chaves de API não devem passear em arquivo de backup.

O QUE ENTRA E O QUE FICA DE FORA
--------------------------------
Entra tudo que é ORIGINAL: memórias, skills, lições, personalidade e o banco
(que carrega as conversas, as experiências e as skills candidatas).

Da biblioteca entram o `meta.json` e o `trechos.jsonl` — o texto —, mas NÃO o
`vetores.npy`. O motivo é tamanho: uma biblioteca com alguns livros passa
fácil de 50 MB de vetores, e eles são inteiramente reconstrutíveis a partir
dos trechos, que comprimem muito bem. Depois de restaurar, o botão
"reconstruir" de cada documento refaz os vetores em segundos.

Índice de memória também fica de fora do zip por estar dentro do próprio
livia.db — e é reconstruído sozinho na primeira abertura de qualquer jeito.

O GRAFO DE CONHECIMENTO TAMBÉM NÃO VEM
--------------------------------------
Pela mesma regra: ele é INTEIRAMENTE reconstruível a partir dos trechos, que
já estão aqui. Um grafo de alguns documentos ocupa centenas de MB e levaria o
zip a ficar impraticável, para guardar algo que a ação "reconstruir
conhecimento" refaz sozinha.

O que importa é que o metadado suficiente para reconstruir venha — e vem: os
`trechos.jsonl` e os `meta.json` (com `knowledge_status`) estão no pacote.
Depois de restaurar, o painel mostra os documentos sem grafo e oferece
construir.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

from . import config

# Só o que é seu. O banco entra inteiro; os .md entram um a um.
_PASTAS = ("memory", "skills", "lessons")
_ARQUIVOS = ("personalidade.md", "livia.db")

# Da biblioteca, só o que não dá para recalcular. Ver o cabeçalho.
_BIBLIOTECA_ARQUIVOS = ("meta.json", "trechos.jsonl")


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

        # Biblioteca: texto sim, vetores não.
        origem = config.DATA_DIR / "biblioteca"
        if origem.exists():
            for pasta in sorted(origem.iterdir()):
                if not pasta.is_dir():
                    continue
                for nome in _BIBLIOTECA_ARQUIVOS:
                    arquivo = pasta / nome
                    if arquivo.exists():
                        zf.write(arquivo, f"biblioteca/{pasta.name}/{nome}")

        zf.writestr(
            "LEIA-ME.txt",
            "Backup da Livia\n"
            f"Gerado em: {datetime.now().isoformat(timespec='seconds')}\n\n"
            "Para restaurar: painel lateral > aba Jeito > Restaurar backup,\n"
            "ou descompacte por cima da pasta data/ com o servidor parado.\n\n"
            "O que tem aqui dentro:\n"
            "  memory/      suas memorias\n"
            "  skills/      os procedimentos que voce ensinou\n"
            "  lessons/     o que ela deduziu das proprias experiencias\n"
            "  biblioteca/  o texto dos documentos (sem os vetores)\n"
            "  livia.db     conversas, experiencias e indices\n\n"
            "Os vetores da biblioteca NAO vem no zip: sao grandes e da para\n"
            "recalcular a partir do texto. Depois de restaurar, use o botao\n"
            "'reconstruir' de cada documento na aba Livros.\n\n"
            "Este arquivo NAO contem chaves de API (elas ficam so no .env).\n",
        )

    carimbo = datetime.now().strftime("%Y-%m-%d-%H%M")
    return buffer.getvalue(), f"livia-backup-{carimbo}.zip"


def importar(dados: bytes) -> dict[str, int]:
    """Restaura um zip gerado por `exportar`. Devolve o que foi restaurado.

    Sobrescreve o que existir com o mesmo nome, e mantém o que não estiver no
    backup — restaurar nunca apaga memória que você criou depois.
    """
    contagem = {
        "memorias": 0, "skills": 0, "licoes": 0,
        "personalidade": 0, "conversas": 0, "documentos": 0,
    }

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
            elif item.startswith("lessons/") and item.endswith(".md"):
                destino = config.DATA_DIR / item
                contagem["licoes"] += 1
            elif item.startswith("biblioteca/") and item.endswith(
                (".json", ".jsonl")
            ):
                destino = config.DATA_DIR / item
                if item.endswith("meta.json"):
                    contagem["documentos"] += 1
            else:
                continue  # LEIA-ME.txt e qualquer coisa inesperada

            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(zf.read(item))

    # O índice acompanha os arquivos restaurados. Sem isto, uma memória
    # recuperada existiria em disco e não apareceria na busca até alguém
    # reiniciar o servidor.
    try:
        from . import memoria
        from .store import COLECOES

        for colecao in COLECOES:
            memoria.sincronizar(colecao)
    except Exception:
        pass  # índice é derivado; ele se refaz na próxima abertura

    return contagem
