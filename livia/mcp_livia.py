"""A Livia como servidor MCP.

MCP (Model Context Protocol) é o padrão aberto que deixa uma IA usar
ferramentas de outro programa. Com isto, o Claude Code, o Gemini CLI, o
Cursor ou qualquer cliente MCP enxergam a Livia: buscam e gravam memória,
mexem na Nebulosa e — o motivo principal — registram sozinhos o relatório do
que fizeram numa ideia.

Não há ferramenta nova inventada aqui. O catálogo é o MESMO que o chat usa
(`ferramentas.catalogo()`), mais duas de memória que no chat são automáticas
(a Livia lembra sozinha; um cliente de fora precisa pedir). Uma ferramenta
criada para o chat aparece no MCP sem ninguém lembrar de registrar.

Dois jeitos de ligar:

- HTTP em `/mcp`, com `Authorization: Bearer <LIVIA_MCP_TOKEN>`. É o que
  funciona pela internet, via Tailscale Funnel.
- stdio: `python -m livia.mcp_livia`, para um cliente na mesma máquina.

Sem LIVIA_MCP_TOKEN, a rota HTTP responde 404: a porta não existe.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

from . import config, ferramentas, memoria

log = logging.getLogger("livia.mcp")

INSTRUCOES = """\
Esta é a Livia, a IA pessoal do André: memória de longo prazo e a Nebulosa, \
o céu de ideias dele (cada ideia tem temperatura e estágio; categorias são \
sistemas solares; notas e relatórios são luas).

