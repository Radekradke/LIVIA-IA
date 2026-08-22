"""Servidor web: a casca em volta do miolo.

Starlette + SSE. Sem framework pesado, sem build de front-end — a interface é
um único arquivo HTML. Para 1-2 usuários isso é o suficiente e sobe em 1 segundo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route

from . import (
    auth, backup, biblioteca, brain, conhecimento, config, context, db,
    experiencia, ferramentas, knowledge_client, knowledge_ingest,
    knowledge_router, learner, memoria, persona, router, saude, web,
)
from .store import COLECOES, memory, skills

log = logging.getLogger("livia.server")

AJUDA = """\
**Comandos disponíveis**

- `/buscar <termo>` — força uma busca na web antes de responder.
- `/grafo <pergunta>` — força a busca pelo grafo de conhecimento.
- `/lembrar <fato>` — grava uma memória na hora, sem passar pelo modelo.
- `/esquecer <nome>` — apaga a memória com esse nome.
- `/arquivar <nome>` — tira do prompt sem apagar (dá para reativar depois).
- `/memorias` — lista tudo que está guardado.
- `/experiencias` — o que ela já tentou, e como terminou.
- `/licoes` — o que ela concluiu sozinha a partir das experiências.
- `/porque` — por que ela lembrou do que lembrou na última resposta.
- `/manutencao-memoria` — faxina: duplicatas, conflitos, memória esquecida.
- `/ajuda` — mostra isto.

Fora os comandos, é só conversar.

**Sobre a web:** você não precisa pedir. Se colar um link, ela abre e lê. Se a \
pergunta depender de informação atual — preço, notícia, clima —, ela busca \
sozinha e mostra as fontes. O `/buscar` serve só para forçar quando ela não \
achou necessário.

**Sobre a memória:** coisas que valem guardar viram memória sozinhas, e você vê \
um aviso discreto quando isso acontece. Ela não carrega tudo em toda pergunta — \
busca por significado o que tem a ver com o assunto. Se você corrigir alguma \
coisa, a versão antiga é marcada como superada em vez de continuar valendo.

**Sobre a manutenção:** `/manutencao-memoria` só RELATA. Para ela mexer de \
verdade, use `/manutencao-memoria aplicar`.
"""


def _sse(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


# --------------------------------------------------------------------------
# Comandos (atalhos que não gastam chamada de API)
# --------------------------------------------------------------------------


# A procedência da última resposta, por conversa. Fica em memória de
# propósito: serve para responder `/porque` logo depois, e não tem por que
# sobreviver a um reinício.
_ULTIMA_PROCEDENCIA: dict[int, dict[str, object]] = {}


async def _handle_command(text: str, conversa: int = 0) -> str | None:
    """Devolve a resposta se for um comando, ou None se for conversa normal.

    Virou assíncrona porque a manutenção de memória precisa gerar vetores.
    Os comandos continuam sem chamar modelo de linguagem nenhum.
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    command, _, argument = stripped.partition(" ")
    command = command.lower()
    argument = argument.strip()

    # /buscar e /grafo não são atalhos: continuam o fluxo normal de conversa,
    # só que forçando um tipo de busca. Devolver None deixa passar adiante.
    if command in ("/buscar", "/grafo"):
        return None

    if command == "/ajuda":
        return AJUDA

    if command == "/lembrar":
        if not argument:
            return "Use assim: `/lembrar prefiro Postgres a MySQL`"
        name = " ".join(argument.split()[:6])
        resultado = await memoria.guardar(
            name, argument, kind="fact", origem="/lembrar",
            importancia=0.7,  # pediu à mão: vale mais que o que ela deduziu
        )
        doc = resultado["memoria"]
        aviso = ""
        if resultado["resultado"] == "substituida":
            aviso = f"\n\n(isto substituiu **{resultado['substituiu']}**, que dizia quase o mesmo)"
        return f"Gravado como **{doc['name']}**.\n\n> {doc['description']}{aviso}"

    if command == "/esquecer":
        if not argument:
            return "Use assim: `/esquecer prefere-postgres`"
        if memory.delete(argument):
            memoria.sincronizar()
            return f"Apaguei a memória **{argument}**."
        return f"Não achei nenhuma memória chamada **{argument}**. Veja `/memorias`."

    if command == "/arquivar":
        if not argument:
            return "Use assim: `/arquivar prefere-postgres`"
        if memoria.arquivar(argument):
            return (
                f"Arquivei **{argument}**. Ela sai das respostas mas continua no "
                "disco — dá para reativar pelo painel."
            )
        return f"Não achei nenhuma memória chamada **{argument}**."

    if command == "/memorias":
        items = memory.all()
        if not items:
            return "Nenhuma memória gravada ainda."
        linhas = []
        for d in items:
            marca = "" if d.ativa else f"  _({d.status})_"
            escopo = "" if d.scope == "global" else f"  `{d.scope}`"
            linhas.append(f"- **{d.name}** — {d.description}{escopo}{marca}")
        numeros = memoria.estatisticas()
        return (
            f"**{numeros['ativas']} memória(s) ativa(s)** "
            f"(de {numeros['total']} no disco):\n\n" + "\n".join(linhas)
        )

    if command == "/experiencias":
        registros = db.experiencia_listar(15)
        if not registros:
            return "Nenhuma experiência registrada ainda."
        linhas = []
        for e in registros:
            marca = {True: "✔", False: "✘", None: "?"}[e.get("sucesso")]
            linhas.append(f"- {marca} **{e['tarefa']}**")
            if e.get("erro"):
                linhas.append(f"    - {str(e['erro'])[:160]}")
        numeros = experiencia.estatisticas()
        return (
            f"**{numeros['total']} experiência(s)** — {numeros['sucessos']} deram "
            f"certo, {numeros['falhas']} falharam, {numeros['sem_veredito']} sem "
            f"veredito.\n\n" + "\n".join(linhas)
        )

    if command == "/licoes":
        items = COLECOES["lessons"].all()
        if not items:
            return (
                "Nenhuma lição ainda. Elas aparecem quando um padrão se repete "
                "em várias experiências — rode `/manutencao-memoria aplicar` "
                "para ela procurar agora."
            )
        linhas = "\n".join(f"- **{d.name}** — {d.description}" for d in items)
        return f"**{len(items)} lição(ões) que ela deduziu sozinha:**\n\n{linhas}"

    if command == "/porque":
        return _explicar_ultima(conversa)

    if command in ("/manutencao-memoria", "/manutencao"):
        aplicar = argument.lower() in ("aplicar", "aplica", "sim", "vai")
        relatorio = await memoria.manutencao(aplicar=aplicar)
        return _texto_manutencao(relatorio, aplicar)

    return f"Comando `{command}` não existe. Veja `/ajuda`."


