"""Leitura e escrita dos arquivos de texto que formam a "cabeça" da Livia.

Memórias e skills são só arquivos Markdown com um cabeçalho simples:

    ---
    name: prefere-python
    description: O André prefere Python a JavaScript em projetos novos.
    kind: preferencia
    created: 2026-08-17
    ---

    Corpo livre em Markdown.

Formato deliberadamente burro: dá para abrir, ler e editar no bloco de notas.
Se um dia você quiser jogar tudo fora e recomeçar, é só apagar a pasta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def slugify(text: str, max_len: int = 60) -> str:
    """Transforma um título qualquer em nome de arquivo seguro."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    slug = slug[:max_len].strip("-")
    return slug or "sem-titulo"


@dataclass
class Doc:
    """Um arquivo de memória ou skill já interpretado."""

    name: str
    description: str
    body: str
    path: Path
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.meta.get("kind", "nota")

    @property
    def created(self) -> str:
        return self.meta.get("created", "")

    def index_line(self) -> str:
        """Uma linha para o índice que vai no prompt."""
        return f"- {self.name}: {self.description}"

    def as_block(self) -> str:
        """O documento inteiro, formatado para entrar no prompt."""
        header = f"### {self.name}\n_{self.description}_"
        body = self.body.strip()
        return f"{header}\n\n{body}" if body else header

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "kind": self.kind,
            "created": self.created,
            "file": self.path.name,
        }


def parse(path: Path) -> Doc | None:
    """Lê um arquivo .md. Devolve None se não der para interpretar."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    meta: dict[str, str] = {}
    body = raw

    match = _FRONTMATTER.match(raw)
    if match:
        head, body = match.group(1), match.group(2)
        for line in head.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()

    name = meta.get("name") or path.stem
    description = meta.get("description") or _first_line(body) or name
    return Doc(name=name, description=description, body=body, path=path, meta=meta)


def write(
    directory: Path,
    name: str,
    description: str,
    body: str = "",
    kind: str = "nota",
) -> Doc:
    """Grava um documento novo (ou sobrescreve um existente com o mesmo nome)."""
    directory.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    path = directory / f"{slug}.md"

    description = " ".join(description.split())
    header = "\n".join(
        [
            "---",
            f"name: {slug}",
            f"description: {description}",
            f"kind: {kind}",
            f"created: {date.today().isoformat()}",
            "---",
            "",
        ]
    )
    path.write_text(header + body.strip() + "\n", encoding="utf-8")

    doc = parse(path)
    if doc is None:  # pragma: no cover - só acontece com disco quebrado
        raise OSError(f"não consegui reler {path}")
    return doc


def load_all(directory: Path) -> list[Doc]:
    """Todos os documentos de uma pasta, em ordem alfabética."""
    if not directory.exists():
        return []
    docs = [parse(p) for p in sorted(directory.glob("*.md"))]
    return [d for d in docs if d is not None]


def find(directory: Path, name: str) -> Doc | None:
    path = directory / f"{slugify(name)}.md"
    return parse(path) if path.exists() else None


def delete(directory: Path, name: str) -> bool:
    path = directory / f"{slugify(name)}.md"
    if path.exists():
        path.unlink()
        return True
    return False


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""
