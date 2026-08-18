"""A ponte entre a pasta de trabalho e o navegador.

As fases 5 e 6 já produziam e liam arquivos, mas eles ficavam só no disco do
servidor. Rodando na Oracle isso é o mesmo que não existir: a Livia diz
"criei o relatório" e o André não tem como pegar.

DUAS DECISÕES DE SEGURANÇA
--------------------------
1. TODO download sai como anexo, com `application/octet-stream`. Nunca
   inline. A Livia escreve arquivos `.html` — servir um deles inline
   executaria o JavaScript dele NA MESMA ORIGEM do painel, com acesso ao
   cookie de sessão. Seria XSS armazenado onde quem escreve o conteúdo é um
   modelo que leu a internet.

2. Link simbólico é conferido depois de resolvido. `rglob` atravessa link de
   pasta sem avisar; um link para `/etc` dentro da pasta de trabalho
   listaria o sistema inteiro. Confinamento que confia na estrutura da pasta
   não é confinamento.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .ferramentas import FerramentaError, _resolver, _tamanho, raiz

LIMITE_ITENS = 500

# Extensões que a interface marca com ícone próprio. O resto vira genérico.
FAMILIAS = {
    "documento": ("docx", "pdf", "odt", "rtf"),
    "planilha": ("xlsx", "xlsm", "csv", "tsv", "ods"),
    "texto": ("txt", "md", "log"),
    "codigo": ("py", "js", "ts", "json", "html", "css", "sql", "yml", "yaml"),
    "imagem": ("png", "jpg", "jpeg", "gif", "webp", "svg"),
}


def familia(nome: str) -> str:
    extensao = Path(nome).suffix.lower().lstrip(".")
    for rotulo, extensoes in FAMILIAS.items():
        if extensao in extensoes:
            return rotulo
    return "arquivo"


def _descrever(alvo: Path, base: Path) -> dict[str, object]:
    dados = alvo.stat()
    relativo = alvo.relative_to(base).as_posix()
    return {
        "caminho": relativo,
        "nome": alvo.name,
        "pasta": alvo.parent.relative_to(base).as_posix() if alvo.parent != base else "",
        "bytes": dados.st_size,
        "tamanho": _tamanho(dados.st_size),
        "modificado": datetime.fromtimestamp(dados.st_mtime).isoformat(timespec="seconds"),
        "familia": familia(alvo.name),
        # Cópia de segurança de uma sobrescrita. A interface mostra discreta,
        # para não competir com o arquivo de verdade.
        "backup": ".antes-" in alvo.name,
    }


def listar(limite: int = LIMITE_ITENS) -> list[dict[str, object]]:
    """Arquivos da pasta de trabalho, do mais recente para o mais antigo.

    Mais recente primeiro porque o caso comum é pegar o que ela acabou de
    fazer.
    """
    base = raiz()
    itens: list[dict[str, object]] = []

    for alvo in base.rglob("*"):
        if len(itens) >= limite:
            break
        nome = alvo.name
        # `.relatorio.docx.parcial` é escrita em andamento; `.git` e afins não
        # interessam a ninguém.
        if nome.startswith(".") or any(p.startswith(".") for p in alvo.parts):
            continue
        try:
            if not alvo.is_file():
                continue
            # Confere DEPOIS de resolver: um link para fora não pode entrar
            # na lista só por estar dentro da pasta.
            real = alvo.resolve()
            if real != base and base not in real.parents:
                continue
            itens.append(_descrever(alvo, base))
        except OSError:
            continue

    itens.sort(key=lambda i: i["modificado"], reverse=True)
    return itens


def para_download(caminho: str) -> Path:
    """Caminho absoluto de um arquivo que pode ser baixado, ou erro."""
    alvo = _resolver(caminho)
    if not alvo.exists():
        raise FerramentaError(f"'{caminho}' não existe.")
    if alvo.is_dir():
        raise FerramentaError(f"'{caminho}' é uma pasta.")

    real = alvo.resolve()
    base = raiz()
    if real != base and base not in real.parents:
        raise FerramentaError(f"'{caminho}' aponta para fora da pasta de trabalho.")
    return real


def sobre(caminho: str) -> dict[str, object] | None:
    """Ficha de um arquivo, ou None se ele não existir.

    Serve para o servidor avisar a interface quando a Livia acaba de criar
    algo — e só avisa depois de confirmar que o arquivo está mesmo no disco.
    """
    try:
        alvo = para_download(caminho)
    except FerramentaError:
        return None
    return _descrever(alvo, raiz())