def _explicar_ultima(conversa: int) -> str:
    """Fase 22: de onde veio o que ela usou na última resposta."""
    dados = _ULTIMA_PROCEDENCIA.get(conversa)
    if not dados:
        return "Ainda não respondi nada nesta conversa para explicar."

    linhas: list[str] = []

    if dados.get("modo") == "completo":
        # A memória não foi selecionada — mas o conhecimento pode ter sido.
        # São dois sistemas independentes, e cortar a explicação aqui
        # escondia de onde vieram os documentos citados na resposta.
        linhas.append(
            "Sobre a memória: carreguei tudo, sem seleção — é o que acontece "
            "quando não há como gerar vetores (sem Ollama e sem chave do "
            "Gemini). Por isso não dá para apontar qual memória pesou mais."
        )
        if not dados.get("documentos"):
            return linhas[0]

    memorias = dados.get("memorias") or []
    if memorias:
        linhas.append("**Memórias que usei:**")
        for m in memorias:
            linhas.append(f"- **{m['nome']}** — {m['motivo']} (nota {m['nota']})")

    licoes = dados.get("licoes") or []
    if licoes:
        linhas.append("\n**Lições que pesaram:**")
        linhas += [f"- **{l['nome']}** — {l['motivo']}" for l in licoes]

    if dados.get("skills"):
        linhas.append("\n**Skills:** " + ", ".join(dados["skills"]))

    experiencias = dados.get("experiencias") or []
    if experiencias:
        linhas.append("\n**Experiências parecidas:**")
        for e in experiencias:
            marca = {True: "funcionou", False: "falhou", None: "sem veredito"}[e["sucesso"]]
            linhas.append(f"- {e['tarefa']} → {marca}")

    conhecimento = dados.get("conhecimento") or {}
    if dados.get("documentos"):
        modo = str(conhecimento.get("modo") or "vector")
        rotulo = {
            "vector": "por semelhança de texto",
            "graph": "pelo grafo de relações",
            "hybrid": "por semelhança + grafo",
        }.get(modo, modo)
        linhas.append(f"\n**Conhecimento ({rotulo}):**")
        for d in dados["documentos"]:
            linhas.append(f"- {d}")

        if conhecimento.get("relacoes"):
            linhas.append("\n**Relações que levaram até isso:**")
            for caminho in conhecimento["relacoes"][:6]:
                linhas.append("- " + " → ".join(caminho))

        if modo == "hybrid":
            linhas.append(
                f"\n_Busca híbrida: {conhecimento.get('vector_hits', 0)} por texto, "
                f"{conhecimento.get('graph_hits', 0)} pelo grafo, "
                f"{conhecimento.get('duplicados', 0)} repetidos descartados._"
            )

        if conhecimento.get("divergencias"):
            linhas.append("\n**As fontes divergem** — ver o aviso na resposta.")

    if dados.get("busca"):
        linhas.append(f"\n**Busca na web:** \"{dados['busca']}\"")

    if not linhas:
        return (
            "Nada da memória entrou nessa resposta — nem memória, nem lição, nem "
            "documento. Respondi só com o que o modelo já sabe e com a conversa."
        )

    escopo = dados.get("escopo") or "global"
    linhas.append(f"\n_Escopo considerado: {escopo}._")
    return "\n".join(linhas)


def _texto_manutencao(relatorio: dict[str, object], aplicar: bool) -> str:
    partes = ["**Manutenção da memória**", ""]

    duplicatas = relatorio.get("duplicatas") or []
    obsoletas = relatorio.get("obsoletas") or []
    conflitos = relatorio.get("conflitos") or []
    licoes = relatorio.get("licoes") or {}

    partes.append(f"- duplicatas encontradas: {len(duplicatas)}")
    for d in duplicatas[:8]:
        partes.append(f"    - `{d['parecida']}` ≈ `{d['mantida']}` ({d['semelhanca']})")

    partes.append(f"- memórias sem uso há muito tempo: {len(obsoletas)}")
    for o in obsoletas[:8]:
        partes.append(f"    - `{o['nome']}` ({o['dias_sem_uso']} dias)")

    if conflitos:
        partes.append(f"- inconsistências no índice: {len(conflitos)}")

    partes.append(f"- heurísticas possíveis: {len(licoes.get('heuristicas') or [])}")
    partes.append(f"- anti-patterns possíveis: {len(licoes.get('anti_patterns') or [])}")
    candidatas = licoes.get("candidatas") or []
    if candidatas:
        partes.append(f"- skills candidatas propostas: {len(candidatas)} (esperando você aprovar)")

    partes.append("")
    if aplicar:
        partes.append(
            f"Apliquei: {relatorio.get('arquivadas', 0)} arquivada(s), duplicatas "
            "resolvidas por substituição (nada foi apagado) e lições gravadas."
        )
    else:
        partes.append(
            "Isto foi só um relatório — não mexi em nada. Para aplicar, mande "
            "`/manutencao-memoria aplicar`."
        )
    return "\n".join(partes)


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------


