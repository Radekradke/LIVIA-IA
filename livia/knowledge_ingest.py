"""Ingestão dupla e o estado de cada documento no grafo.

A REGRA QUE GOVERNA ESTE ARQUIVO
--------------------------------
A biblioteca é a principal. Sempre. Se o grafo falhar, o documento continua
indexado, buscável e útil — o que se perde é a capacidade de responder
perguntas relacionais sobre ele, e isso é um bônus a menos, não um erro.

    arquivo
       ├──→ biblioteca (chunks + vetores)   ← nunca sofre rollback
       └──→ Knowledge Engine (grafo)        ← pode falhar sem consequência

Inverter isso — desfazer a indexação porque o grafo não subiu — trocaria uma
funcionalidade que funciona por uma que é opcional.

POR QUE A FILA
--------------
Construir grafo leva minutos. Dentro do pedido HTTP do upload, a conexão
morreria antes e o André ficaria olhando uma barra travada. Então o upload
termina quando a BIBLIOTECA termina, o documento nasce com
`knowledge_status: pending`, e a fila (tabela SQLite, ver `db.job_*`) cuida
do resto em segundo plano.

NADA PESADO COMEÇA SOZINHO
--------------------------
Uma biblioteca com 23 documentos levaria horas de CPU para virar grafo. Isso
não pode acontecer no primeiro boot depois de uma atualização, sem ninguém
pedir. `pendentes_de_grafo()` conta quantos faltam e a interface OFERECE;
quem manda começar é o André.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from . import biblioteca, config, db, knowledge, knowledge_client

log = logging.getLogger("livia.knowledge")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# O estado mora no meta.json, junto do resto
# --------------------------------------------------------------------------


def marcar(
    slug: str,
    estado: str,
    *,
    erro: str = "",
    engine: str = "",
) -> dict[str, object] | None:
    """Anota no `meta.json` como está o grafo deste documento.

    Campos acrescentados, nunca substituídos: um `meta.json` de antes desta
    versão não tem nenhum deles, e continua válido. Quem lê assume
    `disabled` — ver `estado_de()`.
    """
    caminho = biblioteca.PASTA / slug / "meta.json"
    if not caminho.exists():
        return None
    try:
        meta = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    meta["knowledge_status"] = estado
    meta["knowledge_updated_at"] = _agora()
    if engine:
        meta["knowledge_engine"] = engine
    if erro:
        meta["knowledge_error"] = erro[:400]
    elif estado == knowledge.PRONTO:
        meta.pop("knowledge_error", None)

    try:
        caminho.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        return None
    return meta


def estado_de(livro: dict[str, object]) -> str:
    """Como está o grafo deste documento.

    Documento antigo, sem o campo, é `disabled` — não `pending`. A diferença
    importa: `pending` faria a interface prometer um processamento que
    ninguém pediu, e a contagem de "faltam N documentos" incluiria a
    biblioteca inteira de quem nunca ligou a funcionalidade.
    """
    if not knowledge_client.ligado():
        return knowledge.DESLIGADO
    estado = str(livro.get("knowledge_status") or "")
    return estado if estado in knowledge.ESTADOS else knowledge.DESLIGADO


def pendentes_de_grafo() -> list[str]:
    """Documentos indexados que ainda não têm grafo.

    Alimenta o "Há 23 documentos sem grafo. Construir agora?" — a pergunta
    existe para que horas de CPU nunca comecem em silêncio.
    """
    if not knowledge_client.ligado():
        return []
    return [
        str(l["slug"])
        for l in biblioteca.listar()
        if estado_de(l) in (knowledge.DESLIGADO, knowledge.FALHOU, knowledge.DESATUALIZADO)
    ]


def _trechos_de(slug: str) -> list[dict[str, object]]:
    """Os trechos já guardados. Não precisa do arquivo original."""
    caminho = biblioteca.PASTA / slug / "trechos.jsonl"
    if not caminho.exists():
        return []
    trechos = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if not linha.strip():
            continue
        try:
            trechos.append(json.loads(linha))
        except json.JSONDecodeError:
            continue
    return trechos


def _meta_de(slug: str) -> dict[str, object]:
    caminho = biblioteca.PASTA / slug / "meta.json"
    if not caminho.exists():
        return {}
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# --------------------------------------------------------------------------
# Enfileirar
# --------------------------------------------------------------------------


def agendar(slug: str, operacao: str = "ingest") -> bool:
    """Põe um documento na fila do grafo. Devolve se entrou.

    Chamado logo depois de a biblioteca terminar. Nunca bloqueia, nunca
    levanta: o upload já deu certo, e o que acontece aqui é bônus.
    """
    if not knowledge_client.ligado():
        return False
    try:
        db.job_enfileirar(slug, operacao)
        if operacao == "ingest":
            marcar(slug, knowledge.PENDENTE)
        log.debug("[knowledge] job %s enfileirado para %s", operacao, slug)
        return True
    except Exception as exc:
        log.debug("[knowledge] não consegui enfileirar %s: %s", slug, exc)
        return False


def agendar_remocao(slug: str) -> None:
    """Tombstone: o documento saiu daqui, o grafo precisa saber.

    Enfileira ANTES de tentar, e a tentativa direta acontece no worker. Assim
    apagar com o serviço offline não deixa conhecimento órfão para sempre —
    trechos de um documento que não existe mais aparecendo em resposta futura
    seria o pior tipo de fantasma.
    """
    if not knowledge_client.ligado():
        return
    try:
        db.job_limpar(slug)              # jobs de ingestão dele não valem mais
        db.job_enfileirar(slug, "remove")
    except Exception as exc:
        log.debug("[knowledge] não consegui agendar remoção de %s: %s", slug, exc)


# --------------------------------------------------------------------------
# O worker
# --------------------------------------------------------------------------


async def processar_um() -> dict[str, object] | None:
    """Executa o próximo job da fila. None quando não há nada.

    Devolve o que aconteceu para quem quiser mostrar progresso.
    """
    if not knowledge_client.disponivel():
        return None

    job = db.job_proximo()
    if job is None:
        return None

    slug = str(job["document_id"])
    operacao = str(job["operacao"])

    try:
        if operacao == "remove":
            ok = await knowledge_client.remover(slug)
            db.job_terminar(int(job["id"]), ok, "" if ok else "serviço não confirmou")
            return {"slug": slug, "operacao": operacao, "ok": ok}

        trechos = _trechos_de(slug)
        if not trechos:
            db.job_terminar(int(job["id"]), False, "sem trechos guardados")
            marcar(slug, knowledge.FALHOU, erro="sem trechos guardados")
            return {"slug": slug, "operacao": operacao, "ok": False}

        marcar(slug, knowledge.PROCESSANDO)
        resposta = await knowledge_client.ingerir(slug, trechos, _meta_de(slug))

        if resposta and resposta.get("ok"):
            db.job_terminar(int(job["id"]), True)
            marcar(slug, knowledge.PRONTO,
                   engine=str(resposta.get("engine") or "cognee"))
            log.debug("[knowledge] grafo pronto para %s", slug)
            return {"slug": slug, "operacao": operacao, "ok": True}

        db.job_terminar(int(job["id"]), False, "o serviço não confirmou a ingestão")
        marcar(slug, knowledge.FALHOU, erro="o serviço não confirmou a ingestão")
        return {"slug": slug, "operacao": operacao, "ok": False}

    except Exception as exc:
        log.warning("[knowledge] job %s de %s falhou: %s", operacao, slug, exc)
        db.job_terminar(int(job["id"]), False, str(exc))
        if operacao == "ingest":
            marcar(slug, knowledge.FALHOU, erro=str(exc))
        return {"slug": slug, "operacao": operacao, "ok": False}


async def processar_fila(limite: int = 50) -> dict[str, int]:
    """Roda a fila até esvaziar (ou até o limite).

    O limite existe para o disparo pela interface ter fim previsível — sem
    ele, mandar construir uma biblioteca grande viraria um pedido HTTP de
    horas.
    """
    feitos = falhos = 0
    for _ in range(max(1, limite)):
        resultado = await processar_um()
        if resultado is None:
            break
        if resultado["ok"]:
            feitos += 1
        else:
            falhos += 1
    return {"feitos": feitos, "falhos": falhos, "restantes": db.job_pendentes()}


def recuperar_apos_reinicio() -> int:
    """Jobs interrompidos por um reinício voltam para a fila."""
    try:
        return db.job_recuperar_abandonados()
    except Exception:
        return 0


def resumo() -> dict[str, object]:
    """Retrato para o diagnóstico e para a interface."""
    ligado = knowledge_client.ligado()
    livros = biblioteca.listar() if ligado else []
    por_estado: dict[str, int] = {}
    for livro in livros:
        estado = estado_de(livro)
        por_estado[estado] = por_estado.get(estado, 0) + 1

    return {
        "ligado": ligado,
        "documentos": por_estado,
        "sem_grafo": len(pendentes_de_grafo()),
        "fila": db.job_pendentes() if ligado else 0,
        "cliente": knowledge_client.diagnostico(),
    }
