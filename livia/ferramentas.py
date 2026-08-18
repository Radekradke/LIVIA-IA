"""Ferramentas: o que a Livia consegue FAZER, além de conversar.

Até aqui ela só produzia texto. Com isto, o modelo pode pedir ações — ler um
arquivo seu, escrever outro, listar uma pasta, fazer uma conta — e usar o
resultado na resposta.

A REGRA DE OURO É O CONFINAMENTO
--------------------------------
Toda operação de arquivo acontece dentro de UMA pasta, definida em
LIVIA_WORKSPACE. Antes de qualquer leitura ou escrita, o caminho é resolvido
para a forma canônica e conferido: se escapou da pasta, a operação é recusada.

Isso importa porque o caminho vem do modelo, e modelo erra. Um `../../.env`
pedido sem má intenção nenhuma leria a sua chave de API. O confinamento não
depende de o modelo se comportar bem — depende do código conferir.

O QUE NÃO ESTÁ AQUI
-------------------
Executar código arbitrário. Não há como isolar isso em Python puro: o código
teria os mesmos poderes do processo, e portanto os seus. Fazer direito exige
contêiner ou aprovação humana a cada execução — decisão do André, não padrão
silencioso meu.
"""

from __future__ import annotations

import ast
import json
import operator
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import config

LIMITE_LEITURA = 60_000       # caracteres devolvidos por leitura
LIMITE_ESCRITA = 400_000      # caracteres aceitos por escrita
LIMITE_LISTAGEM = 300         # itens por listagem


class FerramentaError(RuntimeError):
    """Falha que vale contar ao modelo, em português, para ele se corrigir."""


# --------------------------------------------------------------------------
# Confinamento
# --------------------------------------------------------------------------


def raiz() -> Path:
    r = config.WORKSPACE
    r.mkdir(parents=True, exist_ok=True)
    return r.resolve()


def _resolver(caminho: str) -> Path:
    """Caminho absoluto dentro da pasta de trabalho, ou erro.

    `resolve()` desfaz `..`, links simbólicos e caminhos relativos antes da
    conferência — checar a string crua não serviria de nada.
    """
    if not caminho or not caminho.strip():
        raise FerramentaError("Faltou o caminho do arquivo.")

    bruto = caminho.strip()

    # Caminho absoluto é recusado, não reinterpretado. Aceitar `/etc/shadow`
    # removendo a barra criaria `workspace/etc/shadow` em silêncio — o modelo
    # pediu uma coisa e receberia outra, sem saber. Recusar é mais honesto e
    # deixa o erro visível para ele se corrigir.
    if bruto.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", bruto):
        raise FerramentaError(
            f"'{caminho}' é um caminho absoluto. Use caminho relativo à pasta "
            "de trabalho, como 'notas/plano.md'."
        )

    base = raiz()
    alvo = (base / bruto).resolve()

    if alvo != base and base not in alvo.parents:
        raise FerramentaError(
            f"'{caminho}' fica fora da pasta de trabalho. Você só pode mexer "
            f"em arquivos dentro de {base.name}/."
        )
    return alvo


def _tamanho(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n/1024:.1f} KB" if n < 1024**2 else f"{n/1024**2:.1f} MB"


# --------------------------------------------------------------------------
# As ferramentas
# --------------------------------------------------------------------------