async def chat(request: Request) -> Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "corpo inválido"}, status_code=400)

    message = str(payload.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "mensagem vazia"}, status_code=400)

    raw_id = payload.get("conversation_id")
    conversation_id = int(raw_id) if isinstance(raw_id, (int, str)) and str(raw_id).isdigit() else 0
    is_new = not (conversation_id and db.conversation_exists(conversation_id))
    if is_new:
        conversation_id = db.create_conversation()

    async def events() -> AsyncIterator[bytes]:
        db.add_message(conversation_id, "user", message)

        # Comandos respondem na hora, sem chamar o modelo.
        command_reply = await _handle_command(message, conversation_id)
        if command_reply is not None:
            db.add_message(conversation_id, "assistant", command_reply)
            yield _sse({"type": "delta", "text": command_reply})
            yield _sse(
                {
                    "type": "done",
                    "conversation_id": conversation_id,
                    "stats": context.stats(),
                }
            )
            return

        history = db.get_messages(conversation_id, limit=config.HISTORY_TURNS)

        # A montagem do prompt agora depende da PERGUNTA: em vez de despejar
        # tudo, busca por significado o que tem a ver com ela. Se a seleção
        # falhar por qualquer motivo, cai na montagem completa de antes —
        # ficar sem memória seria pior que ficar com memória demais.
        try:
            system_prompt, procedencia = await context.montar(
                message, historico=history[:-1]
            )
        except Exception:
            system_prompt, procedencia = context.build_system_prompt(), {"modo": "completo"}
        _ULTIMA_PROCEDENCIA[conversation_id] = procedencia

        # Contexto extra que será colado antes da pergunta. É uma LISTA de
        # propósito: biblioteca e web podem disparar na mesma pergunta, e
        # sobrescrever uma com a outra descartaria material relevante em
        # silêncio — que foi exatamente o bug da primeira versão disto.
        blocos: list[str] = []

        # --- conhecimento (biblioteca + grafo) -------------------------------
        # O roteador decide entre vetor, grafo e híbrido por heurística local,
        # sem gastar chamada de modelo. Com o grafo desligado ou fora do ar
        # ele vira exatamente a busca vetorial de sempre — é por isso que
        # ligar a funcionalidade não muda nada para quem não a usa.
        if not biblioteca.vazia() or knowledge_client.disponivel():
            # A heurística acerta a maioria, não todas. `/grafo` é a saída
            # manual para quando o André sabe que a pergunta é relacional e a
            # regex não percebeu.
            forcar_grafo = message.strip().lower().startswith("/grafo")
            pergunta_busca = message.strip()[6:].strip() if forcar_grafo else message
            try:
                hits, proc_conhecimento = await knowledge_router.buscar(
                    pergunta_busca,
                    modo=knowledge_router.HYBRID if forcar_grafo else None,
                )
            except Exception as exc:
                # O conhecimento é bônus. Falhar aqui responde sem ele.
                log.debug("[knowledge] busca falhou: %s", exc)
                hits, proc_conhecimento = [], {}

            if hits:
                blocos.append(knowledge_router.formatar(hits, proc_conhecimento))
                vistos: list[str] = []
                for h in hits:
                    rotulo = h.rotulo()
                    if rotulo not in vistos:
                        vistos.append(rotulo)
                procedencia["documentos"] = vistos
                procedencia["conhecimento"] = proc_conhecimento
                yield _sse({"type": "livros", "trechos": vistos})

                # Metadado OPCIONAL: interface antiga ignora sem quebrar.
                if proc_conhecimento.get("modo") != knowledge_router.VECTOR:
                    yield _sse({
                        "type": "knowledge",
                        "mode": proc_conhecimento.get("modo"),
                        "sources": len(vistos),
                        "graph_hits": proc_conhecimento.get("graph_hits", 0),
                    })

        # --- web -----------------------------------------------------------
        # Link colado na mensagem: o modelo lê direto, sem busca nenhuma.
        tem_link = config.WEB_ENABLED and bool(web.urls_em(message))
        consulta = None
        forcada = message.strip().lower().startswith("/buscar")

        if forcada:
            consulta = message.strip()[7:].strip() or None
        elif config.WEB_ENABLED and not tem_link:
            try:
                consulta = await web.precisa_buscar(message)
            except Exception:
                consulta = None

        if consulta:
            yield _sse({"type": "status", "text": f"buscando: {consulta}"})
            resultados = await web.buscar(consulta, config.WEB_RESULTS)
            if resultados:
                blocos.append(web.formatar(consulta, resultados))
                procedencia["busca"] = consulta
                yield _sse({
                    "type": "sources",
                    "label": "resultados da busca",
                    "urls": [r["url"] for r in resultados],
                })
            else:
                yield _sse({"type": "status", "text": "a busca não devolveu nada"})
        elif tem_link:
            yield _sse({"type": "status", "text": "abrindo o link…"})

        # Aplica tudo de uma vez, preservando cada fonte que contribuiu.
        if blocos:
            history = [*history[:-1], {
                "role": "user",
                "content": "\n\n".join(blocos) + f"\n---\n\n{message}",
            }]

        # --- ferramentas ----------------------------------------------------
        # O modelo pode pedir ações antes de responder: ler um arquivo, listar
        # a pasta, calcular. Cada rodada executa o que ele pediu e devolve o
        # resultado; quando ele para de pedir, a resposta final sai em
        # streaming como sempre.
        # As ações executadas viram o registro de experiência lá embaixo. O
        # que conta como sucesso é isto aqui — ferramenta que rodou —, não a
        # impressão de que a resposta ficou boa.
        acoes_feitas: list[dict[str, object]] = []

        # A capacidade é conferida ANTES do laço. Sem isto, uma configuração
        # perfeitamente válida — modo local com um modelo que não chama função
        # — derrubava a resposta inteira com "nenhum provedor sabe usar
        # ferramentas". Não poder usar ferramenta é motivo para responder sem
        # ela, nunca para não responder.
        if config.TOOLS_ENABLED and router.quem_tem(router.TOOLS):
            for _ in range(config.TOOLS_MAX_ROUNDS):
                try:
                    chamadas, eco = await brain.com_ferramentas(
                        system_prompt, history, ferramentas.catalogo()
                    )
                except brain.BrainError as exc:
                    yield _sse({"type": "error", "message": str(exc)})
                    return
                except Exception:
                    break  # ferramenta é bônus; se falhar, responde sem ela

                if not chamadas:
                    break

                history = [*history, *eco]
                for chamada in chamadas:
                    yield _sse({
                        "type": "acao",
                        "texto": ferramentas.resumir(chamada["nome"], chamada["args"]),
                    })
                    resultado, ok = ferramentas.executar(chamada["nome"], chamada["args"])
                    acoes_feitas.append({
                        "nome": chamada["nome"],
                        "ok": ok,
                        "resultado": resultado[:400],
                    })
                    if not ok:
                        yield _sse({"type": "acao", "texto": f"⚠ {resultado}", "falhou": True})
                    history.append({
                        "role": "ferramenta",
                        "id": chamada["id"],
                        "nome": chamada["nome"],
                        "resultado": resultado,
                    })

        perfil = router.classificar(
            message,
            tem_documento=bool(blocos),
        )

        fontes: list[str] = []
        usados: list[str] = []
        chunks: list[str] = []
        try:
            async for piece in brain.stream(
                system_prompt,
                history,
                ler_urls=config.WEB_ENABLED,
                fontes=fontes,
                usados=usados,
                # O roteador classifica a mensagem localmente e diz qual
                # provedor prefere: link vai para quem abre páginas, tarefa
                # técnica para o especialista, conversa curta para o rápido.
                # É só preferência — o brain cuida da fila e do fallback.
                preferir=perfil.preferred_provider,
            ):
                if not chunks and tem_link and usados and usados[0] != "gemini":
                    # Pediu leitura de link, mas quem respondeu não sabe abrir
                    # páginas. Sem este aviso a resposta sai confiante sobre uma
                    # página que ninguém leu — o pior tipo de erro que existe.
                    yield _sse({
                        "type": "status",
                        "text": "não consegui abrir o link (o provedor que lê "
                                "páginas está indisponível) — respondendo sem ele",
                        "fixo": True,
                    })
                elif not chunks and usados and usados[0] != _principal():
                    # O principal falhou e o reserva assumiu. Vale avisar, para
                    # a pessoa entender se a resposta vier com outra cara.
                    yield _sse({
                        "type": "status",
                        "text": f"respondendo pelo reserva ({usados[0]})",
                        "fixo": True,
                    })
                chunks.append(piece)
                yield _sse({"type": "delta", "text": piece})
        except brain.BrainError as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return
        except Exception as exc:  # rede caindo no meio, etc.
            yield _sse({"type": "error", "message": f"Falha inesperada: {exc}"})
            return

        reply = "".join(chunks).strip()
        if not reply:
            yield _sse({"type": "error", "message": "O modelo devolveu uma resposta vazia."})
            return

        db.add_message(conversation_id, "assistant", reply)

        if fontes:
            yield _sse({"type": "sources", "label": "páginas lidas", "urls": fontes})

        for quebrado in saude.quebrados():
            yield _sse({
                "type": "status",
                "text": f"{quebrado} está mal configurado e foi desativado — confira a chave no .env",
                "fixo": True,
            })

        # Só agora, com a resposta já na tela, decidimos o que memorizar.
        escopo = str(procedencia.get("escopo") or "global")
        learned: list[dict[str, object]] = []
        try:
            learned = await learner.extract(
                message, reply,
                escopo=None if escopo == "global" else escopo,
                historico=history[:-1],
            )
        except Exception:
            learned = []  # aprender é bônus; nunca pode quebrar a conversa
        if learned:
            yield _sse({"type": "learned", "memories": learned})

        # E registramos o que foi tentado. O veredito sai de evidência
        # operacional; sem evidência ele fica indefinido, e a experiência
        # não vota em heurística nenhuma.
        try:
            _registrar_experiencia(
                message, reply, acoes_feitas, conversation_id, escopo, history
            )
        except Exception:
            pass  # registrar é bônus; nunca pode derrubar a conversa

        title = None
        if is_new:
            try:
                title = await learner.suggest_title(message)
                db.set_title(conversation_id, title)
            except Exception:
                title = None

        yield _sse(
            {
                "type": "done",
                "conversation_id": conversation_id,
                "title": title,
                "stats": context.stats(),
            }
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _principal() -> str:
    """O provedor que deveria ter atendido, com a configuração de agora.

    Comparar com `config.PROVIDERS[0]` não serve mais: o primeiro da lista
    padrão é o Ollama, e quem não o tem ligado veria "respondendo pelo
    reserva" em toda mensagem, para sempre.
    """
    disponiveis = router.disponiveis()
    return disponiveis[0] if disponiveis else ""


def _registrar_experiencia(
    mensagem: str,
    resposta: str,
    acoes: list[dict[str, object]],
    conversa: int,
    escopo: str,
    history: list[dict[str, str]],
) -> None:
    """Guarda o que foi tentado nesta rodada, e revisa o veredito da anterior.

    A revisão é o pedaço que faz isto valer: quando o André corrige agora, a
    experiência que ficou marcada como sucesso na rodada PASSADA estava errada.
    Sem voltar atrás, o registro premiaria justamente o que não funcionou.
    """
    if experiencia.e_correcao(mensagem):
        anteriores = db.experiencia_listar(1)
        if anteriores and anteriores[0].get("conversa") == conversa:
            db.experiencia_atualizar(
                int(anteriores[0]["id"]),
                sucesso=0,
                feedback=mensagem[:400],
            )

    experiencia.registrar(
        mensagem,
        acoes=acoes,
        resultado=resposta[:2000],
        contexto=" | ".join(
            str(m.get("content") or "")[:120] for m in history[-3:-1]
        ),
        escopo=escopo,
        conversa=conversa,
    )


# --------------------------------------------------------------------------
# Conversas
# --------------------------------------------------------------------------


async def list_conversations(request: Request) -> Response:
    return JSONResponse({"conversations": db.list_conversations()})


async def conversation_messages(request: Request) -> Response:
    conversation_id = int(request.path_params["conversation_id"])
    if not db.conversation_exists(conversation_id):
        return JSONResponse({"error": "conversa não encontrada"}, status_code=404)
    return JSONResponse({"messages": db.get_messages(conversation_id)})


async def delete_conversation(request: Request) -> Response:
    db.delete_conversation(int(request.path_params["conversation_id"]))
    return JSONResponse({"ok": True})


# --------------------------------------------------------------------------
# Memórias e skills
# --------------------------------------------------------------------------


def _store_for(kind: str):
    return COLECOES.get(kind)


def _unknown() -> Response:
    return JSONResponse({"error": "coleção inválida"}, status_code=404)


async def list_docs(request: Request) -> Response:
    store = _store_for(request.path_params["kind"])
    if store is None:
        return _unknown()
    return JSONResponse({"items": [d.to_json() for d in store.all()]})


async def create_doc(request: Request) -> Response:
    store = _store_for(request.path_params["kind"])
    if store is None:
        return _unknown()
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "corpo inválido"}, status_code=400)

    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    body = str(payload.get("body") or "")
    if not name or not description:
        return JSONResponse({"error": "nome e descrição são obrigatórios"}, status_code=400)

    extra = {}
    for campo in ("scope", "status", "importance"):
        if payload.get(campo) is not None:
            extra[campo] = str(payload[campo])

    doc = store.save(
        name, description, body,
        kind=str(payload.get("kind") or "manual"),
        extra=extra or None,
    )
    memoria.sincronizar(request.path_params["kind"])
    return JSONResponse({"item": doc.to_json(), "stats": context.stats()})


