"""Aprendizado automático: decidir o que da conversa merece virar memória.

Roda depois que a resposta já foi entregue, então nunca deixa o chat mais lento.
Usa o modelo barato: a tarefa é de triagem, não exige inteligência de ponta.

O critério é deliberadamente rígido. Uma memória boa é uma que ainda vai ser
verdade daqui a três meses. Salvar demais é pior que salvar de menos: cada
memória entra em TODA conversa futura, então lixo acumulado degrada tudo.
"""

from __future__ import annotations

from . import brain, config
from .store import memory

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
                        "description": "Um de: preferencia, projeto, correcao, referencia",
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
- Referências permanentes (links, caminhos, nomes de sistemas internos).

NÃO GUARDE:
- O assunto da conversa em si, resumos ou "conversamos sobre X".
- Conhecimento geral que qualquer modelo já tem.
- Perguntas pontuais e coisas que só valem para hoje.
- Qualquer coisa parecida com uma memória que já existe (a lista vem abaixo).
- Senhas, chaves de API ou dados sensíveis. Nunca. Sem exceção.

Na dúvida, não guarde. Devolver uma lista vazia é o resultado mais comum e \
está perfeitamente correto.

Escreva cada descrição como um fato acabado, em terceira pessoa, sem depender \
do contexto da conversa. Ruim: "ele disse que prefere isso". Bom: "Prefere \
Postgres a MySQL em projetos novos porque já conhece a administração."
"""


async def extract(user_message: str, assistant_reply: str) -> list[dict[str, str]]:
    """Analisa uma rodada e grava o que valer a pena. Devolve o que gravou."""
    if not config.AUTO_LEARN:
        return []

    existing = memory.all()
    index = "\n".join(d.index_line() for d in existing) or "(nenhuma memória ainda)"

    prompt = (
        f"MEMÓRIAS QUE JÁ EXISTEM:\n{index}\n\n"
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

    saved: list[dict[str, str]] = []
    for item in raw[:3]:  # teto por rodada, para nunca virar enxurrada
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name or not description:
            continue
        kind = str(item.get("kind") or "nota").strip() or "nota"
        doc = memory.save(name, description, kind=kind)
        saved.append({"name": doc.name, "description": doc.description, "kind": kind})

    return saved


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