def listar(pasta: str = "") -> str:
    alvo = _resolver(pasta) if pasta else raiz()
    if not alvo.exists():
        raise FerramentaError(f"A pasta '{pasta}' não existe.")
    if not alvo.is_dir():
        raise FerramentaError(f"'{pasta}' é um arquivo, não uma pasta.")

    itens = sorted(alvo.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    if not itens:
        return f"A pasta '{pasta or '.'}' está vazia."

    linhas = []
    for item in itens[:LIMITE_LISTAGEM]:
        rel = item.relative_to(raiz()).as_posix()
        if item.is_dir():
            linhas.append(f"{rel}/")
        else:
            linhas.append(f"{rel}  ({_tamanho(item.stat().st_size)})")

    if len(itens) > LIMITE_LISTAGEM:
        linhas.append(f"... e mais {len(itens) - LIMITE_LISTAGEM} itens")
    return "\n".join(linhas)


def ler(caminho: str) -> str:
    """Conteúdo legível de um arquivo — PDF, DOCX e XLSX inclusive.

    O formato é decidido pela extensão, e o trabalho pesado fica em
    leitura.py. Aqui só resolvemos o caminho, despachamos e cortamos no
    limite: um PDF de 300 páginas estouraria a janela de contexto e a
    resposta sairia pior do que se ela tivesse lido só o começo.
    """
    from . import leitura

    alvo = _resolver(caminho)
    if not alvo.exists():
        raise FerramentaError(f"O arquivo '{caminho}' não existe. Use listar_arquivos para ver o que há.")
    if alvo.is_dir():
        raise FerramentaError(f"'{caminho}' é uma pasta. Use listar_arquivos.")

    if leitura.suportado(alvo):
        texto = leitura.extrair(alvo, caminho)
    else:
        try:
            texto = alvo.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise FerramentaError(
                f"'{caminho}' não é um arquivo de texto (parece binário) e a "
                f"extensão '{alvo.suffix or 'nenhuma'}' não é uma que eu saiba "
                f"interpretar. Eu leio: {', '.join(leitura.EXTENSOES)} e texto."
            ) from None
        except OSError as exc:
            raise FerramentaError(f"Não consegui abrir '{caminho}': {exc}") from exc

    if len(texto) > LIMITE_LEITURA:
        return (
            texto[:LIMITE_LEITURA]
            + f"\n\n[... arquivo truncado: tem {len(texto)} caracteres, "
              f"mostrei os primeiros {LIMITE_LEITURA}]"
        )
    return texto or "(o arquivo está vazio)"


def escrever(caminho: str, conteudo: str) -> str:
    alvo = _resolver(caminho)
    if len(conteudo) > LIMITE_ESCRITA:
        raise FerramentaError(f"Conteúdo grande demais ({len(conteudo)} caracteres).")

    existia = alvo.exists()
    if existia and alvo.is_dir():
        raise FerramentaError(f"'{caminho}' é uma pasta.")

    # Sobrescrever guarda uma cópia. A pasta é do André, e o modelo pode
    # errar o caminho — perder trabalho por causa disso seria inaceitável.
    if existia:
        carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = alvo.with_name(f"{alvo.stem}.antes-{carimbo}{alvo.suffix}")
        try:
            shutil.copy2(alvo, backup)
        except OSError:
            pass

    alvo.parent.mkdir(parents=True, exist_ok=True)
    try:
        alvo.write_text(conteudo, encoding="utf-8")
    except OSError as exc:
        raise FerramentaError(f"Não consegui escrever '{caminho}': {exc}") from exc

    verbo = "Sobrescrevi" if existia else "Criei"
    extra = " (a versão anterior foi guardada ao lado)" if existia else ""
    return f"{verbo} '{caminho}' com {_tamanho(len(conteudo.encode('utf-8')))}{extra}."


# Calculadora sem `eval`: só as operações listadas, sobre números literais.
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def _avaliar(no: ast.AST) -> float:
    if isinstance(no, ast.Constant) and isinstance(no.value, (int, float)):
        return no.value
    if isinstance(no, ast.BinOp) and type(no.op) in _OPS:
        if isinstance(no.op, ast.Pow):
            expoente = _avaliar(no.right)
            if abs(expoente) > 200:
                raise FerramentaError("Expoente grande demais.")
            return _OPS[ast.Pow](_avaliar(no.left), expoente)
        return _OPS[type(no.op)](_avaliar(no.left), _avaliar(no.right))
    if isinstance(no, ast.UnaryOp) and type(no.op) in _OPS:
        return _OPS[type(no.op)](_avaliar(no.operand))
    raise FerramentaError("Só entendo contas com + - * / // % ** e parênteses.")


def calcular(expressao: str) -> str:
    try:
        arvore = ast.parse(expressao.strip(), mode="eval")
    except SyntaxError:
        raise FerramentaError(f"'{expressao}' não é uma conta válida.") from None
    try:
        resultado = _avaliar(arvore.body)
    except ZeroDivisionError:
        raise FerramentaError("Divisão por zero.") from None
    if isinstance(resultado, float) and resultado.is_integer():
        resultado = int(resultado)
    return f"{expressao} = {resultado}"


# --------------------------------------------------------------------------
# Catálogo entregue ao modelo
# --------------------------------------------------------------------------

CATALOGO: list[dict[str, object]] = [
    {
        "name": "listar_arquivos",
        "description": (
            "Lista o que existe na pasta de trabalho do André. Use antes de ler "
            "ou escrever, para saber os nomes certos em vez de adivinhar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pasta": {
                    "type": "string",
                    "description": "Subpasta a listar. Vazio lista a raiz da pasta de trabalho.",
                }
            },
        },
    },
    {
        "name": "ler_arquivo",
        "description": (
            "Lê um arquivo da pasta de trabalho e devolve o conteúdo em texto. "
            "Entende PDF, DOCX, XLSX, CSV, JSON, HTML e qualquer arquivo de "
            "texto. Use sempre que precisar do que está escrito, em vez de "
            "supor pelo nome do arquivo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "caminho": {"type": "string", "description": "Caminho relativo à pasta de trabalho."}
            },
            "required": ["caminho"],
        },
    },
    {
        "name": "escrever_arquivo",
        "description": (
            "Cria ou sobrescreve um arquivo de texto na pasta de trabalho. "
            "Se já existir, a versão anterior é guardada ao lado automaticamente. "
            "Escreva o conteúdo completo do arquivo, não um trecho."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "caminho": {"type": "string", "description": "Caminho relativo à pasta de trabalho."},
                "conteudo": {"type": "string", "description": "Conteúdo completo do arquivo."},
            },
            "required": ["caminho", "conteudo"],
        },
    },
    {
        "name": "calcular",
        "description": (
            "Faz uma conta com precisão. Use sempre que a resposta depender de "
            "aritmética exata — modelos erram cálculo de cabeça."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expressao": {"type": "string", "description": "Ex: (1250 * 0.87) / 3"}
            },
            "required": ["expressao"],
        },
    },
]

