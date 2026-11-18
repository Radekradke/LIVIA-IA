"""Rotas HTTP da Nebulosa.

Num arquivo à parte porque são muitas e porque o server.py já passa de mil
linhas. A autenticação continua sendo a do middleware de lá: tudo aqui está
sob /api/ e exige sessão como qualquer outra rota.

Os erros de regra (gate faltando, ideia que não existe) voltam como JSON com
status 4xx e o texto em português — a interface mostra o texto, e no caso
do gate faltando abre o formulário com as perguntas que vieram junto.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import config, nebulosa


async def _corpo(request: Request) -> dict[str, object]:
    try:
        dados = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _erro(exc: Exception) -> Response:
    if isinstance(exc, nebulosa.GateFaltando):
        return JSONResponse(
            {
                "error": str(exc),
                "gate_faltando": True,
                "destino": exc.destino,
                "faltando": [g.to_json() for g in exc.pendentes],
                "ideia": nebulosa.publico(nebulosa.obter(exc.id)),
            },
            status_code=409,
        )
    if isinstance(exc, nebulosa.NebulosaError):
        status = 404 if "não existe ideia" in str(exc).lower() else 400
        return JSONResponse({"error": str(exc)}, status_code=status)
    raise exc


def _desligada() -> Response:
    return JSONResponse({"error": "A Nebulosa está desligada (LIVIA_NEBULOSA=0)."}, status_code=404)


def _num(valor: object) -> float | None:
    try:
        return None if valor is None or valor == "" else float(valor)
    except (TypeError, ValueError):
        return None


async def estado(request: Request) -> Response:
    if not config.NEBULOSA_ENABLED:
        return _desligada()
    return JSONResponse(nebulosa.estado())


async def criar(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        ideia = nebulosa.criar(
            str(dados.get("conteudo") or ""),
            titulo=str(dados.get("titulo") or ""),
            fonte=str(dados.get("fonte") or ""),
            x=_num(dados.get("x")),
            y=_num(dados.get("y")),
        )
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ideia": ideia})


async def detalhar(request: Request) -> Response:
    id_ = request.path_params["id"]
    try:
        if request.query_params.get("abrir") == "1":
            return JSONResponse({"ideia": nebulosa.abrir(id_)})
        return JSONResponse({"ideia": nebulosa.detalhar(id_)})
    except nebulosa.NebulosaError as exc:
        return _erro(exc)


async def alterar(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        ideia = nebulosa.editar(
            request.path_params["id"],
            titulo=dados.get("titulo") if isinstance(dados.get("titulo"), str) else None,
            conteudo=dados.get("conteudo") if isinstance(dados.get("conteudo"), str) else None,
            fonte=dados.get("fonte") if isinstance(dados.get("fonte"), str) else None,
            x=_num(dados.get("x")),
            y=_num(dados.get("y")),
            ancorada=dados.get("ancorada") if isinstance(dados.get("ancorada"), bool) else None,
        )
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ideia": ideia})


async def apagar(request: Request) -> Response:
    try:
        return JSONResponse({"ok": nebulosa.apagar(request.path_params["id"])})
    except nebulosa.NebulosaError as exc:
        return _erro(exc)


async def gate(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        ideia = nebulosa.responder_gate(
            request.path_params["id"], str(dados.get("gate") or ""), str(dados.get("valor") or "")
        )
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ideia": ideia})


async def gates_em_lote(request: Request) -> Response:
    """Várias respostas de uma vez, e opcionalmente já promove.

    É o que o formulário de gate faltando usa: o André responde as três
    perguntas e clica uma vez só, em vez de três salvamentos e um promover.
    """
    dados = await _corpo(request)
    id_ = request.path_params["id"]
    try:
        for chave, valor in (dados.get("respostas") or {}).items():
            nebulosa.responder_gate(id_, str(chave), str(valor or ""))
        destino = str(dados.get("promover_para") or "").strip()
        ideia = (nebulosa.mudar_estagio(id_, destino) if destino
                 else nebulosa.publico(nebulosa.obter(int(id_))))
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ideia": ideia})


async def estagio(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        ideia = nebulosa.mudar_estagio(
            request.path_params["id"], str(dados.get("estagio") or ""),
            motivo=str(dados.get("motivo") or ""),
        )
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ideia": ideia})


async def reviver(request: Request) -> Response:
    try:
        return JSONResponse({"ideia": nebulosa.reviver(request.path_params["id"])})
    except nebulosa.NebulosaError as exc:
        return _erro(exc)


async def adotar(request: Request) -> Response:
    try:
        return JSONResponse({"ideia": nebulosa.adotar(request.path_params["id"])})
    except nebulosa.NebulosaError as exc:
        return _erro(exc)


async def juntar(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        ideia = nebulosa.juntar(request.path_params["id"], dados.get("absorver"))
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ideia": ideia})


async def ligar(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        nova = nebulosa.ligar(dados.get("a"), dados.get("b"))
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ok": True, "nova": nova})


async def desligar(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        return JSONResponse({"ok": nebulosa.desligar(dados.get("a"), dados.get("b"))})
    except (TypeError, ValueError):
        return JSONResponse({"error": "ligação inválida"}, status_code=400)


async def gravidade(request: Request) -> Response:
    try:
        intensidade = float(request.query_params.get("intensidade", "0.5"))
    except ValueError:
        intensidade = 0.5
    return JSONResponse(await nebulosa.gravidade(intensidade))


async def nomear(request: Request) -> Response:
    dados = await _corpo(request)
    grupos = [list(g) for g in (dados.get("grupos") or []) if isinstance(g, list)]
    return JSONResponse({"constelacoes": await nebulosa.nomear(grupos[:20])})


async def ponte(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        ideia = await nebulosa.ponte(
            list(dados.get("a") or []), list(dados.get("b") or []),
            x=_num(dados.get("x")), y=_num(dados.get("y")),
        )
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"ideia": ideia})


async def layout(request: Request) -> Response:
    """Salva posições. Com `retrato: true`, guarda antes o céu atual (undo)."""
    dados = await _corpo(request)
    retrato = None
    if dados.get("retrato"):
        retrato = nebulosa.guardar_layout()
    posicoes = [p for p in (dados.get("posicoes") or []) if isinstance(p, dict)]
    n = nebulosa.mover_varias(posicoes) if posicoes else 0
    return JSONResponse({"ok": True, "movidas": n, "retrato": retrato})


async def desfazer(request: Request) -> Response:
    posicoes = nebulosa.desfazer_layout()
    if posicoes is None:
        return JSONResponse({"error": "Nada para desfazer."}, status_code=404)
    return JSONResponse({"posicoes": posicoes})


async def listar_sugestoes(request: Request) -> Response:
    if request.query_params.get("gerar") == "1":
        lista = await nebulosa.gerar_sugestoes(forcar=request.query_params.get("forcar") == "1")
    else:
        lista = nebulosa.sugestoes()
    return JSONResponse({"sugestoes": lista})


async def decidir_sugestao(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        resultado = nebulosa.decidir_sugestao(
            int(request.path_params["id"]), str(dados.get("acao") or "") == "aceitar"
        )
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse(resultado)


async def listar_padroes(request: Request) -> Response:
    return JSONResponse({"padroes": nebulosa.padroes(), "minimo": config.NEBULOSA_MIN_PADRAO})


async def padrao_licao(request: Request) -> Response:
    dados = await _corpo(request)
    try:
        licao = nebulosa.virar_licao(str(dados.get("chave") or ""))
    except nebulosa.NebulosaError as exc:
        return _erro(exc)
    return JSONResponse({"licao": licao})


routes = [
    Route("/api/nebulosa", estado),
    Route("/api/nebulosa/ideias", criar, methods=["POST"]),
    Route("/api/nebulosa/ideias/{id:int}", detalhar),
    Route("/api/nebulosa/ideias/{id:int}", alterar, methods=["PATCH"]),
    Route("/api/nebulosa/ideias/{id:int}", apagar, methods=["DELETE"]),
    Route("/api/nebulosa/ideias/{id:int}/gate", gate, methods=["POST"]),
    Route("/api/nebulosa/ideias/{id:int}/gates", gates_em_lote, methods=["POST"]),
    Route("/api/nebulosa/ideias/{id:int}/estagio", estagio, methods=["POST"]),
    Route("/api/nebulosa/ideias/{id:int}/reviver", reviver, methods=["POST"]),
    Route("/api/nebulosa/ideias/{id:int}/adotar", adotar, methods=["POST"]),
    Route("/api/nebulosa/ideias/{id:int}/juntar", juntar, methods=["POST"]),
    Route("/api/nebulosa/ligacoes", ligar, methods=["POST"]),
    Route("/api/nebulosa/ligacoes", desligar, methods=["DELETE"]),
    Route("/api/nebulosa/gravidade", gravidade),
    Route("/api/nebulosa/constelacoes", nomear, methods=["POST"]),
    Route("/api/nebulosa/ponte", ponte, methods=["POST"]),
    Route("/api/nebulosa/layout", layout, methods=["POST"]),
    Route("/api/nebulosa/layout/desfazer", desfazer, methods=["POST"]),
    Route("/api/nebulosa/sugestoes", listar_sugestoes),
    Route("/api/nebulosa/sugestoes/{id:int}", decidir_sugestao, methods=["POST"]),
    Route("/api/nebulosa/padroes", listar_padroes),
    Route("/api/nebulosa/padroes/licao", padrao_licao, methods=["POST"]),
]
