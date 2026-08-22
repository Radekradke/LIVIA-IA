"""As gavetas onde a Livia guarda o que sabe.

- memória: fatos sobre você e seus projetos ("prefere Python").
- skills:  procedimentos que você ensina uma vez e ela reusa ("como faço deploy").
- lições:  regras que ELA deduziu das próprias experiências ("nesta situação,
           tentar B antes de A"), incluindo os anti-patterns.

A diferença é de propósito, não de mecânica — as três são arquivos .md numa
pasta, e por isso usam a mesma classe.

A distinção entre as três importa e não deve ser dissolvida:

    MEMÓRIA      o que é verdade sobre o André
    SKILL        como fazer algo, ensinado por ele
    LIÇÃO        o que a experiência mostrou, deduzido por ela

Misturar as três num balaio só ("coisas que ela sabe") tornaria impossível
responder à pergunta que mais importa quando algo sai errado: isso veio do
André ou foi ela que concluiu sozinha?
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

    def save(
        self,
        name: str,
        description: str,
        body: str = "",
        kind: str = "nota",
        extra: dict[str, str] | None = None,
    ) -> Doc:
        return docs.write(self.directory, name, description, body, kind, extra)

    def patch(self, name: str, **campos: object) -> Doc | None:
        """Muda o cabeçalho sem mexer no corpo (status, escopo, importância)."""
        return docs.patch(self.directory, name, **campos)

    def ativos(self) -> list[Doc]:
        """Só o que ainda vale. Substituída e arquivada ficam no disco, e é
        de propósito: o histórico é a prova de por que ela mudou de ideia."""
        return [d for d in self.all() if d.ativa]

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
        # Só o que está ativo. Uma memória substituída continua no disco (o
        # histórico é a prova de por que ela mudou de ideia), mas mandá-la
        # para o prompt junto com a que a substituiu recriaria exatamente a
        # contradição que substituir existe para resolver.
        items = self.ativos()
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
lessons = Store(config.LESSONS_DIR, config.SKILL_BUDGET_CHARS, "lição")

# Nome da coleção -> gaveta. Usado pelo servidor e pelo índice semântico para
# não repetir este mapa em três lugares.
COLECOES = {"memories": memory, "skills": skills, "lessons": lessons}
