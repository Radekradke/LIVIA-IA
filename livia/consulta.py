"""Ferramentas para a Livia consultar a própria cabeça.

O PROMPT FAZIA UMA PROMESSA QUE O CÓDIGO NÃO CUMPRIA
----------------------------------------------------
Quando a coleção de memórias passa do orçamento, `Store.render()` degrada
para um índice e escreve no prompt: "peça o conteúdo completo de um item se
precisar". Só que não existia como pedir. Ela via os títulos, sabia que havia
mais, e não tinha nenhuma ferramenta para chegar lá.

Estas quatro ferramentas fecham esse buraco.

BUSCA LÉXICA, DE PROPÓSITO
--------------------------
Sem embedding e sem chamada de API. Três razões, em ordem de peso:

1. Custo. A biblioteca já gasta a cota de vetores com livros; gastar de novo
   para achar uma memória de uma linha é desproporcional.
2. Latência. Isso roda no meio de uma rodada de ferramentas, com o André
   esperando a resposta.
3. Serve para o que precisa servir. Memória e skill são textos curtos que
   ELE escreveu, com as palavras dele.

O QUE ISTO NÃO FAZ
------------------
Sinônimo. Procurar "banco de dados" não acha uma memória que só diz
"Postgres a MySQL" — as palavras não se cruzam em lugar nenhum, e nenhuma
busca por palavra resolveria isso. Só vetor resolveria, e vetor custa cota.

Isso importa menos do que parece, porque o índice de nomes e descrições
está SEMPRE no prompt, nas duas formas que `Store.render()` produz. Ela vê
que existe uma memória chamada `prefere-postgres` e chama `ler_memoria`
direto. A busca é atalho para o corpo, não o único caminho até ele.

O que a busca precisava mesmo era radical — ver §_radical.
"""

from __future__ import annotations

import re
import unicodedata

from .docs import Doc
from .ferramentas import FerramentaError
from .store import memory, skills

LIMITE_RESULTADOS = 8
LIMITE_CORPO = 6_000

# Palavras que aparecem em qualquer pergunta e não distinguem nada.
_VAZIAS = frozenset(
    "a o as os um uma uns umas de do da dos das em no na nos nas por para com "
    "sem sobre e ou que qual quais como quando onde porque pois se ja mais "
    "muito pouco todo toda todos todas eu ele ela voce nos meu minha seu sua "
    "isso isto aquilo the of to and is are was".split()
)


def _dobrar(texto: str) -> str:
    """Minúsculas e sem acento — 'memória' e 'memoria' têm que se encontrar."""
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    return sem_acento.encode("ascii", "ignore").decode("ascii").lower()


def _palavras(texto: str) -> list[str]:
    return [p for p in re.findall(r"[a-z0-9]+", _dobrar(texto)) if len(p) > 1]


def _termos(consulta: str) -> list[str]:
    """Palavras que valem buscar. Se sobrar nada, volta às originais.

    Buscar "o que" não deveria dar zero resultados por ter tirado tudo — sem
    esse cuidado, uma pergunta curta feita só de palavras comuns devolveria
    vazio como se a coleção estivesse vazia.
    """
    todas = _palavras(consulta)
    uteis = [p for p in todas if p not in _VAZIAS]
    return uteis or todas


# Português conjuga tudo. Sem cortar a flexão, "quanto isso custa" não encontra
# uma memória que diz "custo", e "como publicar" não encontra "publica" — as
# duas coisas medidas com dados de verdade antes de escrever isto.
#
# Não é um stemmer sério: é uma tesoura curta com piso de 4 letras, o bastante
# para casar conjugação e plural sem grudar palavras que não têm relação.
_PLURAIS = (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el"),
            ("ois", "ol"), ("ns", "m"))
_SUFIXOS = ("issimo", "mente", "ando", "endo", "indo", "acao", "coes", "dade",
            "agem", "ado", "ada", "ido", "ida", "vel", "cao", "ar", "er", "ir",
            "am", "em", "ou", "ei", "es", "s")
_PISO = 4