async def delete_doc(request: Request) -> Response:
    store = _store_for(request.path_params["kind"])
    if store is None:
        return _unknown()
    ok = store.delete(request.path_params["name"])
    memoria.sincronizar(request.path_params["kind"])
    return JSONResponse({"ok": ok, "stats": context.stats()})


async def get_persona(request: Request) -> Response:
    return JSONResponse({"text": persona.read(), "default": persona.PADRAO})


async def save_persona(request: Request) -> Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "corpo inválido"}, status_code=400)

    if payload.get("reset"):
        return JSONResponse({"text": persona.reset()})

    text = str(payload.get("text") or "").strip()
    if not text:
        return JSONResponse(
            {"error": "a personalidade não pode ficar vazia — use 'restaurar padrão'"},
            status_code=400,
        )
    persona.write(text)
    return JSONResponse({"text": persona.read()})


async def listar_livros(request: Request) -> Response:
    """Os documentos, cada um com o estado dos DOIS índices.

    Vetores e grafo são coisas separadas e podem estar em estados diferentes:
    um documento pode estar buscável por texto e sem grafo nenhum. Mostrar só
    um dos dois faria o André reconstruir a coisa errada quando algo desse
    errado.
    """
    livros = biblioteca.listar()
    ligado = knowledge_client.ligado()
    for livro in livros:
        livro["knowledge"] = knowledge_ingest.estado_de(livro) if ligado else "disabled"
    return JSONResponse({
        "livros": livros,
        "conhecimento": {
            "ligado": ligado,
            "sem_grafo": len(knowledge_ingest.pendentes_de_grafo()),
            "fila": db.job_pendentes() if ligado else 0,
        },
    })