Como usar bem:
- Antes de começar algo que pode já ter história, procure: buscar_memoria e \
buscar_ideias.
- Ao TERMINAR um trabalho relacionado a uma ideia da Nebulosa, registre um \
relatório (registrar_relatorio) dizendo o que foi feito, o que ficou decidido \
e o próximo passo. Isso aquece a ideia e deixa o histórico com o André.
- Só grave memória (lembrar) com fatos duráveis que o André disse; nada de \
conclusão sua.
- Criar ideia, mudar estágio, criar sistema: só quando o André pedir.
"""

# Ferramentas que no chat não existem porque lá a memória é automática.
_MEMORIA = [
    {
        "name": "buscar_memoria",
        "description": (
            "Procura nas memórias de longo prazo do André (fatos, preferências, "
            "projetos, decisões). Use antes de começar algo que pode ter histórico."
        ),
        "parameters": {"type": "object", "properties": {
            "pergunta": {"type": "string", "description": "O que você quer saber, em linguagem natural."},
            "limite": {"type": "integer", "description": "Quantas memórias no máximo (padrão 8)."},
        }, "required": ["pergunta"]},
    },
    {
        "name": "lembrar",
        "description": (
            "Grava uma memória durável sobre o André (algo que ELE disse ou decidiu). "
            "Se já existir uma quase igual, a Livia atualiza em vez de duplicar."
        ),
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Identificador curto, ex.: 'stack-do-ghostline'."},
            "descricao": {"type": "string", "description": "O fato, em uma ou duas frases."},
            "detalhes": {"type": "string"},
        }, "required": ["nome", "descricao"]},
    },
]

# Nome do cliente MCP → a IA do relatório. Assim o Claude Code não precisa
# dizer que é o Claude: a Livia já sabe quem está do outro lado.
_CLIENTES = {"claude": "claude", "gemini": "gemini", "antigravity": "gemini",
             "codex": "gpt", "openai": "gpt", "chatgpt": "gpt"}


def ia_do_cliente(nome: str | None) -> str:
    nome = (nome or "").lower()
    for pista, ia in _CLIENTES.items():
        if pista in nome:
            return ia
    return "outra" if nome else ""


def catalogo() -> list[dict[str, Any]]:
    return ferramentas.catalogo() + _MEMORIA


async def chamar(nome: str, args: dict[str, Any], *, cliente: str = "") -> tuple[str, bool]:
    """Roda uma ferramenta pedida por um cliente MCP. Devolve (texto, deu_certo)."""
    if nome == "buscar_memoria":
        achados = await memoria.recuperar(
            str(args.get("pergunta") or ""), limite=int(args.get("limite") or 8), limiar=0.0)
        if not achados:
            return "Nada na memória sobre isso.", True
        return "\n\n".join(
            f"[{a.doc.name}] {a.doc.description}" + (f"\n{a.doc.body.strip()[:1200]}" if a.doc.body.strip() else "")
            for a in achados), True
    if nome == "lembrar":
        r = await memoria.guardar(
            str(args.get("nome") or "nota"), str(args.get("descricao") or ""),
            corpo=str(args.get("detalhes") or ""), origem=f"mcp:{cliente or 'cliente'}")
        return f"Memória {r['resultado']}: {r['memoria']['name']}.", True
    if nome == "registrar_relatorio" and not args.get("ia"):
        args = {**args, "ia": ia_do_cliente(cliente) or "outra"}
    return await ferramentas.executar_async(nome, args)


def _cliente(ctx: Any) -> str:
    """Nome que o cliente deu ao se apresentar (claude-code, gemini-cli…)."""
    for caminho in (("session", "client_params", "client_info", "name"),
                    ("session", "client_params", "clientInfo", "name")):
        alvo = ctx
        for parte in caminho:
            alvo = getattr(alvo, parte, None)
            if alvo is None:
                break
        if isinstance(alvo, str):
            return alvo
    return ""


async def _listar(_ctx: Any, _params: Any) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[
        types.Tool(name=t["name"], description=t.get("description") or "",
                   input_schema=t.get("parameters") or {"type": "object", "properties": {}})
        for t in catalogo()
    ])


async def _chamar(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    args = dict(params.arguments or {})
    cliente = _cliente(ctx)
    try:
        texto, ok = await chamar(params.name, args, cliente=cliente)
    except Exception as exc:  # um erro vira resposta, nunca derruba a sessão
        log.exception("[mcp] %s falhou", params.name)
        texto, ok = f"Erro: {exc}", False
    log.info("[mcp] %s · %s · %s", cliente or "?", params.name, "ok" if ok else "erro")
    return types.CallToolResult(content=[types.TextContent(type="text", text=texto)], is_error=not ok)


def servidor() -> Server:
    return Server("livia", version="1.0", title="Livia",
                  description="Memória e Nebulosa de ideias do André",
                  instructions=INSTRUCOES, on_list_tools=_listar, on_call_tool=_chamar)


class RotaMCP:
    """ASGI montado em /mcp: confere o token e entrega ao transporte do SDK.

    Stateless: cada pedido se basta (é o modelo da spec 2026-07-28), então
    reiniciar a Livia não derruba cliente nenhum.
    """

    def __init__(self) -> None:
        self.gerente = None

    @contextlib.asynccontextmanager
    async def vida(self):  # noqa: ANN201
        """Sobe o transporte junto com a Livia. Um gerente por subida: o do SDK
        só aceita `run()` uma vez, e o lifespan roda de novo a cada reinício."""
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        self.gerente = StreamableHTTPSessionManager(app=servidor(), stateless=True, json_response=True)
        try:
            async with self.gerente.run():
                yield
        finally:
            self.gerente = None

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            return
        if not config.MCP_TOKEN:
            await _responder(send, 404, {"error": "MCP desligado: defina LIVIA_MCP_TOKEN no .env"})
            return
        cabecalhos = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        enviado = cabecalhos.get("authorization", "").removeprefix("Bearer ").strip()
        if not enviado or not hmac.compare_digest(enviado.encode(), config.MCP_TOKEN.encode()):
            await _responder(send, 401, {"error": "token MCP inválido"},
                             extra=[(b"www-authenticate", b'Bearer realm="livia"')])
            return
        if self.gerente is None:
            await _responder(send, 503, {"error": "a Livia ainda está subindo"})
            return
        await self.gerente.handle_request(scope, receive, send)


async def _responder(send, status: int, corpo: dict, extra: list | None = None) -> None:  # noqa: ANN001
    dados = json.dumps(corpo).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), *(extra or [])]})
    await send({"type": "http.response.body", "body": dados})


def main() -> None:
    """Modo stdio, para um cliente na mesma máquina (não precisa de token)."""
    import anyio
    from mcp.server.stdio import stdio_server

    from . import db

    db.init()
    srv = servidor()

    async def rodar() -> None:
        async with stdio_server() as (leitura, escrita):
            await srv.run(leitura, escrita, srv.create_initialization_options())

    anyio.run(rodar)


if __name__ == "__main__":
    main()
