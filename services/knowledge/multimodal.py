"""Parser avançado — o caminho para documentos que o pypdf não vence.

O pypdf CONTINUA sendo o caminho normal, e isso não é conservadorismo: ele lê
um PDF de texto em milissegundos, sem modelo nenhum, sem GPU. Trocá-lo por um
pipeline pesado em todo documento seria pagar minutos para resolver um
problema que a maioria dos arquivos não tem.

    PDF
     ↓
    pypdf
     ↓
    saiu texto suficiente?
     ├─ sim → fluxo de sempre                    (o caso comum)
     └─ não → parser avançado disponível?
               ├─ sim → RAG-Anything / MinerU
               └─ não → a mensagem honesta de hoje

O QUE VEM DAQUI NÃO É "TEXTO"
-----------------------------
Um PDF difícil tem tabela, equação e diagrama, e achatar tudo em parágrafo
perde justamente o que era difícil. Cada pedaço extraído mantém o tipo
(`text`, `table`, `image`, `equation`), e quem monta o prompt sabe disso.

Uma equação que vira "x2 + y2 = z2" em silêncio é pior que uma equação
faltando: a primeira parece certa.

O QUE ESTE MÓDULO NÃO É
-----------------------
Não é um segundo motor de busca. O RAG-Anything traz um stack de retrieval
inteiro junto (LightRAG); nada dele é usado aqui. Aproveitamos SÓ o parsing —
que é onde está o valor que a Livia não tem. Três mecanismos fazendo
recuperação seria exatamente o Frankenstein que a arquitetura evita.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("knowledge.parser")

# Abaixo disto, o que o pypdf tirou não dá para chamar de texto: é PDF
# escaneado, ou uma casca com o conteúdo todo em imagem.
MINIMO_POR_PAGINA = 90

TIPOS = ("text", "table", "image", "equation", "chart")


def _tentar_importar():
    """O import pesado fica aqui dentro, e só é feito quando pedido.

    `raganything` arrasta MinerU, que arrasta opencv. Importar no topo faria
    o serviço inteiro pagar isso mesmo com o parser desligado.
    """
    try:
        from raganything import RAGAnything  # noqa: F401
        return True
    except Exception:
        return False


_disponivel: bool | None = None


def disponivel() -> bool:
    global _disponivel
    if _disponivel is None:
        _disponivel = _tentar_importar()
    return _disponivel


def como_instalar() -> str:
    return (
        "O parser avançado não está instalado. Para PDF escaneado, tabela "
        "complexa ou equação:\n\n"
        '    pip install "raganything>=1.3"\n\n'
        "Para OCR de página escaneada, o extra:\n\n"
        '    pip install "raganything[paddleocr]"\n\n'
        "São gigabytes — instale só se precisar."
    )


def texto_insuficiente(paginas: list[tuple[int, str]]) -> bool:
    """O pypdf deu conta deste PDF?

    A conta é por página, não no total: um PDF de 200 páginas escaneadas com
    uma capa em texto passaria no total e falharia no que importa.
    """
    if not paginas:
        return True
    total = sum(len((t or "").strip()) for _, t in paginas)
    return (total / max(1, len(paginas))) < MINIMO_POR_PAGINA


# --------------------------------------------------------------------------
# Extração
# --------------------------------------------------------------------------


async def extrair(caminho: Path, *, descrever_imagens: bool = False) -> list[dict]:
    """Blocos tipados de um documento difícil.

    Devolve `[{"type", "page", "content", "caption", "source"}]`. Lista vazia
    quando o parser não está instalado — quem chama decide o que dizer, e
    levantar exceção aqui obrigaria todo chamador a tratar o caso normal
    "não instalado" como erro.
    """
    if not disponivel():
        return []

    from raganything import RAGAnything

    try:
        # O parser escreve arquivos intermediários; mantê-los ao lado do
        # grafo evita sujar a pasta do André.
        from . import config as kconfig

        saida = kconfig.DATA_DIR / "parser"
        saida.mkdir(parents=True, exist_ok=True)

        motor = RAGAnything(working_dir=str(saida))
        resultado = await motor.parse_document(
            file_path=str(caminho),
            output_dir=str(saida),
            parse_method="auto",
        )
    except Exception as exc:
        # Falha do parser avançado CAI PARA O COMPORTAMENTO SEGURO: quem
        # chama segue com o que o pypdf conseguiu. Nunca derruba a ingestão.
        log.warning("[parser] avançado falhou em %s: %s", caminho.name, exc)
        return []

    return _normalizar(resultado, caminho.name, descrever_imagens)


def _normalizar(bruto, origem: str, descrever_imagens: bool) -> list[dict]:
    """Do formato do parser para o nosso, preservando o TIPO de cada bloco.

    Defensivo: a forma de retorno muda entre versões, e um KeyError aqui
    perderia o documento inteiro. O que não der para entender é ignorado com
    log, nunca convertido em texto às cegas.
    """
    if isinstance(bruto, dict):
        bruto = bruto.get("content_list") or bruto.get("blocks") or []
    if not isinstance(bruto, (list, tuple)):
        return []

    blocos: list[dict] = []
    for item in bruto:
        if isinstance(item, str):
            blocos.append({"type": "text", "page": None, "content": item,
                           "caption": "", "source": origem})
            continue
        if not isinstance(item, dict):
            continue

        tipo = str(item.get("type") or item.get("category") or "text").lower()
        if tipo in ("equation", "formula", "interline_equation"):
            tipo = "equation"
        elif tipo in ("table", "table_body"):
            tipo = "table"
        elif tipo in ("image", "figure", "img"):
            tipo = "image"
        elif tipo not in TIPOS:
            tipo = "text"

        conteudo = ""
        for chave in ("text", "content", "table_body", "latex", "html", "img_caption"):
            valor = item.get(chave)
            if isinstance(valor, str) and valor.strip():
                conteudo = valor
                break
            if isinstance(valor, list) and valor:
                conteudo = " ".join(str(v) for v in valor)
                break

        legenda = ""
        for chave in ("caption", "img_caption", "table_caption"):
            valor = item.get(chave)
            if isinstance(valor, str) and valor.strip():
                legenda = valor
                break
            if isinstance(valor, list) and valor:
                legenda = " ".join(str(v) for v in valor)
                break

        # Imagem sem legenda e sem descrição não vira texto vazio: fica
        # registrada COMO IMAGEM, para o modelo saber que ali havia uma
        # figura que ninguém leu. Sumir com ela em silêncio faria a resposta
        # parecer completa quando não é.
        if tipo == "image" and not (conteudo or legenda):
            if not descrever_imagens:
                conteudo = "[figura sem legenda — não foi descrita]"

        if not conteudo.strip():
            continue

        pagina = item.get("page_idx") if item.get("page_idx") is not None else item.get("page")
        try:
            pagina = int(pagina) + 1 if item.get("page_idx") is not None else (
                int(pagina) if pagina is not None else None
            )
        except (TypeError, ValueError):
            pagina = None

        blocos.append({
            "type": tipo,
            "page": pagina,
            "content": conteudo[:8000],
            "caption": legenda[:400],
            "source": origem,
        })

    log.debug("[parser] %s -> %d blocos (%s)", origem, len(blocos),
              ", ".join(sorted({b["type"] for b in blocos})))
    return blocos


def para_trechos(blocos: list[dict]) -> list[dict[str, object]]:
    """Converte blocos tipados no formato de trecho da biblioteca.

    O tipo vira PREFIXO do texto, não metadado escondido: "[tabela, p. 14]"
    no começo faz o modelo tratar aquilo como tabela ao ler. Um metadado que
    só nós vemos não muda a resposta dele.
    """
    trechos: list[dict[str, object]] = []
    for b in blocos:
        marca = {
            "table": "[tabela]", "equation": "[equação]",
            "image": "[figura]", "chart": "[gráfico]",
        }.get(b["type"], "")
        corpo = b["content"]
        if b.get("caption"):
            corpo = f"{b['caption']}\n{corpo}"
        trechos.append({
            "texto": f"{marca} {corpo}".strip() if marca else corpo,
            "pagina": b.get("page") or 0,
            "origem": b.get("source") or "",
            "tipo": b["type"],
        })
    return trechos


def diagnostico() -> dict[str, object]:
    return {
        "instalado": disponivel(),
        "ligado": os.getenv("LIVIA_PARSER_AVANCADO", "0") != "0",
        "mensagem": "" if disponivel() else como_instalar(),
    }
