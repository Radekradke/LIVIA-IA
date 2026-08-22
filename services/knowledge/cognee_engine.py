"""Adaptador do Cognee — o único arquivo do projeto que sabe o que é Cognee.

Isolamento deliberado: trocar Cognee por HippoRAG, LightRAG ou implementação
própria é escrever um irmão deste arquivo. Nada em `livia/`, nada em `app.py`
e nada no contrato HTTP muda.

DUAS GERAÇÕES DE API
--------------------
O Cognee renomeou a API pública. A geração atual é:

    remember / recall / forget

e a anterior, que ainda aparece em muito exemplo pela internet, era:

    add + cognify / search / prune

Este adaptador procura a nova, cai para a antiga, e DIZ no `/health` qual
encontrou. Assumir uma das duas produziria o pior tipo de falha: instalar,
parecer que funciona e quebrar na primeira ingestão com um AttributeError
que não explica nada.

O IMPORT É OPCIONAL E FICA AQUI
-------------------------------
`try: import cognee / except ImportError: cognee = None`. Sem o pacote, o
serviço sobe assim mesmo e o `/health` diz o que falta instalar. Um sidecar
que se recusa a subir não consegue explicar por quê.
"""

from __future__ import annotations

import logging
from typing import Any

from . import config, registro

log = logging.getLogger("knowledge.cognee")

try:                      # a dependência pesada vive só aqui dentro
    import cognee as _cognee
except Exception:         # ImportError, e também erros de import de deps dele
    _cognee = None


API_NOVA = "remember/recall/forget"
API_CLASSICA = "add+cognify/search/prune"


class CogneeError(RuntimeError):
    """Falha já em português, para o /health e para o log."""


def instalado() -> bool:
    return _cognee is not None


def versao() -> str:
    if _cognee is None:
        return ""
    return str(getattr(_cognee, "__version__", "") or "desconhecida")


def dialeto() -> str:
    """Qual geração da API o pacote instalado oferece."""
    if _cognee is None:
        return ""
    if hasattr(_cognee, "remember") and hasattr(_cognee, "recall"):
        return API_NOVA
    if hasattr(_cognee, "add") and hasattr(_cognee, "search"):
        return API_CLASSICA
    return ""


