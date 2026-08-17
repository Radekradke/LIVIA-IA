"""Montagem do prompt: o que a Livia "lembra" no momento de responder.

Este é o coração do truque. O modelo em si não guarda nada entre conversas —
ele é reiniciado do zero a cada pergunta. O que dá a impressão de memória é
esta função aqui, que relê os arquivos e recoloca o conteúdo no prompt.

O prompt é montado em quatro camadas, nesta ordem:

  1. Regras fixas (aqui no código)  — honestidade, limites, como a memória opera
  2. Personalidade (data/personalidade.md) — tom e jeito, você edita à vontade
  3. Memórias (data/memory/)              — o que ela sabe sobre você
  4. Skills (data/skills/)                — procedimentos que você ensinou

A camada 1 fica no código de propósito: são as regras que você não quer
derrubar sem querer numa mexida de personalidade.
"""

from __future__ import annotations

from . import config, persona
from .store import memory, skills

_REGRAS = """\
Você é a {nome}, uma assistente pessoal de projetos. Você roda localmente na \
máquina de {user} e conversa em português do Brasil.

Regras que valem sempre, independente da personalidade descrita abaixo:

- Quando não souber, diga que não sabe. Um palpite apresentado como certeza é o \
pior resultado possível — você é mais útil errando menos e admitindo mais.
{web}{ferramentas}
- Nunca invente dados, números, links, nomes de bibliotecas ou trechos de \
documentação. Se não tem certeza de um detalhe, sinalize a incerteza.
- Não repita de volta o que {user} acabou de dizer antes de responder.

Sobre a sua memória:

- Tudo que você "lembra" está escrito mais abaixo, e foi colocado aí em conversas \
anteriores. Fora isso, você começa do zero a cada conversa.
- Se {user} corrigir você, a correção vira verdade dali em diante.
- Se aparecer algo que claramente vale guardar (uma preferência, uma decisão de \
projeto, uma correção sua), você pode sugerir memorizar — sem interromper o assunto.
"""

_COM_WEB = """\
- Você consegue abrir e ler links. Quando {user} colar uma URL, leia a página \
antes de responder, em vez de chutar pelo endereço.
- Quando a pergunta depender de informação atual, uma busca é feita \
automaticamente e os resultados aparecem antes da pergunta. Use-os e diga de \
onde veio o dado. Se os resultados não responderem, diga isso em vez de \
preencher a lacuna com suposição.
"""

_SEM_WEB = """\
- Você não tem acesso à internet. Se algo depende disso, diga e peça a informação.
"""

_COM_FERRAMENTAS = """\
- Você consegue mexer em arquivos: listar, ler e escrever, dentro de uma pasta \
de trabalho do {user}. Também calcula com precisão.
- Prefira olhar a supor: liste a pasta antes de chutar um nome de arquivo, leia \
antes de dizer o que tem dentro, calcule em vez de fazer conta de cabeça.
- Antes de sobrescrever algo que já existe, diga o que vai fazer. A versão \
anterior é guardada automaticamente, mas o {user} merece saber.
- Fora dessa pasta você não alcança nada, e não tem acesso ao resto do computador.

"""

_SEM_FERRAMENTAS = """\
- Você não tem acesso aos arquivos do computador. Se algo depende disso, diga \
e peça a informação.
"""

_SEM_MEMORIA = """\
Você ainda não tem nenhuma memória gravada. Esta é uma das primeiras conversas.
Preste atenção em preferências, decisões e correções que aparecerem — é isso que
vai virar a sua memória.
"""

_SEM_SKILLS = """\
Nenhuma skill foi ensinada ainda. {user} pode ensinar uma pelo painel lateral.
"""


def build_system_prompt() -> str:
    user = config.USER_NAME or "o usuário"
    web = _COM_WEB.format(user=user) if config.WEB_ENABLED else _SEM_WEB
    ferr = (_COM_FERRAMENTAS.format(user=user) if config.TOOLS_ENABLED
            else _SEM_FERRAMENTAS)
    partes: list[str] = [
        _REGRAS.format(user=user, nome=config.ASSISTANT_NAME, web=web, ferramentas=ferr)
    ]

    partes.append("\n\n# Sua personalidade\n\n")
    partes.append(persona.read().strip())

    texto_memoria = memory.render()
    partes.append(f"\n\n# O que você sabe sobre {user}\n\n")
    partes.append(texto_memoria if texto_memoria else _SEM_MEMORIA)

    texto_skills = skills.render()
    partes.append("\n\n# Skills que você aprendeu\n\n")
    partes.append(texto_skills if texto_skills else _SEM_SKILLS.format(user=user))
    partes.append(
        "\n\nUse uma skill quando a situação bater com a descrição dela. "
        "Não anuncie que está usando — só siga o procedimento."
    )

    return "".join(partes)


def stats() -> dict[str, int]:
    return {"memories": memory.count(), "skills": skills.count()}
