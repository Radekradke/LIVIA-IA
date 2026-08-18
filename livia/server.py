"""Servidor web: a casca em volta do miolo.

Starlette + SSE. Sem framework pesado, sem build de front-end — a interface é
um único arquivo HTML. Para 1-2 usuários isso é o suficiente e sobe em 1 segundo.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
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
    arquivos, auth, backup, biblioteca, brain, config, context, db,
    ferramentas, learner, persona, router, saude, segredos, web,
)
from .store import memory, skills

AJUDA = """\
**Comandos disponíveis**

- `/buscar <termo>` — força uma busca na web antes de responder.
- `/lembrar <fato>` — grava uma memória na hora, sem passar pelo modelo.
- `/esquecer <nome>` — apaga a memória com esse nome.
- `/memorias` — lista tudo que está guardado.
- `/ajuda` — mostra isto.

Fora os comandos, é só conversar.

**Sobre a web:** você não precisa pedir. Se colar um link, ela abre e lê. Se a \
pergunta depender de informação atual — preço, notícia, clima —, ela busca \
sozinha e mostra as fontes. O `/buscar` serve só para forçar quando ela não \
achou necessário.

**Sobre a memória:** coisas que valem guardar viram memória sozinhas, e você vê \
um aviso discreto quando isso acontece.
"""


def _sse(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


# --------------------------------------------------------------------------
# Comandos (atalhos que não gastam chamada de API)
# --------------------------------------------------------------------------


def _handle_command(text: str) -> str | None:
    """Devolve a resposta se for um comando, ou None se for conversa normal."""
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None

    command, _, argument = stripped.partition(" ")
    command = command.lower()
    argument = argument.strip()

    # /buscar não é atalho: continua o fluxo normal de conversa, só que
    # forçando a busca na web. Devolver None deixa passar adiante.
    if command == "/buscar":
        return None

    if command == "/ajuda":
        return AJUDA

    if command == "/lembrar":
        if not argument:
            return "Use assim: `/lembrar prefiro Postgres a MySQL`"
        achados = segredos.conferir(argument)
        if achados:
            # Recusar o pedido explícito do André é uma escolha: guardar
            # seria pior para ele do que a recusa, e o motivo vai dito.
            return segredos.motivo(achados)

        name = " ".join(argument.split()[:6])
        doc = memory.save(name, argument, kind="manual")
        return f"Gravado como **{doc.name}**.\n\n> {doc.description}"

    if command == "/esquecer":
        if not argument:
            return "Use assim: `/esquecer prefere-postgres`"
        if memory.delete(argument):
            return f"Apaguei a memória **{argument}**."
        return f"Não achei nenhuma memória chamada **{argument}**. Veja `/memorias`."

    if command == "/memorias":
        items = memory.all()
        if not items:
            return "Nenhuma memória gravada ainda."
        linhas = "\n".join(f"- **{d.name}** — {d.description}" for d in items)
        return f"**{len(items)} memória(s):**\n\n{linhas}"

    return f"Comando `{command}` não existe. Veja `/ajuda`."


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
        command_reply = _handle_command(message)
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
        system_prompt = context.build_system_prompt()

        # Contexto extra que será colado antes da pergunta. É uma LISTA de
        # propósito: biblioteca e web podem disparar na mesma pergunta, e
        # sobrescrever uma com a outra descartaria material relevante em
        # silêncio — que foi exatamente o bug da primeira versão disto.
        blocos: list[str] = []

        # --- biblioteca ----------------------------------------------------
        # Busca sempre (é local e barato); injeta só o que passar do limiar.
        if not biblioteca.vazia():
            try:
                achados = await biblioteca.buscar(message)
            except Exception:
                achados = []
            if achados:
                blocos.append(biblioteca.formatar(achados))
                vistos: list[str] = []
                for a in achados:
                    rotulo = str(a["livro"])
                    if a["pagina"]:
                        rotulo += f" p.{a['pagina']}"
                    if rotulo not in vistos:
                        vistos.append(rotulo)
                yield _sse({"type": "livros", "trechos": vistos})

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
        if config.TOOLS_ENABLED:
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
                    if not ok:
                        yield _sse({"type": "acao", "texto": f"⚠ {resultado}", "falhou": True})

                    # Arquivo gravado vira cartão com link de download. A
                    # confirmação é no disco: se a gravação falhou, sobre()
                    # devolve None e nada é anunciado.
                    if ok:
                        gravado = ferramentas.arquivo_gravado(
                            chamada["nome"], chamada["args"]
                        )
                        ficha = arquivos.sobre(gravado) if gravado else None
                        if ficha:
                            yield _sse({"type": "file", **ficha})
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
                elif not chunks and usados and usados[0] != config.PROVIDERS[0]:
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
        learned: list[dict[str, str]] = []
        try:
            learned = await learner.extract(message, reply)
        except Exception:
            learned = []  # aprender é bônus; nunca pode quebrar a conversa
        if learned:
            yield _sse({"type": "learned", "memories": learned})

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


_STORES = {"memories": memory, "skills": skills}


def _store_for(kind: str):
    return _STORES.get(kind)


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

    achados = segredos.conferir(name, description, body)
    if achados:
        return JSONResponse({"error": segredos.motivo(achados)}, status_code=400)

    doc = store.save(name, description, body, kind=str(payload.get("kind") or "manual"))
    return JSONResponse({"item": doc.to_json(), "stats": context.stats()})


async def delete_doc(request: Request) -> Response:
    store = _store_for(request.path_params["kind"])
    if store is None:
        return _unknown()
    ok = store.delete(request.path_params["name"])
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
    return JSONResponse({"livros": biblioteca.listar()})


async def apagar_livro(request: Request) -> Response:
    ok = biblioteca.remover(request.path_params["slug"])
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


async def listar_workspace(request: Request) -> Response:
    """O que existe na pasta de trabalho, do mais recente ao mais antigo."""
    itens = arquivos.listar()
    return JSONResponse({
        "arquivos": itens,
        "pasta": config.WORKSPACE.name,
        "total": len(itens),
    })


async def baixar_arquivo(request: Request) -> Response:
    """Entrega um arquivo da pasta de trabalho.

    SEMPRE como anexo e SEMPRE como octet-stream. A Livia escreve arquivos
    .html; servir um deles inline executaria o JavaScript dele na mesma
    origem do painel, com acesso ao cookie de sessão. Quem escreve esse
    conteúdo é um modelo que leu a internet — inline seria XSS armazenado
    de graça.
    """
    try:
        alvo = arquivos.para_download(request.path_params["caminho"])
    except ferramentas.FerramentaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    return FileResponse(
        alvo,
        filename=alvo.name,
        media_type="application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff"},
    )


async def diagnostico(request: Request) -> Response:
    """Retrato do sistema para a interface. Nunca inclui segredo.

    Cada sonda é isolada. Isto é consultado JUSTAMENTE quando algo está
    quebrado, então uma sonda que estoura não pode derrubar o retrato
    inteiro — a tela que explica o problema morreria por causa dele. Um teste
    pegou exatamente isso: `mkdir` levanta ValueError, não OSError, em certos
    caminhos, e o endpoint respondia 500.
    """

    def sonda(funcao, padrao=None):
        try:
            return funcao()
        except Exception:
            return padrao

    def workspace_gravavel() -> bool:
        ws = config.WORKSPACE
        ws.mkdir(parents=True, exist_ok=True)
        teste = ws / ".escrita-teste"
        teste.write_text("x", encoding="utf-8")
        teste.unlink()
        return True

    livros = sonda(biblioteca.listar, []) or []

    return JSONResponse({
        "provedores": sonda(saude.diagnostico, {}) or {},
        "workspace": {
            # None quando nem deu para tentar — diferente de False, que é
            # "tentei e não consegui". A interface trata os dois como problema,
            # mas o log fica honesto.
            "gravavel": sonda(workspace_gravavel, False),
            "pasta": sonda(lambda: config.WORKSPACE.name, "?"),
            "arquivos": len(sonda(arquivos.listar, []) or []),
        },
        "banco": {
            "disponivel": sonda(lambda: config.DB_PATH.exists(), False),
            # Conversa gravada é a prova mais direta de que o disco persiste:
            # em hospedagem de disco efêmero o número volta a zero sozinho, e
            # é assim que o André descobre — antes de perder as memórias sem
            # entender por quê.
            "conversas": sonda(db.count_conversations),
        },
        "biblioteca": {
            "documentos": len(livros),
            "invalidos": sum(1 for l in livros if l.get("compativel") is False),
        },
        "memoria": {
            "memorias": sonda(memory.count, 0),
            "skills": sonda(skills.count, 0),
            "aprende_sozinha": config.AUTO_LEARN,
        },
        "ferramentas": {
            "ligadas": config.TOOLS_ENABLED,
            "quantas": len(sonda(ferramentas.catalogo, []) or []),
        },
        "web": {"ligada": config.WEB_ENABLED},
        "protegido": sonda(auth.protegido, True),
        "momento": datetime.now().isoformat(timespec="seconds"),
    })


async def health_check(request: Request) -> Response:
    """Endpoint que hospedagens consultam para saber se o app está de pé.

    O nome Python NÃO pode ser `saude`: isso sombrearia o módulo homônimo,
    e `saude.diagnostico()` passaria a chamar um atributo de função. Foi
    exatamente o que aconteceu, e só um teste denunciou.
    """
    return JSONResponse({"ok": True})


async def status(request: Request) -> Response:
    return JSONResponse(
        {
            "user": config.USER_NAME,
            "name": config.ASSISTANT_NAME,
            "model": config.MODEL,
            "auto_learn": config.AUTO_LEARN,
            "has_key": bool(config.GEMINI_API_KEY),
            **context.stats(),
        }
    )


async def index(request: Request) -> Response:
    return FileResponse(config.WEB_DIR / "index.html")


# --------------------------------------------------------------------------
# Acesso
# --------------------------------------------------------------------------

LIVRES = {"/entrar", "/api/entrar", "/saude"}


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

        if request.url.path in LIVRES:
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
    Route("/api/workspace", listar_workspace),
    Route("/api/workspace/{caminho:path}", baixar_arquivo),
    Route("/api/biblioteca", listar_livros),
    Route("/api/biblioteca", enviar_livro, methods=["POST"]),
    Route("/api/biblioteca/{slug:str}", apagar_livro, methods=["DELETE"]),
    Route("/api/backup", baixar_backup),
    Route("/api/backup", restaurar_backup, methods=["POST"]),
    Route("/api/persona", get_persona),
    Route("/api/persona", save_persona, methods=["POST"]),
    Route("/api/store/{kind:str}", list_docs),
    Route("/api/store/{kind:str}", create_doc, methods=["POST"]),
    Route("/api/store/{kind:str}/{name:str}", delete_doc, methods=["DELETE"]),
]

@asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    db.init()
    yield


app = Starlette(
    routes=routes,
    lifespan=lifespan,
    middleware=[Middleware(ExigirSenha)],
)