async def apagar_livro(request: Request) -> Response:
    """Apaga da biblioteca e pede a remoção no grafo.

    A remoção local NUNCA espera pelo grafo: com o serviço offline, o pedido
    fica registrado como tombstone e sai na próxima vez que a fila rodar.
    Sem isso, o grafo guardaria trechos de um documento que não existe mais e
    eles apareceriam em respostas futuras — o pior tipo de fantasma.
    """
    slug = request.path_params["slug"]
    ok = biblioteca.remover(slug)
    if ok:
        knowledge_ingest.agendar_remocao(slug)
        try:
            await knowledge_ingest.processar_fila(1)
        except Exception:
            pass          # o tombstone fica na fila para a próxima
    return JSONResponse({"ok": ok})


async def enviar_livro(request: Request) -> Response:
    """Recebe o arquivo e relata o progresso enquanto processa.

    Um livro de 500 páginas leva ~30s. Sem retorno visual, a pessoa acha que
    travou e recarrega a página no meio — por isso o progresso vai por SSE,
    como no chat.
    """
    nome = request.headers.get("x-nome-arquivo", "documento.pdf")
    dados = await request.body()
    if not dados:
        return JSONResponse({"error": "arquivo vazio"}, status_code=400)

    async def eventos() -> AsyncIterator[bytes]:
        try:
            async for passo in biblioteca.adicionar(nome, dados):
                # A biblioteca é a principal. Ela termina primeiro, e só
                # depois o grafo entra na fila — construir grafo leva minutos
                # e mataria a conexão deste upload.
                if passo.get("etapa") == "pronto":
                    passo = _com_grafo(passo)
                yield _sse(passo)
        except biblioteca.BibliotecaError as exc:
            yield _sse({"etapa": "erro", "texto": str(exc)})
        except Exception as exc:
            yield _sse({"etapa": "erro", "texto": f"Falha inesperada: {exc}"})

    return StreamingResponse(
        eventos(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _com_grafo(passo: dict[str, object]) -> dict[str, object]:
    """Depois que a biblioteca terminou, o grafo entra na fila.

    Nunca ao contrário, e nunca com rollback: se o grafo falhar, o documento
    continua indexado e buscável. Perder a indexação porque o serviço
    opcional não subiu trocaria o que funciona pelo que é bônus.
    """
    livro = passo.get("livro")
    if not isinstance(livro, dict):
        return passo
    slug = str(livro.get("slug") or "")
    if slug and knowledge_ingest.agendar(slug):
        livro["knowledge_status"] = "pending"
        passo["grafo"] = "na fila"
    return passo


# --------------------------------------------------------------------------
# Knowledge Engine
# --------------------------------------------------------------------------


async def knowledge_status(request: Request) -> Response:
    """Estado do grafo: serviço, modelos e documentos."""
    return JSONResponse({
        "servico": await knowledge_client.status(),
        "resumo": knowledge_ingest.resumo(),
        "jobs": db.job_listar(20) if knowledge_client.ligado() else [],
    })


async def knowledge_processar(request: Request) -> Response:
    """Roda a fila. É o botão "construir conhecimento agora".

    Existe como ação explícita porque uma biblioteca de 23 documentos levaria
    horas de CPU, e nada disso pode começar sozinho num boot.
    """
    if not knowledge_client.ligado():
        return JSONResponse(
            {"error": knowledge_client.impedimento()}, status_code=409
        )

    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}

    # Enfileira o que ainda não tem grafo, se pedirem.
    if payload.get("tudo"):
        for slug in knowledge_ingest.pendentes_de_grafo():
            knowledge_ingest.agendar(slug)

    limite = 50
    try:
        limite = max(1, min(200, int(payload.get("limite") or 50)))
    except (TypeError, ValueError):
        pass

    resultado = await knowledge_ingest.processar_fila(limite)
    return JSONResponse(resultado)


async def knowledge_reconstruir(request: Request) -> Response:
    """Reconstrói o grafo de UM documento.

    Diferente de reindexar embeddings, que é outra ação e outro botão: uma
    mexe nos vetores da biblioteca, a outra no grafo. Confundir as duas faria
    o André refazer a coisa errada quando algo desse errado.
    """
    slug = request.path_params["slug"]
    if not knowledge_client.ligado():
        return JSONResponse(
            {"error": knowledge_client.impedimento()}, status_code=409
        )

    knowledge_ingest.agendar(slug, "ingest")
    resultado = await knowledge_ingest.processar_fila(1)
    return JSONResponse({"ok": resultado["feitos"] == 1, **resultado})


async def baixar_backup(request: Request) -> Response:
    dados, nome = backup.exportar()
    return Response(
        dados,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


async def restaurar_backup(request: Request) -> Response:
    dados = await request.body()
    if not dados:
        return JSONResponse({"error": "arquivo vazio"}, status_code=400)
    try:
        contagem = backup.importar(dados)
    except Exception as exc:
        return JSONResponse(
            {"error": f"não consegui ler o backup: {exc}"}, status_code=400
        )
    return JSONResponse({"restaurado": contagem, "stats": context.stats()})


async def diagnostico(request: Request) -> Response:
    """Retrato do sistema para a interface. Nunca inclui segredo."""
    ws = config.WORKSPACE
    try:
        ws.mkdir(parents=True, exist_ok=True)
        teste = ws / ".escrita-teste"
        teste.write_text("x", encoding="utf-8")
        teste.unlink()
        gravavel = True
    except OSError:
        gravavel = False

    from . import embeddings

    return JSONResponse({
        "provedores": saude.diagnostico(),
        "workspace": {"gravavel": gravavel, "pasta": ws.name},
        "banco": {"disponivel": config.DB_PATH.exists()},
        "biblioteca": {
            "documentos": len(biblioteca.listar()),
            "precisam_reconstruir": biblioteca.incompativeis(),
        },
        "ferramentas": {"ligadas": config.TOOLS_ENABLED},
        "web": {"ligada": config.WEB_ENABLED, "buscador": web.provedor_de_busca()},
        "local": {
            "somente_local": config.LOCAL_ONLY,
            "ollama": await saude.checar_ollama(),
        },
        "embeddings": {
            "provedor": (embeddings.provedores() or ["nenhum"])[0],
            "disponivel": embeddings.disponivel(),
            "assinatura": embeddings.assinatura() if embeddings.disponivel() else "",
        },
        "memoria": memoria.estatisticas(),
        "experiencias": experiencia.estatisticas(),
        "conhecimento": {
            **knowledge_ingest.resumo(),
            "servico": await knowledge_client.status(),
        },
    })


async def health_check(request: Request) -> Response:
    """Endpoint que hospedagens consultam para saber se o app está de pé.

    O nome Python NÃO pode ser `saude`: isso sombrearia o módulo homônimo,
    e `saude.diagnostico()` passaria a chamar um atributo de função. Foi
    exatamente o que aconteceu, e só um teste denunciou.
    """
    return JSONResponse({"ok": True})


async def status(request: Request) -> Response:
    from . import embeddings

    return JSONResponse(
        {
            "user": config.USER_NAME,
            "name": config.ASSISTANT_NAME,
            "model": config.OLLAMA_MODEL if config.LOCAL_ONLY else config.MODEL,
            "auto_learn": config.AUTO_LEARN,
            # "tem como responder" — não é mais só a chave do Gemini: com o
            # Ollama ligado, a Livia funciona sem chave nenhuma.
            "has_key": bool(router.disponiveis()),
            "local_only": config.LOCAL_ONLY,
            "semantic": config.SEMANTIC_MEMORY and embeddings.disponivel(),
            "candidatas": len(experiencia.candidatas()),
            **context.stats(),
        }
    )


async def index(request: Request) -> Response:
    return FileResponse(config.WEB_DIR / "index.html")


# --------------------------------------------------------------------------
# Aplicativo instalável (PWA)
# --------------------------------------------------------------------------
#
# O que isto entrega e o que NÃO entrega: ver o cabeçalho de web/sw.js. Em
# resumo — a Livia vira um aplicativo de janela própria que abre na hora e
# deixa LER o que já foi visto com o servidor desligado. Conversar offline
# não existe e nenhum cache resolve: quem pensa é o Python.


async def manifesto(request: Request) -> Response:
    """O manifesto é GERADO, não é arquivo estático.

    Porque o nome dela é configurável: quem puser LIVIA_NAME=Ada instala um
    aplicativo chamado Ada, com o mesmo cuidado que a aba do navegador e o
    cabeçalho já têm.
    """
    nome = config.ASSISTANT_NAME
    return JSONResponse(
        {
            "name": f"{nome} — assistente pessoal",
            "short_name": nome,
            "description": (
                f"{nome} roda na sua máquina. Memória em arquivos de texto, "
                "e opção de funcionar sem internet nenhuma."
            ),
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "any",
            "lang": "pt-BR",
            "dir": "ltr",
            # A cor da barra é a do tema escuro, que é o padrão de fábrica.
            # A variante clara é tratada por <meta name="theme-color"> com
            # media query, que o manifesto não sabe fazer.
            "background_color": "#140a0c",
            "theme_color": "#140a0c",
            "categories": ["productivity", "utilities"],
            "icons": [
                {"src": "/icones/icone-192.png", "sizes": "192x192",
                 "type": "image/png", "purpose": "any"},
                {"src": "/icones/icone-512.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "any"},
                # `maskable` tem margem: o Android recorta o ícone num
                # círculo e comeria as pontas da gema sem ela.
                {"src": "/icones/icone-192-mask.png", "sizes": "192x192",
                 "type": "image/png", "purpose": "maskable"},
                {"src": "/icones/icone-512-mask.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
                {"src": "/icones/livia.svg", "sizes": "any",
                 "type": "image/svg+xml", "purpose": "any"},
            ],
        },
        media_type="application/manifest+json",
    )


async def serviceworker(request: Request) -> Response:
    """O worker precisa ser servido da RAIZ para valer no site inteiro.

    Um `/static/sw.js` só controlaria `/static/`. E ele nunca pode ser
    cacheado pelo navegador: é justamente o arquivo que precisa mudar para
    consertar um cache errado.
    """
    return FileResponse(
        config.WEB_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


async def icone(request: Request) -> Response:
    """Ícones do aplicativo. Nome conferido contra uma lista fechada.

    A alternativa — montar o caminho com o que veio na URL — deixaria
    `/icones/../../.env` valer. Aqui vale a mesma regra das ferramentas: o
    caminho vem de fora, então quem confere é o código.
    """
    permitidos = {
        "livia.svg": "image/svg+xml",
        "icone-192.png": "image/png",
        "icone-512.png": "image/png",
        "icone-192-mask.png": "image/png",
        "icone-512-mask.png": "image/png",
        "apple-touch-icon.png": "image/png",
    }
    nome = request.path_params["nome"]
    tipo = permitidos.get(nome)
    if tipo is None:
        return PlainTextResponse("não encontrado", status_code=404)

    caminho = config.WEB_DIR / "icones" / nome
    if not caminho.exists():
        return PlainTextResponse("não encontrado", status_code=404)
    return FileResponse(
        caminho,
        media_type=tipo,
        headers={"Cache-Control": "public, max-age=604800"},
    )


# --------------------------------------------------------------------------
# Memória, experiências e lições (painel)
# --------------------------------------------------------------------------


async def alterar_doc(request: Request) -> Response:
    """Arquivar, reativar ou mudar escopo/importância de um item.

    Existe separado de `create_doc` porque mudar o estado de uma memória não
    é reescrevê-la: o corpo que o André editou à mão tem que sobreviver.
    """
    kind = request.path_params["kind"]
    store = _store_for(kind)
    if store is None:
        return _unknown()

    nome = request.path_params["name"]
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "corpo inválido"}, status_code=400)

    acao = str(payload.get("acao") or "").strip()
    if acao == "arquivar":
        ok = memoria.arquivar(nome, colecao=kind)
    elif acao == "reativar":
        ok = memoria.reativar(nome, colecao=kind)
    elif acao == "substituir":
        ok = memoria.substituir(nome, str(payload.get("por") or ""), colecao=kind)
    else:
        campos = {
            c: payload[c] for c in ("scope", "importance", "description", "kind")
            if payload.get(c) is not None
        }
        if not campos:
            return JSONResponse({"error": "nada para alterar"}, status_code=400)
        ok = store.patch(nome, **campos) is not None
        memoria.sincronizar(kind)

    if not ok:
        return JSONResponse({"error": "item não encontrado"}, status_code=404)

    doc = store.get(nome)
    return JSONResponse({
        "item": doc.to_json() if doc else None,
        "stats": context.stats(),
    })


async def detalhar_doc(request: Request) -> Response:
    """Um item com o que o índice sabe dele: usos, última vez, origem."""
    kind = request.path_params["kind"]
    store = _store_for(kind)
    if store is None:
        return _unknown()

    doc = store.get(request.path_params["name"])
    if doc is None:
        return JSONResponse({"error": "item não encontrado"}, status_code=404)

    linha = db.memoria_linha(doc.name, kind) or {}
    return JSONResponse({
        "item": doc.to_json(),
        "indice": {
            "usos": linha.get("usos", 0),
            "usado_em": linha.get("usado_em", ""),
            "criado_em": linha.get("criado_em", ""),
            "atualizado_em": linha.get("atualizado_em", ""),
            "vetorizada": linha.get("vetor") is not None,
        },
    })


async def listar_experiencias(request: Request) -> Response:
    registros = db.experiencia_listar(60)
    return JSONResponse({
        "experiencias": [
            {
                "id": e["id"],
                "criado_em": e["criado_em"],
                "tarefa": e["tarefa"],
                "sucesso": e["sucesso"],
                "erro": e["erro"],
                "licao": e["licao"],
                "escopo": e["escopo"],
                "acoes": [str(a.get("nome") or "") for a in e["acoes"]],
            }
            for e in registros
        ],
        "resumo": experiencia.estatisticas(),
    })


async def apagar_experiencia(request: Request) -> Response:
    ok = db.experiencia_apagar(int(request.path_params["id"]))
    return JSONResponse({"ok": ok})


async def listar_candidatas(request: Request) -> Response:
    return JSONResponse({"candidatas": experiencia.candidatas()})


async def decidir_candidata(request: Request) -> Response:
    """Aprovar ou rejeitar uma skill que ela propôs.

    O ponto de controle humano da fase 12: nada vira procedimento permanente
    sem alguém dizer que sim.
    """
    id_ = int(request.path_params["id"])
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}

    if str(payload.get("acao") or "").strip() == "aprovar":
        resultado = experiencia.aprovar(id_)
        if resultado is None:
            return JSONResponse({"error": "candidata não encontrada"}, status_code=404)
        return JSONResponse({"skill": resultado, "stats": context.stats()})

    if not experiencia.rejeitar(id_):
        return JSONResponse({"error": "candidata não encontrada"}, status_code=404)
    return JSONResponse({"ok": True})


async def manutencao_memoria(request: Request) -> Response:
    """Faxina. Só aplica se o pedido disser explicitamente `aplicar: true`."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}
    relatorio = await memoria.manutencao(aplicar=bool(payload.get("aplicar")))
    return JSONResponse(relatorio)


async def importar_projeto(request: Request) -> Response:
    """Indexa uma pasta do workspace, relatando o progresso por SSE."""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        payload = {}
    pasta = str(payload.get("pasta") or "").strip()

    async def eventos() -> AsyncIterator[bytes]:
        try:
            async for passo in conhecimento.importar(pasta):
                yield _sse(passo)
        except (conhecimento.ConhecimentoError, biblioteca.BibliotecaError) as exc:
            yield _sse({"etapa": "erro", "texto": str(exc)})
        except Exception as exc:
            yield _sse({"etapa": "erro", "texto": f"Falha inesperada: {exc}"})

    return StreamingResponse(
        eventos(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def listar_projetos(request: Request) -> Response:
    return JSONResponse({"pastas": conhecimento.pastas_candidatas()})


async def reconstruir_indice(request: Request) -> Response:
    """Recalcula os vetores de um documento cujo gerador mudou."""
    slug = request.path_params["slug"]

    async def eventos() -> AsyncIterator[bytes]:
        try:
            async for passo in biblioteca.reindexar(slug):
                yield _sse(passo)
        except biblioteca.BibliotecaError as exc:
            yield _sse({"etapa": "erro", "texto": str(exc)})
        except Exception as exc:
            yield _sse({"etapa": "erro", "texto": f"Falha inesperada: {exc}"})

    return StreamingResponse(
        eventos(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# Acesso
# --------------------------------------------------------------------------

# Rotas que dispensam sessão. As três novas entram porque o navegador busca
# manifesto e ícone SEM enviar cookie — deixá-las atrás da senha faria o
# aplicativo simplesmente não aparecer como instalável, sem erro nenhum
# visível. Nenhuma delas devolve conteúdo seu: são o nome dela, a gema e a
# lógica de cache.
LIVRES = {"/entrar", "/api/entrar", "/saude", "/manifest.webmanifest", "/sw.js"}


def _livre(caminho: str) -> bool:
    return caminho in LIVRES or caminho.startswith("/icones/")


_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

SEM_SENHA = (
    "Esta Livia está sem senha e por isso só aceita acesso local.\n\n"
    "Para liberar acesso de fora, defina LIVIA_PASSWORD nas variáveis de "
    "ambiente e reinicie. Sem isso, qualquer pessoa que alcançasse este "
    "endereço leria suas conversas e memórias."
)


def _e_local(request: Request) -> bool:
    cliente = request.client.host if request.client else ""
    return cliente in _LOOPBACK


class ExigirSenha(BaseHTTPMiddleware):
    """Duas travas, nesta ordem:

    1. Sem senha configurada, só atende quem vem da própria máquina. Isso vale
       mesmo rodando por `uvicorn` direto (Docker, hospedagem), onde a checagem
       do run.py não acontece — a falha aqui seria silenciosa e cara.
    2. Com senha configurada, exige sessão válida em tudo que não seja a tela
       de entrada.
    """

    async def dispatch(self, request: Request, call_next):
        if not auth.protegido():
            if _e_local(request) or request.url.path == "/saude":
                return await call_next(request)
            return PlainTextResponse(SEM_SENHA, status_code=403)

        if _livre(request.url.path):
            return await call_next(request)

        if auth.token_valido(request.cookies.get(auth.COOKIE)):
            return await call_next(request)

        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "sessão expirada"}, status_code=401)
        return RedirectResponse("/entrar", status_code=303)


async def pagina_entrar(request: Request) -> Response:
    if not auth.protegido():
        return RedirectResponse("/", status_code=303)
    return FileResponse(config.WEB_DIR / "entrar.html")


def _via_https(request: Request) -> bool:
    """A conexão do navegador até aqui é HTTPS?

    Detectar isto importa mais do que parece. Um cookie marcado como `secure`
    só volta por HTTPS; marcá-lo assim numa conexão HTTP produz o pior tipo de
    falha: o login funciona, o cookie é ignorado no pedido seguinte, e a pessoa
    cai de novo na tela de senha — em loop, sem mensagem de erro nenhuma.

    Por isso não confiamos numa variável de ambiente: quem sabe a resposta é o
    pedido. Atrás de túnel ou proxy, a conexão chega aqui como HTTP simples e a
    informação real vem no cabeçalho X-Forwarded-Proto.
    """
    encaminhado = request.headers.get("x-forwarded-proto", "")
    if encaminhado.split(",")[0].strip().lower() == "https":
        return True
    return request.url.scheme == "https"


async def fazer_login(request: Request) -> Response:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "corpo inválido"}, status_code=400)

    # Freio simples contra tentativa em massa. Não é rate limit de verdade,
    # mas transforma força bruta em algo inviavelmente lento.
    await asyncio.sleep(0.6)

    if not auth.senha_confere(str(payload.get("senha") or "")):
        return JSONResponse({"error": "senha incorreta"}, status_code=401)

    resposta = JSONResponse({"ok": True})
    resposta.set_cookie(
        auth.COOKIE,
        auth.criar_token(),
        max_age=auth.DURACAO,
        httponly=True,
        samesite="lax",
        secure=_via_https(request),
    )
    return resposta


async def sair(request: Request) -> Response:
    resposta = RedirectResponse("/entrar", status_code=303)
    resposta.delete_cookie(auth.COOKIE)
    return resposta


routes = [
    Route("/", index),
    Route("/manifest.webmanifest", manifesto),
    Route("/sw.js", serviceworker),
    Route("/icones/{nome:str}", icone),
    Route("/entrar", pagina_entrar),
    Route("/api/entrar", fazer_login, methods=["POST"]),
    Route("/sair", sair),
    Route("/api/status", status),
    Route("/api/chat", chat, methods=["POST"]),
    Route("/api/conversations", list_conversations),
    Route("/api/conversations/{conversation_id:int}", conversation_messages),
    Route("/api/conversations/{conversation_id:int}", delete_conversation, methods=["DELETE"]),
    Route("/saude", health_check),
    Route("/api/diagnostico", diagnostico),
    Route("/api/biblioteca", listar_livros),
    Route("/api/biblioteca", enviar_livro, methods=["POST"]),
    Route("/api/biblioteca/{slug:str}", apagar_livro, methods=["DELETE"]),
    Route("/api/backup", baixar_backup),
    Route("/api/backup", restaurar_backup, methods=["POST"]),
    Route("/api/persona", get_persona),
    Route("/api/persona", save_persona, methods=["POST"]),
    Route("/api/store/{kind:str}", list_docs),
    Route("/api/store/{kind:str}", create_doc, methods=["POST"]),
    Route("/api/store/{kind:str}/{name:str}", detalhar_doc),
    Route("/api/store/{kind:str}/{name:str}", alterar_doc, methods=["PATCH"]),
    Route("/api/store/{kind:str}/{name:str}", delete_doc, methods=["DELETE"]),
    Route("/api/experiencias", listar_experiencias),
    Route("/api/experiencias/{id:int}", apagar_experiencia, methods=["DELETE"]),
    Route("/api/candidatas", listar_candidatas),
    Route("/api/candidatas/{id:int}", decidir_candidata, methods=["POST"]),
    Route("/api/manutencao", manutencao_memoria, methods=["POST"]),
    Route("/api/projetos", listar_projetos),
    Route("/api/projetos", importar_projeto, methods=["POST"]),
    Route("/api/biblioteca/{slug:str}/reindexar", reconstruir_indice, methods=["POST"]),
    Route("/api/conhecimento", knowledge_status),
    Route("/api/conhecimento/processar", knowledge_processar, methods=["POST"]),
    Route("/api/conhecimento/{slug:str}/reconstruir", knowledge_reconstruir,
          methods=["POST"]),
]

@asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    db.init()

    # Migração automática (fase 34): quem atualiza a Livia abre a versão nova
    # e continua usando. O índice se alinha com os arquivos que já existem, e
    # os vetores são gerados sob demanda na primeira pergunta — gerar tudo
    # aqui deixaria a subida travada por minutos numa biblioteca grande.
    for colecao in COLECOES:
        try:
            memoria.sincronizar(colecao)
        except Exception:
            pass  # índice é derivado; falhar aqui não pode impedir o app de subir

    # Jobs de grafo interrompidos por um reinício voltam para a fila. NÃO
    # começam a rodar sozinhos: quem manda processar é o André.
    try:
        recuperados = knowledge_ingest.recuperar_apos_reinicio()
        if recuperados:
            log.info("[knowledge] %d job(s) voltaram para a fila", recuperados)
    except Exception:
        pass

    yield


app = Starlette(
    routes=routes,
    lifespan=lifespan,
    middleware=[Middleware(ExigirSenha)],
)
