"""Aprendizado automático: decidir o que da conversa merece virar memória.

Roda depois que a resposta já foi entregue, então nunca deixa o chat mais lento.
Usa o modelo barato: a tarefa é de triagem, não exige inteligência de ponta.

O critério é deliberadamente rígido. Uma memória boa é uma que ainda vai ser
verdade daqui a três meses. Salvar demais é pior que salvar de menos: cada
memória entra na disputa por espaço no prompt, então lixo acumulado degrada
tudo.

O QUE MUDOU COM A MEMÓRIA SEMÂNTICA
-----------------------------------
Antes, o filtro recebia um índice de TODAS as memórias e o pedido de "não
repita nada parecido com isto". Funciona com vinte itens; com trezentas linhas
de índice, o modelo simplesmente para de conferir, e a mesma preferência é
gravada cinco vezes com palavras diferentes.

Agora são duas etapas:

  1. o modelo propõe, vendo só as memórias PARECIDAS com o trecho da conversa;
  2. o código confere por semelhança de vetor antes de gravar, e decide entre
     criar, atualizar ou substituir.

A segunda etapa é a que realmente protege — ela não depende de o modelo se
comportar bem.

CORREÇÃO TEM TRATAMENTO PRÓPRIO
-------------------------------
"não usamos mais Firebase, migramos para Supabase" é o sinal de aprendizado
mais valioso que existe: é a única vez em que a resposta certa vem de graça,
dita por quem sabe. Quando a mensagem é uma correção, o filtro é avisado disso,
a memória nasce com importância alta, e o modelo é convidado a apontar QUAL
memória antiga deixou de valer — que é o que faz o Firebase virar `superseded`
em vez de conviver com o Supabase.
"""

from __future__ import annotations

import logging

from . import brain, config, experiencia, memoria
from .store import memory

log = logging.getLogger("livia.learner")

_SCHEMA: dict[str, object] = {
    "type": "OBJECT",
    "properties": {
        "memories": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {
                        "type": "STRING",
                        "description": "Título curto em kebab-case, ex: prefere-postgres",
                    },
                    "description": {
                        "type": "STRING",
                        "description": "O fato em uma frase, escrito em terceira pessoa",
                    },
                    "kind": {
                        "type": "STRING",
                        "description": (
                            "Um de: preference, project, correction, reference, "
                            "decision, person, fact"
                        ),
                    },
                    "scope": {
                        "type": "STRING",
                        "description": (
                            "'global' se vale para o usuário em geral, ou 'project' "
                            "se vale só para o projeto de que se está falando"
                        ),
                    },
                    "importance": {
                        "type": "NUMBER",
                        "description": (
                            "0 a 1. Use 0.9+ só para identidade e decisões que "
                            "valem em toda conversa"
                        ),
                    },
                    "replaces": {
                        "type": "STRING",
                        "description": (
                            "Nome exato de uma memória da lista de parecidas que "
                            "deixou de ser verdade. Vazio se nenhuma."
                        ),
                    },
                },
                "required": ["name", "description"],
            },
        }
    },
    "required": ["memories"],
}

_SYSTEM = """\
Você é o filtro de memória de uma assistente pessoal. Sua única tarefa é ler um \
trecho de conversa e decidir se apareceu algo que valha guardar para sempre.

GUARDE apenas:
- Preferências duráveis do usuário (ferramentas, estilo, formato de trabalho).
- Fatos sobre projetos dele que não dá para deduzir sozinho depois.
- Correções que ele fez na assistente — o que ela errou e qual é o certo.
- Decisões tomadas, que valem até serem revogadas.
- Referências permanentes (links, caminhos, nomes de sistemas internos).

NÃO GUARDE:
- O assunto da conversa em si, resumos ou "conversamos sobre X".
- Conhecimento geral que qualquer modelo já tem.
- Perguntas pontuais e coisas que só valem para hoje.
- Qualquer coisa já dita por uma das memórias parecidas listadas abaixo.
- Senhas, chaves de API ou dados sensíveis. Nunca. Sem exceção.

Na dúvida, não guarde. Devolver uma lista vazia é o resultado mais comum e \
está perfeitamente correto.

SOBRE `replaces`: se o usuário disse que algo MUDOU e uma das memórias \
parecidas afirma o estado antigo, ponha o nome exato dela em `replaces`. Não \
invente nome: só vale um que esteja na lista. Isso é o que evita a assistente \
ficar afirmando as duas coisas ao mesmo tempo.

SOBRE `scope`: use 'project' quando o fato só vale dentro do projeto que está \
sendo discutido ("aqui usamos SQLite"), e 'global' quando vale para o usuário \
em geral ("prefere Postgres"). Os dois podem coexistir sem contradição.

Escreva cada descrição como um fato acabado, em terceira pessoa, sem depender \
do contexto da conversa. Ruim: "ele disse que prefere isso". Bom: "Prefere \
Postgres a MySQL em projetos novos porque já conhece a administração."
"""

# Teto por rodada. Uma conversa densa pode render três memórias legítimas;
# mais que isso é quase sempre o modelo picotando o mesmo fato.
MAXIMO_POR_RODADA = 3