def _objetos():
    """Import tardio: objetos.py depende deste módulo. No topo daria ciclo."""
    from . import objetos

    return objetos


def catalogo() -> list[dict[str, object]]:
    """Todas as ferramentas que o modelo enxerga, numa lista só."""
    return CATALOGO + _objetos().CATALOGO


_EXECUTORES = {
    "listar_arquivos": lambda a: listar(str(a.get("pasta") or "")),
    "ler_arquivo": lambda a: ler(str(a.get("caminho") or "")),
    "escrever_arquivo": lambda a: escrever(str(a.get("caminho") or ""), str(a.get("conteudo") or "")),
    "calcular": lambda a: calcular(str(a.get("expressao") or "")),
}


def executar(nome: str, argumentos: dict[str, object]) -> tuple[str, bool]:
    """Roda uma ferramenta. Devolve (texto do resultado, deu_certo).

    O erro volta como texto normal, não como exceção: o modelo lê a mensagem
    e costuma se corrigir sozinho na tentativa seguinte — pedir a listagem
    antes de ler, por exemplo. Derrubar a conversa não ajudaria ninguém.
    """
    executor = _EXECUTORES.get(nome) or _objetos().EXECUTORES.get(nome)
    if executor is None:
        return f"A ferramenta '{nome}' não existe.", False
    try:
        return executor(argumentos), True
    except FerramentaError as exc:
        return str(exc), False
    except Exception as exc:
        return f"Falha inesperada em {nome}: {exc}", False


# Ferramentas que gravam arquivo. A interface precisa saber para oferecer o
# download — sem isso a Livia diz "criei o relatório" e o arquivo fica só no
# disco do servidor, fora do alcance de quem pediu.
CRIAM_ARQUIVO = ("escrever_arquivo", "criar_documento", "criar_planilha")


def arquivo_gravado(nome: str, argumentos: dict[str, object]) -> str | None:
    """Caminho relativo que a chamada gravou, ou None.

    Só o que a ferramenta PEDIU para gravar. Quem confirma que existe de
    verdade é arquivos.sobre(), depois — anunciar um arquivo que falhou
    seria pior que não anunciar nada.
    """
    if nome not in CRIAM_ARQUIVO:
        return None
    caminho = argumentos.get("caminho")
    return str(caminho).strip() if caminho else None


def resumir(nome: str, argumentos: dict[str, object]) -> str:
    """Frase curta para a interface mostrar o que está acontecendo."""
    if nome == "listar_arquivos":
        return f"listando {argumentos.get('pasta') or 'a pasta de trabalho'}"
    if nome == "ler_arquivo":
        return f"lendo {argumentos.get('caminho')}"
    if nome == "escrever_arquivo":
        return f"escrevendo {argumentos.get('caminho')}"
    if nome == "calcular":
        return f"calculando {argumentos.get('expressao')}"
    return _objetos().resumir(nome, argumentos)