def _radical(palavra: str) -> str:
    """Corta flexão até o núcleo. Nunca deixa menos de 4 letras."""
    if len(palavra) <= _PISO:
        return palavra

    for fim, troca_por in _PLURAIS:
        if palavra.endswith(fim) and len(palavra) - len(fim) + len(troca_por) >= _PISO:
            palavra = palavra[: -len(fim)] + troca_por
            break

    for fim in _SUFIXOS:
        if palavra.endswith(fim) and len(palavra) - len(fim) >= _PISO:
            palavra = palavra[: -len(fim)]
            break

    if len(palavra) > _PISO and palavra[-1] in "aeo":
        palavra = palavra[:-1]

    return palavra


def _casam(termo: str, palavra: str) -> bool:
    """Duas palavras falam da mesma coisa?

    Prefixo conta nos dois sentidos: quem digita "config" quer achar
    "configuracao", e quem digita "configuracao" quer achar "config".
    """
    if termo == palavra:
        return True
    a, b = _radical(termo), _radical(palavra)
    if a == b:
        return True
    curto, longo = (a, b) if len(a) <= len(b) else (b, a)
    return len(curto) >= _PISO and longo.startswith(curto)


def _pontuar(doc: Doc, termos: list[str], frase: str) -> float:
    """Quanto este documento tem a ver com a busca.

    O peso segue a chance de o campo dizer do que o documento trata: o nome
    foi escolhido para isso, a descrição resume, o corpo é detalhe.
    """
    nome = _dobrar(doc.name)
    descricao = _dobrar(doc.description)
    corpo = _dobrar(doc.body)

    campos = ((_palavras(doc.name), 8), (_palavras(doc.description), 4), (_palavras(doc.body), 1))

    nota = 0.0
    for termo in termos:
        for palavras, peso in campos:
            if any(_casam(termo, palavra) for palavra in palavras):
                nota += peso

    # A frase inteira aparecendo é sinal muito mais forte que as palavras
    # soltas — "deploy no render" não é a mesma coisa que "deploy" + "render".
    if len(frase) > 3:
        if frase in descricao or frase in nome:
            nota += 12
        elif frase in corpo:
            nota += 5

    return nota


def _buscar(loja, consulta: str, quantas: int, rotulo: str, plural: str) -> str:
    itens = loja.all()
    if not itens:
        return f"Não há nenhuma {rotulo} guardada ainda."

    consulta = (consulta or "").strip()
    if not consulta:
        raise FerramentaError(f"Diga o que procurar entre as {plural}.")

    termos = _termos(consulta)
    frase = _dobrar(consulta)

    notas = [(doc, _pontuar(doc, termos, frase)) for doc in itens]
    achados = sorted((p for p in notas if p[1] > 0), key=lambda p: -p[1])
    achados = achados[: max(1, min(quantas, LIMITE_RESULTADOS))]

    if not achados:
        # Listar o que existe é mais útil que só dizer "não achei": ela
        # reformula sozinha em vez de concluir que a coleção está vazia.
        indice = "\n".join(doc.index_line() for doc in itens[:20])
        return (
            f"Nenhuma {rotulo} sobre '{consulta}'. O que existe:\n{indice}"
            + (f"\n(e mais {len(itens) - 20})" if len(itens) > 20 else "")
        )

    partes = [f"{len(achados)} de {len(itens)} {plural}, da mais parecida para a menos:"]
    for doc, nota in achados:
        partes.append("")
        partes.append(f"--- {doc.name} ---")
        partes.append(doc.description)
        corpo = doc.body.strip()
        if corpo:
            partes.append("")
            partes.append(corpo[:1200] + ("…" if len(corpo) > 1200 else ""))
    return "\n".join(partes)