async def extract(
    user_message: str,
    assistant_reply: str,
    *,
    escopo: str | None = None,
    historico: list[dict[str, str]] | None = None,
) -> list[dict[str, object]]:
    """Analisa uma rodada e grava o que valer a pena. Devolve o que gravou."""
    if not config.AUTO_LEARN:
        return []

    corrigindo = experiencia.e_correcao(user_message)
    preferindo = experiencia.e_preferencia(user_message)

    if escopo is None:
        escopo = memoria.detectar_projeto(user_message, historico)

    parecidas = await _parecidas(user_message, assistant_reply)
    contexto_parecidas = (
        "\n".join(f"- {a.doc.name}: {a.doc.description}" for a in parecidas)
        or "(nenhuma memória parecida com este trecho)"
    )

    aviso = ""
    if corrigindo:
        aviso = (
            "\nATENÇÃO: a mensagem do usuário parece ser uma CORREÇÃO. Se ela "
            "desfaz algo afirmado por uma das memórias parecidas, preencha "
            "`replaces` com o nome daquela memória.\n"
        )
    elif preferindo:
        aviso = "\nATENÇÃO: o usuário parece estar declarando uma preferência durável.\n"

    prompt = (
        f"MEMÓRIAS PARECIDAS COM ESTE TRECHO:\n{contexto_parecidas}\n"
        f"{aviso}\n"
        f"--- TRECHO DA CONVERSA ---\n\n"
        f"Usuário: {user_message.strip()[:4000]}\n\n"
        f"Assistente: {assistant_reply.strip()[:4000]}\n\n"
        f"--- FIM ---\n\n"
        "O que dessa troca merece virar memória permanente?"
    )

    result = await brain.structured(_SYSTEM, prompt, _SCHEMA)
    if not isinstance(result, dict):
        return []

    raw = result.get("memories")
    if not isinstance(raw, list):
        return []

    nomes_validos = {a.doc.name for a in parecidas}
    saved: list[dict[str, object]] = []

    for item in raw[:MAXIMO_POR_RODADA]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name or not description:
            continue

        # `replaces` só vale apontando para memória que existe e que o modelo
        # de fato viu. Sem essa checagem, um nome alucinado marcaria como
        # superada uma memória que nada tem a ver.
        substitui = str(item.get("replaces") or "").strip()
        if substitui not in nomes_validos:
            substitui = ""

        resultado = await memoria.guardar(
            name,
            description,
            kind=_tipo(item, corrigindo),
            escopo=_escopo(item, escopo),
            importancia=_importancia(item, corrigindo),
            origem="conversa (correção)" if corrigindo else "conversa",
            substitui=substitui or None,
        )
        saved.append({
            "name": resultado["memoria"]["name"],
            "description": resultado["memoria"]["description"],
            "kind": resultado["memoria"]["kind"],
            "scope": resultado["memoria"]["scope"],
            "resultado": resultado["resultado"],
            "substituiu": resultado["substituiu"],
        })

    if saved:
        log.debug("[learner] gravadas=%d correcao=%s", len(saved), corrigindo)
    return saved


async def _parecidas(user_message: str, assistant_reply: str):
    """As memórias que já falam do assunto deste trecho.

    Com um limiar baixo de propósito: aqui não se quer só a quase-idêntica, e
    sim tudo que fale do mesmo tema — é essa lista que permite ao modelo
    apontar qual memória deixou de valer.
    """
    trecho = f"{user_message}\n{assistant_reply}"[:4000]
    try:
        return await memoria.semelhantes(trecho, limiar=0.35, limite=8)
    except Exception:
        return []


def _tipo(item: dict[str, object], corrigindo: bool) -> str:
    from .docs import normalizar_tipo

    bruto = str(item.get("kind") or "").strip()
    if corrigindo and not bruto:
        return "correction"
    return normalizar_tipo(bruto or "fact")


def _escopo(item: dict[str, object], escopo_detectado: str | None) -> str:
    pedido = str(item.get("scope") or "").strip().lower()
    if pedido.startswith("project") and escopo_detectado:
        return escopo_detectado
    if pedido.startswith("project:"):
        return pedido
    return memoria.GLOBAL


def _importancia(item: dict[str, object], corrigindo: bool) -> float:
    try:
        valor = float(item.get("importance") or 0.5)
    except (TypeError, ValueError):
        valor = 0.5
    valor = max(0.0, min(1.0, valor))
    if corrigindo:
        # Correção explícita pesa. É informação de primeira mão, dita por
        # quem sabe, e normalmente desfaz um erro que já custou tempo.
        valor = max(valor, 0.75)
    return valor


async def suggest_title(first_message: str) -> str:
    """Título curto para a conversa, usado na lista lateral."""
    schema = {
        "type": "OBJECT",
        "properties": {"title": {"type": "STRING"}},
        "required": ["title"],
    }
    result = await brain.structured(
        "Você cria títulos curtos para conversas. Máximo 5 palavras, sem aspas, "
        "sem ponto final, em português.",
        f"Crie um título para uma conversa que começa assim:\n\n{first_message[:600]}",
        schema,
    )
    if isinstance(result, dict):
        title = str(result.get("title") or "").strip()
        if title:
            return title[:60]
    return first_message.strip()[:40] or "Nova conversa"
