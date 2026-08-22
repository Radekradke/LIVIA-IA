"""Indexar uma pasta de projeto para a Livia poder consultá-la.

A biblioteca já sabe guardar documento e achar trecho por significado. O que
faltava era um caminho para "leia o meu projeto inteiro" sem o André arrastar
arquivo por arquivo — e sem que isso vire um tiro no pé.

O TIRO NO PÉ QUE ISTO EVITA
---------------------------
Indexar uma pasta de projeto de forma ingênua faz três estragos:

1. Varre `node_modules` e `.git` e passa uma hora gerando vetor de código de
   terceiro e de objeto binário do git.
2. Indexa o `.env`, e a partir daí a chave de API do André está dentro de um
   arquivo de vetores, pronta para reaparecer no prompt na primeira pergunta
   sobre configuração.
3. Estoura a memória com um `.min.js` de 4 MB numa linha só.

Por isso: lista de extensões permitidas (não proibidas), pastas ignoradas,
teto de tamanho, e uma varredura de segredo que age no nível do ARQUIVO e da
LINHA. Se um arquivo permitido tiver uma chave no meio, a linha some do texto
indexado — e o arquivo entra sem ela, em vez de o projeto inteiro ser recusado.

A REGRA DE OURO CONTINUA VALENDO
--------------------------------
Só dá para indexar pasta DENTRO do workspace. O caminho passa pelo mesmo
`_resolver` das ferramentas, com o mesmo confinamento. Uma "importação de
projeto" que aceitasse caminho absoluto seria um jeito elegante de ler
/home/andre/.ssh.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path

from . import biblioteca, config, ferramentas
from .docs import slugify

log = logging.getLogger("livia.conhecimento")

# Permitir por lista, nunca proibir por lista. Extensão nova aparece toda
# semana; se o padrão fosse "indexa o que não está proibido", o primeiro
# formato binário desconhecido entraria como lixo.
EXTENSOES = {
    ".md", ".txt", ".rst",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sql", ".html", ".css", ".scss",
    ".sh", ".java", ".go", ".rs", ".rb", ".php", ".c", ".h", ".cpp", ".cs",
}

# Pastas que nunca valem a pena: dependência de terceiro, artefato de build,
# histórico do git, ambiente virtual.
PASTAS_IGNORADAS = {
    "node_modules", ".git", ".hg", ".svn", "dist", "build", "out",
    "coverage", ".coverage", "venv", ".venv", "env", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".nuxt",
    "target", "vendor", ".idea", ".vscode", ".terraform", ".cache",
    "site-packages", ".tox", "htmlcov", ".gradle", "Pods",
}

# Arquivos que carregam segredo por natureza. `.env.example` fica de fora da
# proibição de propósito: ele é documentação, e não tem valor real dentro.
ARQUIVOS_PROIBIDOS = re.compile(
    r"^(\.env(\.[\w-]+)?|\.netrc|\.npmrc|\.pypirc|\.htpasswd|"
    r"id_[rd]sa|id_ecdsa|id_ed25519|.*\.pem|.*\.key|.*\.pfx|.*\.p12|"
    r"credentials|credentials\.json|service-account.*\.json|"
    r"secrets?\.(ya?ml|json|toml|ini)|"
    r"cookies\.sqlite|logins\.json|key[34]\.db)$",
    re.IGNORECASE,
)
EXCECOES_PROIBIDAS = re.compile(r"^\.env\.(example|sample|template)$", re.IGNORECASE)

# Linhas que parecem carregar segredo de verdade. O objetivo NÃO é detectar
# tudo (impossível) — é não deixar passar o caso comum: variável com valor
# longo, token de formato conhecido, chave privada.
LINHA_SUSPEITA = re.compile(
    r"("
    # O nome pode vir com prefixo ou sufixo: SECRET_KEY, MINHA_API_KEY_PROD.
    r"[A-Za-z0-9_\-]*"
    r"(?:api[_-]?key|secret|password|passwd|token|auth|credential|private[_-]?key)"
    r"[A-Za-z0-9_\-]*"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{16,}"
    r"|AKIA[0-9A-Z]{16}"                       # AWS
    r"|sk-[A-Za-z0-9]{20,}"                    # OpenAI e parecidos
    r"|AIza[0-9A-Za-z_\-]{30,}"                # Google
    r"|gh[pousr]_[A-Za-z0-9]{30,}"             # GitHub
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"           # Slack
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r")",
    re.IGNORECASE,
)

TAMANHO_MAXIMO = 400_000     # bytes por arquivo
ARQUIVOS_MAXIMO = 400        # por importação
LINHA_MAXIMA = 2_000         # caracteres; acima disso é bundle, não código


class ConhecimentoError(RuntimeError):
    """Erro já em português, para mostrar ao usuário."""


# --------------------------------------------------------------------------
# Seleção
# --------------------------------------------------------------------------


def _proibido(nome: str) -> bool:
    if EXCECOES_PROIBIDAS.match(nome):
        return False
    return bool(ARQUIVOS_PROIBIDOS.match(nome))


def _binario(dados: bytes) -> bool:
    """Byte zero no começo é o sinal mais confiável de arquivo binário."""
    return b"\x00" in dados[:4096]


def limpar_segredos(texto: str) -> tuple[str, int]:
    """Tira as linhas que parecem carregar segredo. Devolve (texto, quantas).

    Age por linha, não por arquivo. Um `settings.py` legítimo com uma chave
    esquecida no meio continua valendo a pena indexar — sem aquela linha.
    """
    saida: list[str] = []
    removidas = 0
    for linha in texto.splitlines():
        if LINHA_SUSPEITA.search(linha):
            saida.append("[linha removida: parecia conter uma credencial]")
            removidas += 1
            continue
        if len(linha) > LINHA_MAXIMA:
            saida.append(linha[:LINHA_MAXIMA] + " […linha truncada]")
            continue
        saida.append(linha)
    return "\n".join(saida), removidas


def listar_arquivos(raiz: Path) -> list[Path]:
    """Os arquivos que valem indexar, em ordem estável."""
    escolhidos: list[Path] = []

    for caminho in sorted(raiz.rglob("*")):
        if len(escolhidos) >= ARQUIVOS_MAXIMO:
            break
        if not caminho.is_file() or caminho.is_symlink():
            continue
        partes = set(caminho.relative_to(raiz).parts[:-1])
        if partes & PASTAS_IGNORADAS:
            continue
        if caminho.suffix.lower() not in EXTENSOES:
            continue
        if _proibido(caminho.name):
            continue
        try:
            if caminho.stat().st_size > TAMANHO_MAXIMO:
                continue
        except OSError:
            continue
        escolhidos.append(caminho)

    return escolhidos


# --------------------------------------------------------------------------
# Importação
# --------------------------------------------------------------------------


async def importar(pasta: str) -> AsyncIterator[dict[str, object]]:
    """Indexa uma pasta do workspace, relatando o progresso.

    O resultado é um documento normal da biblioteca: a busca por significado
    passa a alcançar o código e a documentação do projeto sem nenhuma peça
    nova no caminho de leitura.
    """
    try:
        raiz = ferramentas._resolver(pasta) if pasta else ferramentas.raiz()
    except ferramentas.FerramentaError as exc:
        raise ConhecimentoError(str(exc)) from exc

    if not raiz.exists() or not raiz.is_dir():
        raise ConhecimentoError(
            f"'{pasta}' não é uma pasta dentro da área de trabalho."
        )

    yield {"etapa": "lendo", "texto": f"procurando arquivos em {raiz.name}…"}
    arquivos = listar_arquivos(raiz)
    if not arquivos:
        raise ConhecimentoError(
            f"Não achei nada indexável em '{pasta or raiz.name}'. Eu leio "
            f"{', '.join(sorted(e.lstrip('.') for e in list(EXTENSOES)[:8]))} e "
            "outros formatos de texto — binários, dependências e arquivos de "
            "credencial ficam de fora de propósito."
        )

    yield {
        "etapa": "dividindo",
        "texto": f"{len(arquivos)} arquivos, separando em trechos…",
    }

    trechos: list[dict[str, object]] = []
    ignorados = 0
    segredos = 0

    for caminho in arquivos:
        relativo = caminho.relative_to(raiz).as_posix()
        try:
            dados = caminho.read_bytes()
        except OSError:
            ignorados += 1
            continue
        if _binario(dados):
            ignorados += 1
            continue

        try:
            texto = dados.decode("utf-8")
        except UnicodeDecodeError:
            texto = dados.decode("latin-1", "replace")

        texto, removidas = limpar_segredos(texto)
        segredos += removidas

        # Cada arquivo é dividido separadamente, para o trecho nunca começar
        # num arquivo e terminar em outro — o que produziria contexto que
        # não existe em lugar nenhum do projeto.
        for pedaco in biblioteca.dividir([(0, f"# {relativo}\n\n{texto}")]):
            trechos.append({**pedaco, "origem": relativo})

    if not trechos:
        raise ConhecimentoError(
            "Os arquivos até existem, mas nenhum tem texto suficiente para valer "
            "a pena indexar."
        )

    titulo = f"projeto {raiz.name}"
    log.debug(
        "[conhecimento] projeto=%s arquivos=%d trechos=%d segredos_removidos=%d",
        raiz.name, len(arquivos), len(trechos), segredos,
    )

    async for passo in biblioteca._indexar(
        slugify(titulo),
        titulo,
        trechos,
        arquivo=str(raiz.name),
        paginas=len(arquivos),
        tipo="projeto",
    ):
        if passo.get("etapa") == "pronto":
            livro = dict(passo["livro"])
            livro["arquivos"] = len(arquivos)
            livro["ignorados"] = ignorados
            livro["segredos_removidos"] = segredos
            yield {"etapa": "pronto", "livro": livro}
        else:
            yield passo


def pastas_candidatas() -> list[dict[str, object]]:
    """Subpastas do workspace que dá para importar, com uma prévia do tamanho.

    Serve para a interface oferecer uma lista em vez de exigir que o André
    digite o caminho certo de cabeça.
    """
    try:
        raiz = ferramentas.raiz()
    except OSError:
        return []

    saida = []
    for caminho in sorted(raiz.iterdir()):
        if not caminho.is_dir() or caminho.name in PASTAS_IGNORADAS:
            continue
        if caminho.name.startswith("."):
            continue
        arquivos = listar_arquivos(caminho)
        saida.append({
            "nome": caminho.name,
            "arquivos": len(arquivos),
            "importavel": bool(arquivos),
        })
    return saida