def _ler(loja, nome: str, rotulo: str) -> str:
    nome = (nome or "").strip()
    if not nome:
        raise FerramentaError(f"Falta o nome da {rotulo}.")

    doc = loja.get(nome)
    if doc is None:
        itens = loja.all()
        if not itens:
            raise FerramentaError(f"Não há nenhuma {rotulo} guardada.")
        # Nome parecido acontece muito: ela chuta 'deploy' e o arquivo é
        # 'como-fazer-deploy'. Sugerir é melhor que só recusar.
        alvo = _dobrar(nome)
        perto = [d.name for d in itens if alvo in _dobrar(d.name) or _dobrar(d.name) in alvo]
        dica = f" Talvez seja: {', '.join(perto[:5])}." if perto else ""
        raise FerramentaError(f"Não existe {rotulo} chamada '{nome}'.{dica}")

    corpo = doc.body.strip()
    texto = f"--- {doc.name} ---\n{doc.description}"
    if doc.kind:
        texto += f"\n(tipo: {doc.kind})"
    if corpo:
        texto += "\n\n" + corpo[:LIMITE_CORPO]
        if len(corpo) > LIMITE_CORPO:
            texto += f"\n\n[... cortado: o texto tem {len(corpo)} caracteres]"
    return texto


# --------------------------------------------------------------------------
# As quatro ferramentas
# --------------------------------------------------------------------------


def buscar_memorias(termo: str, quantas: int = 5) -> str:
    return _buscar(memory, termo, quantas, "memória", "memórias")


def ler_memoria(nome: str) -> str:
    return _ler(memory, nome, "memória")


def buscar_skills(termo: str, quantas: int = 5) -> str:
    return _buscar(skills, termo, quantas, "skill", "skills")


def ler_skill(nome: str) -> str:
    return _ler(skills, nome, "skill")


CATALOGO: list[dict[str, object]] = [
    {
        "name": "buscar_memorias",
        "description": (
            "Procura nas memórias sobre o André por palavra-chave. Use quando "
            "o índice de memórias no seu prompt mostrar só os títulos e você "
            "precisar do conteúdo, ou quando ele citar algo que já contou antes "
            "e você não tiver o detalhe à mão. Não custa chamada de API."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "termo": {
                    "type": "string",
                    "description": "Palavras a procurar, ex: 'banco de dados' ou 'deploy'.",
                },
                "quantas": {"type": "integer", "description": "Quantas trazer (padrão 5)."},
            },
            "required": ["termo"],
        },
    },
    {
        "name": "ler_memoria",
        "description": (
            "Lê uma memória inteira pelo nome exato, como aparece no índice. "
            "Use depois de buscar_memorias, ou quando já souber o nome."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nome": {"type": "string", "description": "Nome da memória, ex: prefere-postgres."}
            },
            "required": ["nome"],
        },
    },
    {
        "name": "buscar_skills",
        "description": (
            "Procura nas skills que o André ensinou. Use quando a tarefa parecer "
            "coberta por um procedimento dele e você só tiver o título no índice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "termo": {"type": "string", "description": "Palavras a procurar."},
                "quantas": {"type": "integer", "description": "Quantas trazer (padrão 5)."},
            },
            "required": ["termo"],
        },
    },
    {
        "name": "ler_skill",
        "description": (
            "Lê uma skill inteira pelo nome exato. Faça isso ANTES de executar "
            "o procedimento — seguir pela metade é pior que perguntar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nome": {"type": "string", "description": "Nome da skill."}
            },
            "required": ["nome"],
        },
    },
]

EXECUTORES = {
    "buscar_memorias": lambda a: buscar_memorias(
        str(a.get("termo") or ""), int(a.get("quantas") or 5)
    ),
    "ler_memoria": lambda a: ler_memoria(str(a.get("nome") or "")),
    "buscar_skills": lambda a: buscar_skills(
        str(a.get("termo") or ""), int(a.get("quantas") or 5)
    ),
    "ler_skill": lambda a: ler_skill(str(a.get("nome") or "")),
}


def resumir(nome: str, argumentos: dict[str, object]) -> str:
    if nome == "buscar_memorias":
        return f"procurando nas memórias: {argumentos.get('termo')}"
    if nome == "ler_memoria":
        return f"lendo a memória {argumentos.get('nome')}"
    if nome == "buscar_skills":
        return f"procurando nas skills: {argumentos.get('termo')}"
    if nome == "ler_skill":
        return f"lendo a skill {argumentos.get('nome')}"
    return ""
