"""Memória semântica: lembrar o que tem a ver com a pergunta.

O PROBLEMA QUE ISTO RESOLVE
---------------------------
Antes, TODA memória entrava em TODO prompt. Funciona com vinte; com trezentas,
não. O orçamento estoura, o modelo passa a receber um índice de uma linha por
item, e a memória vira uma lista de títulos que ele quase nunca abre. Pior:
memória boa e memória irrelevante competem pela mesma atenção, então acumular
conhecimento passa a PIORAR as respostas. Um sistema que fica pior quanto mais
aprende está quebrado por dentro.

A saída é buscar por significado:

    pergunta -> vetor -> memórias parecidas -> pontuação -> as N melhores

E a pontuação não é só semelhança. Uma memória vale mais quando é importante,
quando é recente e quando já provou utilidade sendo usada antes:

    nota = semelhança + importância + recência + uso

Os pesos estão em `PESOS`, num lugar só, para dar para mexer sem caçar
constante espalhada.

O QUE É DERIVADO E O QUE É ORIGINAL
-----------------------------------
Os arquivos .md continuam sendo a fonte da verdade. Este módulo mantém um
ÍNDICE ao lado (SQLite, ver db.py): vetor, contadores, carimbos. Apagar o
índice não perde nada — `sincronizar()` reconstrói lendo os arquivos. O
contrário não vale, e é por isso que a ordem nunca se inverte.

QUANDO NÃO HÁ COMO GERAR VETOR
------------------------------
Sem Ollama e sem chave do Gemini, não existe busca semântica. Nesse caso o
sistema volta ao comportamento antigo (carregar tudo até o orçamento) em vez
de ficar sem memória nenhuma. Degradar é aceitável; sumir com a memória do
André porque falta um serviço, não.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np

from . import config, db, docs, embeddings
from .docs import Doc
from .store import COLECOES, memory

log = logging.getLogger("livia.memoria")

# Pesos da pontuação final. Somam 1 para a nota ficar entre 0 e 1 e o limiar
# de relevância significar a mesma coisa em qualquer configuração.
PESOS = {
    "semelhanca": 0.65,   # o que a pergunta pede
    "importancia": 0.15,  # o quanto aquilo pesa na vida do André
    "recencia": 0.12,     # informação nova vale mais que informação velha
    "uso": 0.08,          # o que já foi útil tende a ser útil de novo
}

# Acima disto, a memória entra em toda conversa, com pergunta relacionada ou
# não. É o lugar de "quem é o André" e das decisões que valem sempre.
IMPORTANCIA_SEMPRE = 0.9

# Meia-vida da recência, em dias: uma memória de 90 dias vale metade da nota
# de recência de uma de hoje. Não apaga nada — só desempata.
MEIA_VIDA_DIAS = 90.0

# Depois de tantos usos, o bônus de uso satura. Sem teto, a memória mais usada
# se autoperpetuaria no topo e sufocaria as outras.
USOS_SATURACAO = 10


@dataclass
class Achado:
    """Uma memória recuperada, com a conta que a colocou aqui.

    Guardar as parcelas (e não só a nota) é o que permite responder "por que
    você lembrou disso?" sem chutar — ver `explicar`.
    """

    doc: Doc
    nota: float
    semelhanca: float
    motivo: str = ""
    parcelas: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            **self.doc.to_json(),
            "nota": round(self.nota, 3),
            "semelhanca": round(self.semelhanca, 3),
            "motivo": self.motivo,
            "parcelas": {k: round(v, 3) for k, v in self.parcelas.items()},
        }


# --------------------------------------------------------------------------
# Índice
# --------------------------------------------------------------------------


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _quando(texto: str) -> datetime | None:
    if not texto:
        return None
    try:
        momento = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def sincronizar(colecao: str = "memories") -> dict[str, int]:
    """Alinha o índice com os arquivos .md. Não gera vetor (isso é async).

    Roda barato e pode ser chamada sempre: compara hash de conteúdo e só toca
    no que mudou. É também a migração automática — um usuário que atualiza a
    Livia tem as memórias antigas indexadas na primeira pergunta, sem passo
    manual nenhum.
    """
    store = COLECOES[colecao]
    arquivos = {d.name: d for d in store.all()}
    indexados = db.memoria_nomes(colecao)

    novas = alteradas = removidas = 0

    for nome, doc in arquivos.items():
        digest = embeddings.hash_conteudo(doc.texto_para_vetor())
        linha = db.memoria_linha(nome, colecao)

        campos: dict[str, object] = {
            "kind": doc.kind,
            "escopo": doc.scope,
            "status": doc.status,
            "origem": doc.source,
            "importancia": doc.importance,
            "substitui": doc.supersedes,
            "substituida_por": doc.superseded_by,
        }

        if linha is None:
            db.memoria_upsert(
                nome, colecao,
                hash=digest,
                criado_em=doc.created or date.today().isoformat(),
                atualizado_em=_agora(),
                **campos,
            )
            novas += 1
            continue

        mudou_texto = linha.get("hash") != digest
        mudou_meta = any(linha.get(c) != v for c, v in campos.items())
        if mudou_texto or mudou_meta:
            if mudou_texto:
                # Conteúdo novo, vetor velho: invalidar aqui evita comparar a
                # pergunta com o texto de ontem.
                campos["hash"] = digest
                campos["vetor"] = None
                campos["assinatura"] = ""
            campos["atualizado_em"] = _agora()
            db.memoria_upsert(nome, colecao, **campos)
            alteradas += 1

    for orfa in indexados - set(arquivos):
        # O arquivo sumiu (apagado à mão, restaurado de backup antigo). O
        # índice acompanha — mas nunca o contrário: índice não apaga arquivo.
        db.memoria_apagar(orfa, colecao)
        removidas += 1

    return {"novas": novas, "alteradas": alteradas, "removidas": removidas}


async def indexar(colecao: str = "memories", forcar: bool = False) -> int:
    """Gera os vetores que faltam. Devolve quantos foram calculados.

    Só o que mudou. Um `sincronizar()` marca o vetor como inválido quando o
    texto muda; aqui recalculamos só esses — recalcular tudo a cada pergunta
    seria caríssimo com modelo local.
    """
    if not embeddings.disponivel():
        return 0

    sincronizar(colecao)
    store = COLECOES[colecao]
    arquivos = {d.name: d for d in store.all()}
    assinatura_atual = embeddings.assinatura()

    pendentes: dict[str, str] = {}
    for nome, doc in arquivos.items():
        linha = db.memoria_linha(nome, colecao) or {}
        vetor = linha.get("vetor")
        compativel = embeddings.compativel(str(linha.get("assinatura") or ""), assinatura_atual)
        if forcar or vetor is None or not compativel:
            pendentes[nome] = doc.texto_para_vetor()

    if not pendentes:
        return 0

    try:
        vetores = await embeddings.com_cache(pendentes, embeddings.DOCUMENTO)
    except embeddings.EmbeddingError as exc:
        log.debug("[memoria] indexação adiada: %s", exc)
        return 0

    for nome, vetor in vetores.items():
        db.memoria_upsert(
            nome, colecao, vetor=vetor, assinatura=embeddings.assinatura(),
        )
    log.debug("[memoria] indexadas=%d colecao=%s", len(vetores), colecao)
    return len(vetores)


def _matriz(colecao: str, docs_por_nome: dict[str, Doc]) -> tuple[list[str], np.ndarray]:
    """Os vetores indexados, na ordem dos nomes devolvidos."""
    nomes: list[str] = []
    linhas: list[np.ndarray] = []
    for linha in db.memoria_linhas(colecao):
        nome = str(linha["nome"])
        vetor = linha.get("vetor")
        if vetor is None or nome not in docs_por_nome:
            continue
        nomes.append(nome)
        linhas.append(vetor)

    if not linhas:
        return [], np.zeros((0, 0), dtype=np.float32)

    # Vetores de dimensões diferentes convivem quando alguém trocou de gerador
    # no meio. Empilhar daria erro; ficamos com o formato dominante e o resto
    # espera a próxima indexação.
    dominante = max({v.shape[0] for v in linhas}, key=lambda d: sum(1 for v in linhas if v.shape[0] == d))
    filtrados = [(n, v) for n, v in zip(nomes, linhas) if v.shape[0] == dominante]
    if not filtrados:
        return [], np.zeros((0, 0), dtype=np.float32)
    return [n for n, _ in filtrados], np.vstack([v for _, v in filtrados])


# --------------------------------------------------------------------------
# Pontuação
# --------------------------------------------------------------------------


def _recencia(linha: dict[str, object], doc: Doc) -> float:
    """1.0 para hoje, caindo pela metade a cada MEIA_VIDA_DIAS."""
    quando = (
        _quando(str(linha.get("atualizado_em") or ""))
        or _quando(str(linha.get("criado_em") or ""))
        or _quando(doc.created)
    )
    if quando is None:
        return 0.5
    dias = max(0.0, (datetime.now(timezone.utc) - quando).total_seconds() / 86400)
    return float(0.5 ** (dias / MEIA_VIDA_DIAS))


def _uso(linha: dict[str, object]) -> float:
    try:
        usos = int(linha.get("usos") or 0)
    except (TypeError, ValueError):
        usos = 0
    return min(1.0, usos / USOS_SATURACAO)


def _pontuar(
    doc: Doc, linha: dict[str, object], semelhanca: float, escopo: str | None
) -> tuple[float, dict[str, float]]:
    parcelas = {
        "semelhanca": PESOS["semelhanca"] * max(0.0, semelhanca),
        "importancia": PESOS["importancia"] * doc.importance,
        "recencia": PESOS["recencia"] * _recencia(linha, doc),
        "uso": PESOS["uso"] * _uso(linha),
    }
    nota = sum(parcelas.values())

    # Memória do projeto sobre o qual se está falando ganha um empurrão. Não é
    # exclusividade: uma preferência global continua podendo aparecer.
    if escopo and doc.scope == escopo and escopo != "global":
        parcelas["escopo"] = 0.12
        nota += 0.12

    return nota, parcelas


# --------------------------------------------------------------------------
# Recuperação
# --------------------------------------------------------------------------


async def recuperar(
    pergunta: str,
    *,
    colecao: str = "memories",
    escopo: str | None = None,
    limite: int | None = None,
    limiar: float | None = None,
) -> list[Achado]:
    """As memórias que têm a ver com esta pergunta, da melhor para a pior.

    Devolve lista vazia quando nada é relevante — e isso é o resultado certo,
    não uma falha. Enfiar memória sem relação no prompt só gasta contexto e
    convida o modelo a forçar uma conexão que não existe.
    """
    store = COLECOES[colecao]
    limite = limite if limite is not None else config.MEMORY_MAX_ITEMS
    limiar = limiar if limiar is not None else config.MEMORY_RELEVANCE_THRESHOLD

    ativos = {d.name: d for d in store.ativos()}
    if not ativos or limite <= 0:
        return []

    await indexar(colecao)

    nomes, matriz = _matriz(colecao, ativos)
    semelhancas: dict[str, float] = {}

    if nomes and pergunta.strip():
        try:
            alvo, _ = await embeddings.gerar_um(pergunta, embeddings.PERGUNTA)
            if alvo.shape[0] == matriz.shape[1]:
                valores = embeddings.semelhancas(matriz, alvo)
                semelhancas = {n: float(v) for n, v in zip(nomes, valores)}
        except embeddings.EmbeddingError as exc:
            log.debug("[memoria] busca semântica indisponível: %s", exc)

    achados: list[Achado] = []
    for nome, doc in ativos.items():
        linha = db.memoria_linha(nome, colecao) or {}
        semelhanca = semelhancas.get(nome, 0.0)
        nota, parcelas = _pontuar(doc, linha, semelhanca, escopo)

        sempre = doc.importance >= IMPORTANCIA_SEMPRE
        if not sempre and semelhancas and nota < limiar:
            continue
        achados.append(
            Achado(
                doc=doc,
                nota=1.0 if sempre else nota,
                semelhanca=semelhanca,
                motivo=_motivo(sempre, semelhanca, doc, escopo),
                parcelas=parcelas,
            )
        )

    achados.sort(key=lambda a: a.nota, reverse=True)
    escolhidos = achados[:limite]

    db.memoria_registrar_uso([a.doc.name for a in escolhidos], colecao)
    log.debug("[memoria] retrieved=%d de=%d colecao=%s", len(escolhidos), len(ativos), colecao)
    return escolhidos


def _motivo(sempre: bool, semelhanca: float, doc: Doc, escopo: str | None) -> str:
    if sempre:
        return "marcada como sempre relevante"
    if escopo and doc.scope == escopo and escopo != "global":
        return f"é do projeto em questão ({doc.scope})"
    if semelhanca >= 0.5:
        return "tem a ver com o que foi perguntado"
    if semelhanca > 0:
        return "tem relação distante com a pergunta"
    return "entrou por importância e recência"


def formatar(achados: list[Achado], titulo: str) -> str:
    """O bloco que entra no prompt."""
    if not achados:
        return ""
    blocos = [f"# {titulo}", ""]
    for a in achados:
        blocos.append(a.doc.as_block())
        blocos.append("")
    return "\n".join(blocos).strip()


def explicar(achados: list[Achado]) -> list[dict[str, object]]:
    """Resposta para "por que você lembrou disso?" — sem inventar.

    Cada item diz o nome da memória, a nota e a parcela que mais pesou. Isso
    sai da conta que foi realmente feita, não de uma explicação gerada depois
    pelo modelo (que soaria melhor e poderia ser mentira).
    """
    saida = []
    for a in achados:
        maior = max(a.parcelas.items(), key=lambda kv: kv[1], default=("", 0.0))
        saida.append({
            "nome": a.doc.name,
            "descricao": a.doc.description,
            "escopo": a.doc.scope,
            "nota": round(a.nota, 3),
            "pesou_mais": maior[0],
            "motivo": a.motivo,
        })
    return saida


# --------------------------------------------------------------------------
# Duplicatas e contradições
# --------------------------------------------------------------------------


async def semelhantes(
    texto: str, *, colecao: str = "memories", limiar: float | None = None, limite: int = 5
) -> list[Achado]:
    """Memórias que já dizem quase a mesma coisa que `texto`.

    É a checagem que roda ANTES de gravar. Sem ela, "prefere Postgres",
    "gosta de Postgres" e "usa Postgres nos projetos" viram três memórias, e
    daí a trinta itens dizendo variações da mesma frase.
    """
    limiar = limiar if limiar is not None else config.MEMORY_DUPLICATE_THRESHOLD
    store = COLECOES[colecao]
    ativos = {d.name: d for d in store.ativos()}
    if not ativos or not texto.strip():
        return []

    await indexar(colecao)
    nomes, matriz = _matriz(colecao, ativos)
    if not nomes:
        return []

    try:
        alvo, _ = await embeddings.gerar_um(texto, embeddings.PERGUNTA)
    except embeddings.EmbeddingError:
        return []
    if alvo.shape[0] != matriz.shape[1]:
        return []

    valores = embeddings.semelhancas(matriz, alvo)
    achados = [
        Achado(doc=ativos[n], nota=float(v), semelhanca=float(v), motivo="parecida")
        for n, v in zip(nomes, valores)
        if float(v) >= limiar
    ]
    achados.sort(key=lambda a: a.nota, reverse=True)
    return achados[:limite]


def substituir(antiga: str, nova: str, *, colecao: str = "memories") -> bool:
    """Marca uma memória como superada por outra, preservando as duas.

    O caso concreto: "o CRM usa Firebase" seguido de "migramos para Supabase".
    Manter as duas ativas devolveria as duas no prompt e a Livia responderia
    Firebase metade das vezes. Apagar a antiga perderia o registro de que
    aquilo já foi verdade — e depois ninguém entende por que havia código do
    Firebase no repositório.

    Superar resolve as duas coisas: a antiga sai do prompt e continua no
    disco, apontando para quem a substituiu.
    """
    store = COLECOES[colecao]
    if store.get(antiga) is None or store.get(nova) is None:
        return False

    store.patch(antiga, status=docs.SUBSTITUIDA, superseded_by=nova)
    store.patch(nova, supersedes=antiga)
    sincronizar(colecao)
    log.debug("[memoria] %s -> superseded_by %s", antiga, nova)
    return True


def arquivar(nome: str, *, colecao: str = "memories") -> bool:
    """Tira do prompt sem apagar. Reversível, ao contrário de deletar."""
    store = COLECOES[colecao]
    if store.get(nome) is None:
        return False
    store.patch(nome, status=docs.ARQUIVADA)
    sincronizar(colecao)
    return True


def reativar(nome: str, *, colecao: str = "memories") -> bool:
    store = COLECOES[colecao]
    if store.get(nome) is None:
        return False
    store.patch(nome, status=docs.ATIVA, superseded_by=None)
    sincronizar(colecao)
    return True


# --------------------------------------------------------------------------
# Escopo (memória de projeto)
# --------------------------------------------------------------------------

PREFIXO_PROJETO = "project:"
PREFIXO_CONVERSA = "conversation:"
GLOBAL = "global"


def escopo_projeto(identificador: str) -> str:
    return f"{PREFIXO_PROJETO}{docs.slugify(identificador)}"


def projetos_conhecidos() -> dict[str, str]:
    """Projetos que já aparecem em alguma memória: {slug: rótulo legível}.

    A lista sai dos dados, não de um cadastro. Um projeto passa a existir no
    instante em que a primeira memória dele é gravada, e some quando a última
    é apagada — sem tela de gerenciamento para manter sincronizada.
    """
    achados: dict[str, str] = {}
    for colecao in COLECOES:
        for doc in COLECOES[colecao].all():
            if doc.scope.startswith(PREFIXO_PROJETO):
                slug = doc.scope[len(PREFIXO_PROJETO):]
                achados.setdefault(slug, slug.replace("-", " "))
    return achados


def _pastas_do_workspace() -> dict[str, str]:
    """Subpastas da pasta de trabalho também nomeiam projetos."""
    try:
        raiz = config.WORKSPACE
        if not raiz.exists():
            return {}
        return {
            docs.slugify(p.name): p.name
            for p in raiz.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        }
    except OSError:
        return {}


def detectar_projeto(
    mensagem: str, historico: list[dict[str, str]] | None = None
) -> str | None:
    """Sobre qual projeto é esta conversa? Sem chamar IA.

    Gastar uma chamada de modelo para responder isto dobraria a latência de
    toda mensagem para resolver uma pergunta que casar nomes resolve bem. Os
    nomes vêm de dois lugares que já existem: as memórias com escopo de
    projeto e as pastas do workspace.

    Sem evidência suficiente, devolve None — e aí vale a memória global. Um
    palpite errado aqui é pior que nenhum: puxaria o contexto do projeto
    errado para dentro da resposta.
    """
    candidatos = {**_pastas_do_workspace(), **projetos_conhecidos()}
    if not candidatos:
        return None

    # A mensagem atual pesa mais que o histórico: o assunto pode ter mudado
    # agora, e a conversa inteira não deve arrastar o projeto anterior.
    textos = [(mensagem or "", 3.0)]
    for m in reversed((historico or [])[-6:]):
        textos.append((str(m.get("content") or ""), 1.0))

    notas: dict[str, float] = {}
    for slug, rotulo in candidatos.items():
        # Termos que valem: o slug inteiro e cada palavra dele com 4+ letras.
        # "crm" e "api" sozinhos casariam com qualquer conversa técnica.
        termos = {slug.replace("-", " "), slug.replace("-", "")}
        termos |= {p for p in slug.split("-") if len(p) >= 4}
        termos |= {rotulo.lower()}

        for texto, peso in textos:
            baixo = texto.lower()
            for termo in termos:
                if termo and re.search(rf"\b{re.escape(termo)}\b", baixo):
                    notas[slug] = notas.get(slug, 0.0) + peso
                    break

    if not notas:
        return None
    melhor = max(notas.items(), key=lambda kv: kv[1])
    if melhor[1] < 1.0:
        return None
    return escopo_projeto(melhor[0])


# --------------------------------------------------------------------------
# Gravação com checagem
# --------------------------------------------------------------------------


async def guardar(
    nome: str,
    descricao: str,
    *,
    corpo: str = "",
    kind: str = "fact",
    escopo: str = GLOBAL,
    importancia: float = 0.5,
    origem: str = "",
    colecao: str = "memories",
    substitui: str | None = None,
    verificar_duplicata: bool = True,
) -> dict[str, object]:
    """Grava uma memória, decidindo antes o que fazer com as parecidas.

    Devolve o que aconteceu — `criada`, `atualizada`, `substituida` ou
    `ignorada` — para quem chamou poder contar ao André em vez de deixá-lo
    adivinhar por que a memória dele não apareceu.
    """
    store = COLECOES[colecao]
    texto = f"{nome.replace('-', ' ')}\n{descricao}\n{corpo}".strip()

    if verificar_duplicata and substitui is None:
        parecidas = await semelhantes(texto, colecao=colecao)
        for achado in parecidas:
            if achado.doc.name == docs.slugify(nome):
                break  # é a mesma memória, atualizar é o certo
            # Quase idêntica: não vale um arquivo novo. A mais recente ganha
            # o texto, e a antiga fica registrada como superada.
            substitui = achado.doc.name
            break

    extra = {
        "scope": escopo,
        "status": docs.ATIVA,
        "importance": f"{max(0.0, min(1.0, importancia)):.2f}",
    }
    if origem:
        extra["source"] = origem

    existia = store.get(nome) is not None
    doc = store.save(nome, descricao, corpo, kind=kind, extra=extra)

    resultado = "atualizada" if existia else "criada"
    if substitui and substitui != doc.name:
        if substituir(substitui, doc.name, colecao=colecao):
            resultado = "substituida"

    sincronizar(colecao)
    return {
        "resultado": resultado,
        "memoria": doc.to_json(),
        "substituiu": substitui if resultado == "substituida" else "",
    }


# --------------------------------------------------------------------------
# Manutenção
# --------------------------------------------------------------------------

# Sem uso e sem importância por tanto tempo, vira candidata a arquivamento.
DIAS_PARA_OBSOLETA = 180


async def manutencao(*, aplicar: bool = False) -> dict[str, object]:
    """Faxina da memória: duplicatas, conflitos, esquecidas.

    Roda por comando, não em thread de fundo. Uma tarefa periódica frágil que
    reescreve memória sem ninguém olhando é justamente o tipo de coisa que,
    quando erra, ninguém descobre a tempo.

    Com `aplicar=False` (o padrão) só RELATA. É o modo em que se olha antes de
    deixar a máquina mexer no que o André escreveu.
    """
    from . import experiencia

    relatorio: dict[str, object] = {
        "duplicatas": [], "obsoletas": [], "conflitos": [],
        "arquivadas": 0, "aplicado": aplicar,
    }

    sincronizar("memories")
    await indexar("memories")

    ativos = COLECOES["memories"].ativos()
    vistos: set[str] = set()

    for doc in ativos:
        if doc.name in vistos:
            continue
        parecidas = [
            a for a in await semelhantes(doc.texto_para_vetor(), colecao="memories")
            if a.doc.name != doc.name and a.doc.name not in vistos
        ]
        for achado in parecidas:
            vistos.add(achado.doc.name)
            par = {
                "mantida": doc.name,
                "parecida": achado.doc.name,
                "semelhanca": round(achado.semelhanca, 3),
            }
            relatorio["duplicatas"].append(par)
            if aplicar:
                # A mais recente prevalece; a outra vira superada, não some.
                antiga, nova = _mais_antiga(doc, achado.doc)
                substituir(antiga.name, nova.name)

    limite = datetime.now(timezone.utc)
    for doc in COLECOES["memories"].ativos():
        linha = db.memoria_linha(doc.name) or {}
        if doc.importance >= IMPORTANCIA_SEMPRE:
            continue
        usada = _quando(str(linha.get("usado_em") or ""))
        criada = _quando(str(linha.get("criado_em") or "")) or _quando(doc.created)
        referencia = usada or criada
        if referencia is None:
            continue
        dias = (limite - referencia).days
        if dias >= DIAS_PARA_OBSOLETA and not int(linha.get("usos") or 0):
            relatorio["obsoletas"].append({"nome": doc.name, "dias_sem_uso": dias})
            if aplicar:
                arquivar(doc.name)
                relatorio["arquivadas"] = int(relatorio["arquivadas"]) + 1

    for doc in COLECOES["memories"].all():
        if doc.status == docs.SUBSTITUIDA and not doc.superseded_by:
            relatorio["conflitos"].append(
                {"nome": doc.name, "problema": "marcada como superada sem dizer por quem"}
            )

    promovidas = await experiencia.consolidar(aplicar=aplicar)
    relatorio["licoes"] = promovidas

    return relatorio


def _mais_antiga(a: Doc, b: Doc) -> tuple[Doc, Doc]:
    """(mais antiga, mais nova). Empate: a ordem alfabética decide, para o
    resultado não depender da ordem de leitura do sistema de arquivos."""
    quando_a = a.meta.get("updated") or a.created or ""
    quando_b = b.meta.get("updated") or b.created or ""
    if (quando_a, a.name) <= (quando_b, b.name):
        return a, b
    return b, a


def estatisticas() -> dict[str, int]:
    todas = memory.all()
    return {
        "total": len(todas),
        "ativas": sum(1 for d in todas if d.ativa),
        "substituidas": sum(1 for d in todas if d.status == docs.SUBSTITUIDA),
        "arquivadas": sum(1 for d in todas if d.status == docs.ARQUIVADA),
        "projetos": len(projetos_conhecidos()),
    }