class CogneeEngine:
    """Implementação do KnowledgeEngine em cima do Cognee.

    Cumpre a mesma forma declarada em `livia/knowledge.py` — mas não importa
    aquele módulo, porque este processo não depende da Livia.
    """

    nome = "cognee"

    def __init__(self) -> None:
        self._pronto = False
        self._erro = ""

    # -- ciclo de vida ----------------------------------------------------

    def preparar(self) -> None:
        """Configura o Cognee. Só na primeira vez.

        Reinicializar o motor a cada consulta seria caríssimo — o custo dele
        está justamente em subir os armazenamentos.
        """
        if self._pronto or _cognee is None:
            return

        problemas = config.conferir()
        if problemas:
            # A blindagem do modo local. Não é aviso: é recusa.
            self._erro = " ".join(problemas)
            raise CogneeError(self._erro)

        config.aplicar_no_ambiente()
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._pronto = True
        log.info("[knowledge] cognee %s pronto (api=%s)", versao(), dialeto())

    async def status(self) -> dict[str, object]:
        """O que está de pé, em detalhe suficiente para consertar."""
        if _cognee is None:
            return {
                "status": "not_installed",
                "engine": self.nome,
                "mensagem": (
                    "O Cognee não está instalado. Rode:\n\n"
                    "    pip install -r requirements-knowledge.txt"
                ),
            }

        fala = dialeto()
        if not fala:
            return {
                "status": "error",
                "engine": self.nome,
                "version": versao(),
                "mensagem": (
                    f"O cognee {versao()} instalado não expõe nem "
                    f"{API_NOVA} nem {API_CLASSICA}. Versão incompatível."
                ),
            }

        problemas = config.conferir()
        if problemas:
            return {
                "status": "blocked",
                "engine": self.nome,
                "version": versao(),
                "api": fala,
                "mensagem": " ".join(problemas),
            }

        modelos = await self._checar_modelos()
        tudo_ok = modelos["llm"] and modelos["embedding"]

        return {
            "status": "ok" if tudo_ok else "degraded",
            "engine": self.nome,
            "version": versao(),
            "api": fala,
            "llm": modelos["llm"],
            "embedding": modelos["embedding"],
            "graph": self._pronto or tudo_ok,
            "mensagem": modelos["mensagem"],
            "config": config.resumo(),
            "registro": registro.estatisticas(),
        }

    async def _checar_modelos(self) -> dict[str, Any]:
        """Os modelos configurados existem na máquina?

        NÃO baixa nada. São gigabytes na conexão de alguém, e isso não se faz
        sem pedir — a mensagem diz o comando e para por aí.
        """
        if config.PROVIDER != "ollama":
            # Provedor de nuvem: não temos como conferir sem gastar chamada.
            return {"llm": True, "embedding": True, "mensagem": ""}

        import httpx

        base = config.EMBED_ENDPOINT.split("/api/")[0].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as cliente:
                resposta = await cliente.get(f"{base}/api/tags")
                if resposta.status_code >= 400:
                    raise httpx.HTTPError("resposta ruim")
                instalados = [
                    str(m.get("name") or m.get("model") or "")
                    for m in resposta.json().get("models", [])
                    if isinstance(m, dict)
                ]
        except Exception:
            return {
                "llm": False,
                "embedding": False,
                "mensagem": (
                    f"O Ollama não respondeu em {base}. O servidor está de pé? "
                    "Suba com `ollama serve`."
                ),
            }

        def tem(modelo: str) -> bool:
            alvo = modelo if ":" in modelo else f"{modelo}:latest"
            return any(i == alvo or i.split(":")[0] == modelo for i in instalados)

        tem_llm = tem(config.LLM_MODEL)
        tem_embed = tem(config.EMBED_MODEL)

        faltando = [
            m for m, ok in ((config.LLM_MODEL, tem_llm), (config.EMBED_MODEL, tem_embed))
            if not ok
        ]
        mensagem = ""
        if faltando:
            comandos = "\n".join(f"    ollama pull {m}" for m in dict.fromkeys(faltando))
            mensagem = (
                "O Knowledge Engine precisa "
                + ("destes modelos" if len(faltando) > 1 else f"do modelo {faltando[0]}")
                + ". Execute:\n\n" + comandos
                + "\n\n(não baixo sozinho: são gigabytes na sua conexão)"
            )

        return {"llm": tem_llm, "embedding": tem_embed, "mensagem": mensagem}

    # -- ingestão ---------------------------------------------------------

    async def ingest(
        self,
        document_id: str,
        chunks: list[dict[str, object]],
        meta: dict[str, object],
    ) -> dict[str, object]:
        """Constrói o grafo de UM documento, no dataset dele.

        O registro de procedência é gravado ANTES de chamar o motor. Se a
        construção do grafo falhar no meio, ainda sabemos o que foi mandado —
        e o `rebuild` consegue tentar de novo sem o arquivo original.
        """
        if _cognee is None:
            raise CogneeError("cognee não instalado")
        self.preparar()

        dataset = registro.dataset_de(document_id)
        registro.registrar(
            document_id,
            title=str(meta.get("title") or document_id),
            source=str(meta.get("source") or ""),
            collection_id=str(meta.get("collection_id") or ""),
            chunks=chunks,
            engine=f"{self.nome}:{versao()}",
        )

        # Um texto só, com os trechos separados. O motor faz a própria divisão
        # — brigar com ela reagrupando aqui só atrapalharia a extração de
        # entidades, que melhora com contexto em volta.
        texto = "\n\n".join(str(c.get("text") or "") for c in chunks).strip()
        if not texto:
            raise CogneeError("documento sem texto para ingerir")

        fala = dialeto()
        try:
            if fala == API_NOVA:
                await _cognee.remember(texto, dataset=dataset)
            else:
                await _cognee.add(texto, dataset_name=dataset)
                await _cognee.cognify(datasets=[dataset])
        except Exception as exc:
            log.warning("[knowledge] ingestão falhou em %s: %s", document_id, exc)
            raise CogneeError(f"o motor recusou o documento: {exc}") from exc

        return {
            "ok": True,
            "document_id": document_id,
            "dataset": dataset,
            "chunks": len(chunks),
            "engine": self.nome,
            "version": versao(),
        }

    async def remove(self, document_id: str) -> bool:
        """Apaga o conhecimento de um documento, preservando os outros.

        O registro local some sempre, mesmo se o motor reclamar: deixar
        procedência apontando para um grafo que não existe mais seria pior
        que perder a procedência.
        """
        removido_local = registro.esquecer(document_id)
        if _cognee is None:
            return removido_local

        dataset = registro.dataset_de(document_id)
        try:
            self.preparar()
            if dialeto() == API_NOVA:
                await _cognee.forget(dataset=dataset)
            else:
                await _cognee.prune.prune_data(dataset_name=dataset)
        except Exception as exc:
            log.warning("[knowledge] remoção do dataset %s falhou: %s", dataset, exc)
            return removido_local

        return True

    # -- recuperação ------------------------------------------------------

    async def graph_search(self, pergunta: str, limite: int) -> list[dict[str, object]]:
        """Recuperação relacional, com procedência costurada de volta.

        O motor devolve conhecimento; quem sabe de onde ele veio é o nosso
        registro. Resultado que não casa com documento nenhum é DESCARTADO
        aqui mesmo — a Livia descartaria de qualquer forma, e devolver seria
        gastar rede para nada.
        """
        if _cognee is None:
            return []
        self.preparar()

        try:
            if dialeto() == API_NOVA:
                brutos = await _cognee.recall(pergunta)
            else:
                brutos = await _cognee.search(query_text=pergunta)
        except Exception as exc:
            log.warning("[knowledge] consulta ao grafo falhou: %s", exc)
            return []

        return self._traduzir(brutos, limite)

    def _traduzir(self, brutos: Any, limite: int) -> list[dict[str, object]]:
        """Do formato do motor para o contrato da Livia.

        Defensivo de propósito: o formato de retorno varia entre versões, e
        um `KeyError` aqui derrubaria a busca inteira. O que não der para
        entender vira nada, com log.
        """
        if not isinstance(brutos, (list, tuple)):
            brutos = [brutos]

        saida: list[dict[str, object]] = []
        sem_origem = 0

        for item in list(brutos)[: limite * 4]:
            texto, caminho, dataset = _desmontar(item)
            if not texto.strip():
                continue

            fonte = registro.procedencia(texto, dataset)
            if fonte is None:
                sem_origem += 1
                continue

            saida.append({
                "text": texto[:4000],
                "source": fonte["source"],
                "title": fonte["title"],
                "page": fonte["page"],
                "score": None,
                "retrieval_type": "graph",
                "relation_path": caminho or None,
                "document_id": fonte["document_id"],
                "chunk_id": fonte["chunk_id"],
                "collection_id": fonte["collection_id"],
                "ingested_at": fonte["ingested_at"],
                # Tudo que sai daqui é FONTE: é texto que está escrito num
                # documento. O motor até produz sínteses, mas elas não
                # casariam com o registro — e por isso não passam. Inferência
                # só entra por caminho explícito, nunca por acidente.
                "tipo": "source",
            })
            if len(saida) >= limite:
                break

        if sem_origem:
            log.debug("[knowledge] %d resultados sem procedência descartados", sem_origem)
        return saida


