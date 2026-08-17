"""As duas gavetas onde a Livia guarda o que sabe.

- memória: fatos soltos sobre você e seus projetos ("prefere Python").
- skills: procedimentos que você ensina uma vez e ela reusa ("como eu faço deploy").

A diferença é só de propósito — mecanicamente são a mesma coisa: arquivos .md
numa pasta. Por isso as duas usam a mesma classe.
"""

from __future__ import annotations

from pathlib import Path

from . import config, docs
from .docs import Doc


class Store:
    """Uma pasta de arquivos .md com um orçamento de contexto."""

    def __init__(self, directory: Path, budget_chars: int, label: str) -> None:
        self.directory = directory
        self.budget_chars = budget_chars
        self.label = label

    def all(self) -> list[Doc]:
        return docs.load_all(self.directory)

    def get(self, name: str) -> Doc | None:
        return docs.find(self.directory, name)

    def save(self, name: str, description: str, body: str = "", kind: str = "nota") -> Doc:
        return docs.write(self.directory, name, description, body, kind)

    def delete(self, name: str) -> bool:
        return docs.delete(self.directory, name)

    def count(self) -> int:
        return len(list(self.directory.glob("*.md"))) if self.directory.exists() else 0

    def render(self) -> str:
        """Monta o texto que entra no prompt.

        Enquanto tudo cabe no orçamento, manda os documentos inteiros — é o que
        dá o melhor resultado. Quando a coleção cresce demais, degrada para um
        índice de uma linha por item, e o modelo pede o conteúdo completo se
        precisar. Isso mantém o custo estável mesmo com centenas de arquivos.
        """
        items = self.all()
        if not items:
            return ""

        blocks = [doc.as_block() for doc in items]
        total = sum(len(b) for b in blocks)

        if total <= self.budget_chars:
            return "\n\n".join(blocks)

        index = "\n".join(doc.index_line() for doc in items)
        return (
            f"(São {len(items)} itens, demais para carregar por inteiro. "
            "Abaixo vai só o índice; peça o conteúdo completo de um item se precisar.)\n\n"
            f"{index}"
        )


memory = Store(config.MEMORY_DIR, config.MEMORY_BUDGET_CHARS, "memória")
skills = Store(config.SKILLS_DIR, config.SKILL_BUDGET_CHARS, "skill")
