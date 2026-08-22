"""Leitura e escrita dos arquivos de texto que formam a "cabeça" da Livia.

Memórias e skills são só arquivos Markdown com um cabeçalho simples:

    ---
    name: prefere-python
    description: O André prefere Python a JavaScript em projetos novos.
    kind: preference
    scope: global
    status: active
    created: 2026-08-17
    ---

    Corpo livre em Markdown.

Formato deliberadamente burro: dá para abrir, ler e editar no bloco de notas.
Se um dia você quiser jogar tudo fora e recomeçar, é só apagar a pasta.

CAMPOS NOVOS, ARQUIVOS ANTIGOS
------------------------------
`scope`, `status` e `supersedes` foram acrescentados depois. Arquivo que não
os tem continua válido: quem lê assume `global`, `active` e vazio. Nenhum
Markdown existente precisou ser reescrito para a versão nova funcionar, e
nenhum é reescrito só para migrar.

O que NÃO entra no cabeçalho: contador de uso, data da última utilização,
vetor. São números que mudam a cada resposta, e reescrever o arquivo do André
o tempo todo (sujando o histórico do editor dele, disputando com uma edição
manual aberta) seria péssima troca. Isso vive no SQLite — ver db.py.
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


# Os tipos que a Livia entende. Não é uma trava — um arquivo com `kind`
# fora desta lista continua funcionando —, é vocabulário para a interface
# e para o filtro de memória saberem do que estão falando.
TIPOS = (
    "preference",   # como o André gosta que as coisas sejam feitas
    "project",      # fato sobre um projeto dele
    "correction",   # algo que ela errou e ele corrigiu
    "reference",    # link, caminho, nome de sistema
    "decision",     # escolha tomada, com validade até ser revogada
    "person",       # gente
    "fact",         # o resto que é durável
    "lesson",       # regra deduzida de experiência (heurística/anti-pattern)
)

# Os `kind` das versões anteriores. Ficam aceitos para sempre: o arquivo do
# André não vai ser reescrito só porque a nomenclatura evoluiu.
TIPOS_ANTIGOS = {
    "preferencia": "preference",
    "projeto": "project",
    "correcao": "correction",
    "referencia": "reference",
    "decisao": "decision",
    "pessoa": "person",
    "nota": "fact",
    "manual": "fact",
    "licao": "lesson",
}

ATIVA = "active"
SUBSTITUIDA = "superseded"
ARQUIVADA = "archived"


def normalizar_tipo(kind: str) -> str:
    """Traduz nomenclatura antiga sem tocar no arquivo."""
    limpo = (kind or "").strip().lower()
    if limpo in TIPOS:
        return limpo
    return TIPOS_ANTIGOS.get(limpo, limpo or "fact")


@dataclass
class Doc:
    """Um arquivo de memória, skill ou lição já interpretado."""

    name: str
    description: str
    body: str
    path: Path
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return normalizar_tipo(self.meta.get("kind", "fact"))

    @property
    def kind_bruto(self) -> str:
        """O que está escrito no arquivo, sem tradução. Para a interface."""
        return self.meta.get("kind", "")

    @property
    def created(self) -> str:
        return self.meta.get("created", "")

    @property
    def scope(self) -> str:
        """`global`, `project:<id>` ou `conversation:<id>`."""
        return self.meta.get("scope", "global").strip() or "global"

    @property
    def status(self) -> str:
        return self.meta.get("status", ATIVA).strip() or ATIVA

    @property
    def ativa(self) -> bool:
        return self.status == ATIVA

    @property
    def source(self) -> str:
        return self.meta.get("source", "")

    @property
    def supersedes(self) -> str:
        return self.meta.get("supersedes", "")

    @property
    def superseded_by(self) -> str:
        return self.meta.get("superseded_by", "")

    @property
    def importance(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.meta.get("importance", "0.5"))))
        except ValueError:
            return 0.5

    def texto_para_vetor(self) -> str:
        """O que representa esta memória na busca por significado.

        Nome + descrição + corpo. O nome entra porque slug costuma carregar o
        assunto ("prefere-postgres"), e ignorá-lo perderia sinal de graça.
        """
        partes = [self.name.replace("-", " "), self.description, self.body.strip()]
        return "\n".join(p for p in partes if p)

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
            "scope": self.scope,
            "status": self.status,
            "importance": self.importance,
            "source": self.source,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
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


# A ordem em que os campos são escritos. Fixa, para o `git diff` de quem
# versiona a pasta data/ mostrar mudança de conteúdo, não de arrumação.
_ORDEM = ("name", "description", "kind", "scope", "status", "importance",
          "source", "supersedes", "superseded_by", "created", "updated")


def _cabecalho(meta: dict[str, str]) -> str:
    campos = [(c, meta[c]) for c in _ORDEM if meta.get(c)]
    campos += [(c, v) for c, v in sorted(meta.items()) if c not in _ORDEM and v]
    linhas = ["---", *(f"{c}: {v}" for c, v in campos), "---", ""]
    return "\n".join(linhas)


def write(
    directory: Path,
    name: str,
    description: str,
    body: str = "",
    kind: str = "nota",
    extra: dict[str, str] | None = None,
) -> Doc:
    """Grava um documento novo (ou sobrescreve um existente com o mesmo nome).

    Sobrescrever preserva o `created` original e carimba `updated`. A versão
    anterior reescrevia a data de criação, e com isso a memória mais antiga
    do André parecia ter nascido hoje — o que estraga qualquer critério de
    recência.
    """
    directory.mkdir(parents=True, exist_ok=True)
    slug = slugify(name)
    path = directory / f"{slug}.md"

    hoje = date.today().isoformat()
    anterior = parse(path) if path.exists() else None

    meta: dict[str, str] = dict(anterior.meta) if anterior else {}
    meta.update({
        "name": slug,
        "description": " ".join(description.split()),
        "kind": kind,
        "created": (anterior.created if anterior and anterior.created else hoje),
    })
    if anterior:
        meta["updated"] = hoje
    for chave, valor in (extra or {}).items():
        if valor is None:
            meta.pop(chave, None)
        else:
            meta[chave] = str(valor)

    path.write_text(_cabecalho(meta) + body.strip() + "\n", encoding="utf-8")

    doc = parse(path)
    if doc is None:  # pragma: no cover - só acontece com disco quebrado
        raise OSError(f"não consegui reler {path}")
    return doc


def patch(directory: Path, name: str, **campos: object) -> Doc | None:
    """Muda só o cabeçalho de um documento, sem tocar no corpo.

    É como uma memória vira `superseded` ou `archived`. Reescrever o documento
    inteiro para mudar uma linha do cabeçalho arriscaria perder formatação que
    o André pôs à mão no corpo.
    """
    path = directory / f"{slugify(name)}.md"
    doc = parse(path)
    if doc is None:
        return None

    meta = dict(doc.meta)
    meta.setdefault("name", doc.name)
    meta.setdefault("description", doc.description)
    for chave, valor in campos.items():
        if valor is None:
            meta.pop(chave, None)
        else:
            meta[chave] = str(valor)
    meta["updated"] = date.today().isoformat()

    path.write_text(_cabecalho(meta) + doc.body.strip() + "\n", encoding="utf-8")
    return parse(path)


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
