"""O contrato HTTP do sidecar.

Starlette, e não FastAPI, por dois motivos: a Livia já depende de Starlette
(então o serviço não acrescenta peso quando o Cognee não está instalado), e
são seis rotas — a validação automática do FastAPI resolveria um problema que
não temos.

    GET    /health
    POST   /documents                    ingere um documento
    DELETE /documents/{id}               esquece um documento
    GET    /documents                    o que o grafo conhece
    POST   /documents/{id}/rebuild       refaz o grafo a partir do registro
    POST   /search/graph                 recuperação relacional
    POST   /parse                        parser avançado (PDF difícil)

Não existe `/search/vector` de propósito. O RAG vetorial é da Livia e vai
continuar sendo: expor um segundo aqui criaria dois mecanismos fazendo a
mesma coisa, e um deles envelheceria sem ninguém notar.

O SERVIÇO SOBE MESMO QUEBRADO
-----------------------------
Sem Cognee instalado, sem modelo baixado, com o Ollama fora do ar — o serviço
sobe assim mesmo e o `/health` explica o que falta. Um sidecar que se recusa
a iniciar não consegue contar por quê, e o André ficaria olhando para uma
conexão recusada sem pista nenhuma.
"""

from __future__ import annotations

import logging

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import config, multimodal, registro
from .cognee_engine import CogneeError, motor

log = logging.getLogger("knowledge")


async def _corpo(request: Request) -> dict[str, object]:
    try:
        dados = await request.json()
    except (ValueError, TypeError):
        return {}
    return dados if isinstance(dados, dict) else {}


# --------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------


async def health(request: Request) -> JSONResponse:
    """Estado em detalhe suficiente para consertar, não só para saber que quebrou."""
    try:
        estado = await motor.status()
    except Exception as exc:                    # nunca deixar o health cair
        estado = {"status": "error", "engine": "cognee", "mensagem": str(exc)[:400]}
    estado["parser"] = multimodal.diagnostico()
    return JSONResponse(estado)


async def ingerir(request: Request) -> JSONResponse:
    dados = await _corpo(request)
    document_id = str(dados.get("document_id") or "").strip()
    chunks = dados.get("chunks")

    if not document_id:
        return JSONResponse({"error": "document_id é obrigatório"}, status_code=400)
    if not isinstance(chunks, list) or not chunks:
        return JSONResponse({"error": "chunks vazio"}, status_code=400)

    meta = {
        "title": dados.get("title") or document_id,
        "source": dados.get("source") or "",
        "collection_id": dados.get("collection_id") or "",
    }

    try:
        resultado = await motor.ingest(document_id, chunks, meta)
    except CogneeError as exc:
        # 4xx e não 5xx: o serviço está de pé e respondeu. Devolver 5xx faria
        # o disjuntor da Livia colocar o grafo inteiro de castigo por causa
        # de um documento problemático.
        return JSONResponse({"error": str(exc), "document_id": document_id},
                            status_code=422)
    except Exception as exc:
        log.exception("[knowledge] falha inesperada ingerindo %s", document_id)
        return JSONResponse({"error": f"falha inesperada: {exc}"[:400]},
                            status_code=500)

    return JSONResponse(resultado)


async def esquecer(request: Request) -> JSONResponse:
    document_id = request.path_params["document_id"]
    try:
        ok = await motor.remove(document_id)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)
    return JSONResponse({"ok": bool(ok), "document_id": document_id})


async def listar(request: Request) -> JSONResponse:
    return JSONResponse({
        "documents": registro.documentos(),
        "resumo": registro.estatisticas(),
    })


async def reconstruir(request: Request) -> JSONResponse:
    """Refaz o grafo SEM o arquivo original.

    Os trechos ficaram no registro na primeira ingestão, então reconstruir
    não exige o PDF de volta. É o que permite a ação "reconstruir
    conhecimento" da interface funcionar meses depois.
    """
    document_id = request.path_params["document_id"]
    doc = registro.documento(document_id)
    if doc is None:
        return JSONResponse({"error": "documento desconhecido"}, status_code=404)

    trechos = registro.trechos_de(document_id)
    if not trechos:
        return JSONResponse({"error": "sem trechos guardados"}, status_code=422)

    chunks = [
        {
            "chunk_id": t["chunk_id"],
            "text": t["texto"],
            "page": t["page"],
            "origin": t["origin"],
            "type": t["tipo"],
        }
        for t in trechos
    ]
    meta = {
        "title": doc["title"],
        "source": doc["source"],
        "collection_id": doc["collection_id"],
    }

    try:
        await motor.remove(document_id)          # limpa antes, para não somar
        resultado = await motor.ingest(document_id, chunks, meta)
    except CogneeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except Exception as exc:
        log.exception("[knowledge] falha reconstruindo %s", document_id)
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)

    return JSONResponse({"ok": True, **resultado})


async def buscar_grafo(request: Request) -> JSONResponse:
    dados = await _corpo(request)
    pergunta = str(dados.get("query") or "").strip()
    if not pergunta:
        return JSONResponse({"error": "query vazia"}, status_code=400)

    try:
        limite = max(1, min(50, int(dados.get("limit") or 6)))
    except (TypeError, ValueError):
        limite = 6

    try:
        resultados = await motor.graph_search(pergunta, limite)
    except Exception as exc:
        log.warning("[knowledge] busca falhou: %s", exc)
        resultados = []

    return JSONResponse({"results": resultados, "count": len(resultados)})


async def analisar(request: Request) -> JSONResponse:
    """Parser avançado para o que o pypdf não venceu.

    Recebe o arquivo cru. A Livia só chama isto DEPOIS de o pypdf falhar —
    o caminho rápido continua sendo o dela, e este aqui custa minutos.

    Fica no sidecar, e não na Livia, pelo mesmo motivo do Cognee: o MinerU
    traz ~29 dependências (opencv entre elas). Manter todo o peso opcional
    de um lado só é o que permite `pip install -r requirements.txt` continuar
    pequeno.
    """
    if not multimodal.disponivel():
        return JSONResponse(
            {"error": multimodal.como_instalar(), "instalado": False},
            status_code=501,
        )

    nome = request.headers.get("x-nome-arquivo", "documento.pdf")
    dados = await request.body()
    if not dados:
        return JSONResponse({"error": "arquivo vazio"}, status_code=400)

    import tempfile
    from pathlib import Path as _Path

    descrever = request.headers.get("x-descrever-imagens", "0") != "0"

    with tempfile.TemporaryDirectory() as pasta:
        caminho = _Path(pasta) / nome
        caminho.write_bytes(dados)
        try:
            blocos = await multimodal.extrair(
                caminho, descrever_imagens=descrever
            )
        except Exception as exc:
            log.warning("[parser] falhou em %s: %s", nome, exc)
            return JSONResponse({"error": str(exc)[:400], "blocos": []},
                                status_code=422)

    return JSONResponse({
        "blocos": blocos,
        "trechos": multimodal.para_trechos(blocos),
        "tipos": sorted({b["type"] for b in blocos}),
    })


rotas = [
    Route("/health", health),
    Route("/documents", listar),
    Route("/documents", ingerir, methods=["POST"]),
    Route("/documents/{document_id:str}", esquecer, methods=["DELETE"]),
    Route("/documents/{document_id:str}/rebuild", reconstruir, methods=["POST"]),
    Route("/search/graph", buscar_grafo, methods=["POST"]),
    Route("/parse", analisar, methods=["POST"]),
]

app = Starlette(routes=rotas)