def _desmontar(item: Any) -> tuple[str, list[str], str]:
    """Extrai (texto, caminho da relação, dataset) de um resultado qualquer.

    Cada versão do Cognee devolve uma forma um pouco diferente: string pura,
    dicionário, objeto com atributos. Em vez de nos amarrar a uma, olhamos
    os nomes prováveis e desistimos em silêncio do que não reconhecemos.
    """
    if isinstance(item, str):
        return item, [], ""

    if not isinstance(item, dict):
        item = {
            chave: getattr(item, chave)
            for chave in ("text", "content", "name", "description", "dataset",
                          "dataset_name", "relation", "edges")
            if hasattr(item, chave)
        }

    texto = ""
    for chave in ("text", "content", "chunk", "description", "name", "value"):
        valor = item.get(chave)
        if isinstance(valor, str) and valor.strip():
            texto = valor
            break

    dataset = ""
    for chave in ("dataset", "dataset_name", "collection"):
        valor = item.get(chave)
        if isinstance(valor, str) and valor.strip():
            dataset = valor
            break

    caminho: list[str] = []
    for chave in ("relation_path", "path", "edges", "relations", "triplet"):
        valor = item.get(chave)
        if isinstance(valor, list) and valor:
            caminho = [str(p)[:120] for p in valor[:12] if p]
            break
    if not caminho:
        # Formato de tripla solta: {"source": ..., "relation": ..., "target": ...}
        partes = [item.get("source_node"), item.get("relation"), item.get("target_node")]
        if all(isinstance(p, str) and p for p in partes):
            caminho = [str(p)[:120] for p in partes]

    return texto, caminho, dataset


motor = CogneeEngine()
