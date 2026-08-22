"""Memória semântica: recuperar por significado, sem duplicar, sem contradizer.

Os embeddings são falsos aqui — um dicionário de texto para vetor, montado no
teste. Isso é de propósito e não enfraquece nada: o que precisa ser testado é
a LÓGICA (pontuação, limiar, substituição, escopo), não a qualidade do modelo
de vetores. Testar a qualidade do embedding exigiria rede, seria lento e
mediria o fornecedor, não este código.
"""

from __future__ import annotations

import numpy as np
import pytest

from livia import config, db, docs, embeddings, memoria
from livia.store import COLECOES


# --------------------------------------------------------------------------
# Andaime
# --------------------------------------------------------------------------


class VetorFalso:
    """Gerador de vetores determinístico, guiado por palavras-chave.

    Cada eixo é um assunto. Um texto que fala de banco de dados fica perto de
    outro que fala de banco de dados, e longe de um que fala de impressora —
    que é exatamente a propriedade que a busca semântica precisa ter.
    """

    # O último eixo é "assunto nenhum": sem ele, um texto sem palavra-chave
    # viraria vetor uniforme e ficaria a meio caminho de TODOS os assuntos —
    # o que faria "me conta uma piada" parecer relacionado a banco de dados.
    EIXOS = ["banco", "impressora", "interface", "pessoa", "deploy", "outro"]
    PALAVRAS = {
        "banco": ("postgres", "sqlite", "supabase", "firebase", "banco", "dados"),
        "impressora": ("impressora", "epson", "wps", "imprimir", "papel"),
        "interface": ("interface", "escura", "tema", "cor", "visual", "ui"),
        "pessoa": ("andré", "andre", "joão", "joao", "pessoa", "sócio", "socio"),
        "deploy": ("deploy", "servidor", "docker", "publicar", "produção"),
        "outro": (),
    }

    def __init__(self):
        self.chamadas = 0

    def __call__(self, textos, tarefa="documento"):
        self.chamadas += 1
        linhas = []
        for texto in textos:
            baixo = (texto or "").lower()
            vetor = [
                float(sum(baixo.count(p) for p in self.PALAVRAS[eixo]))
                for eixo in self.EIXOS
            ]
            if not any(vetor):
                vetor[-1] = 1.0
            linhas.append(vetor)
        matriz = embeddings.normalizar(np.array(linhas, dtype=np.float32))
        return matriz, "falso:teste:6"


@pytest.fixture
def vetores(monkeypatch):
    """Troca o gerador de vetores por um determinístico, sem rede."""
    falso = VetorFalso()

    async def gerar(textos, tarefa=embeddings.DOCUMENTO):
        return falso(textos, tarefa)

    async def gerar_um(texto, tarefa=embeddings.PERGUNTA):
        matriz, assin = falso([texto], tarefa)
        return matriz[0], assin

    async def com_cache(itens, tarefa=embeddings.DOCUMENTO):
        if not itens:
            return {}
        chaves = list(itens)
        matriz, _ = falso([itens[k] for k in chaves], tarefa)
        return dict(zip(chaves, matriz))

    monkeypatch.setattr(embeddings, "gerar", gerar)
    monkeypatch.setattr(embeddings, "gerar_um", gerar_um)
    monkeypatch.setattr(embeddings, "com_cache", com_cache)
    monkeypatch.setattr(embeddings, "disponivel", lambda: True)
    monkeypatch.setattr(embeddings, "assinatura", lambda *a, **k: "falso:teste:6")
    monkeypatch.setattr(embeddings, "compativel", lambda gravada, atual=None: True)
    return falso


@pytest.fixture
def memorias(tmp_path, monkeypatch):
    """Pasta de memórias vazia e banco limpo, por teste."""
    pasta = tmp_path / "memory"
    pasta.mkdir()
    monkeypatch.setattr(COLECOES["memories"], "directory", pasta)

    for nome in list(db.memoria_nomes("memories")):
        db.memoria_apagar(nome, "memories")
    yield pasta
    for nome in list(db.memoria_nomes("memories")):
        db.memoria_apagar(nome, "memories")


def gravar(store_dir, nome, descricao, corpo="", **extra):
    from livia import docs as _docs

    return _docs.write(
        store_dir, nome, descricao, corpo,
        kind=extra.pop("kind", "fact"), extra=extra or None,
    )


# --------------------------------------------------------------------------
# Sincronização e migração
# --------------------------------------------------------------------------


def test_memorias_antigas_sao_indexadas_sozinhas(memorias):
    """Fase 34: quem atualiza a Livia não faz conversão manual nenhuma."""
    (memorias / "antiga.md").write_text(
        "---\nname: antiga\ndescription: Prefere Postgres.\nkind: preferencia\n"
        "created: 2026-01-01\n---\n\nSem escopo, sem status, como era antes.\n",
        encoding="utf-8",
    )

    resultado = memoria.sincronizar()
    assert resultado["novas"] == 1

    linha = db.memoria_linha("antiga")
    assert linha["escopo"] == "global"     # ausente no arquivo → padrão
    assert linha["status"] == "active"
    assert linha["kind"] == "preference"   # `preferencia` traduzido, sem reescrever


def test_o_arquivo_antigo_nao_e_reescrito(memorias):
    """Migrar não pode sujar o Markdown do André."""
    caminho = memorias / "antiga.md"
    original = (
        "---\nname: antiga\ndescription: Prefere Postgres.\nkind: preferencia\n---\n\n"
        "Corpo original.\n"
    )
    caminho.write_text(original, encoding="utf-8")
    memoria.sincronizar()
    assert caminho.read_text(encoding="utf-8") == original


def test_arquivo_apagado_a_mao_some_do_indice(memorias):
    gravar(memorias, "temporaria", "Vai sumir.")
    memoria.sincronizar()
    assert "temporaria" in db.memoria_nomes()

    (memorias / "temporaria.md").unlink()
    resultado = memoria.sincronizar()
    assert resultado["removidas"] == 1
    assert "temporaria" not in db.memoria_nomes()


def test_texto_alterado_invalida_o_vetor(memorias, vetores):
    gravar(memorias, "mem", "Usa Postgres.")
    memoria.sincronizar()
    db.memoria_upsert("mem", "memories", vetor=np.ones(6, dtype=np.float32),
                      assinatura="falso:teste:6")

    gravar(memorias, "mem", "Usa SQLite agora.")
    memoria.sincronizar()

    assert db.memoria_linha("mem")["vetor"] is None, (
        "vetor velho com texto novo compararia a pergunta com o texto de ontem"
    )


# --------------------------------------------------------------------------
# Recuperação semântica
# --------------------------------------------------------------------------


async def test_recupera_o_que_tem_a_ver_e_deixa_o_resto(memorias, vetores):
    gravar(memorias, "banco-do-crm", "O CRM usa Supabase como banco de dados.")
    gravar(memorias, "impressora", "A impressora Epson do escritório trava no WPS.")
    gravar(memorias, "tema", "Prefere interface escura em tudo.")

    achados = await memoria.recuperar("qual banco de dados o projeto usa?")
    nomes = [a.doc.name for a in achados]

    assert nomes[0] == "banco-do-crm"
    assert "impressora" not in nomes


async def test_pergunta_sem_relacao_nao_traz_nada(memorias, vetores):
    gravar(memorias, "banco-do-crm", "O CRM usa Supabase como banco de dados.")
    achados = await memoria.recuperar("me conta uma piada sobre gatos")
    assert achados == []


async def test_memoria_critica_entra_sempre(memorias, vetores):
    """Identidade e decisões que valem sempre não dependem da pergunta."""
    gravar(memorias, "quem-e-o-andre", "André é desenvolvedor e mora em Curitiba.",
           importance="0.95")
    gravar(memorias, "banco", "O CRM usa Supabase.")

    achados = await memoria.recuperar("me conta uma piada sobre gatos")
    assert [a.doc.name for a in achados] == ["quem-e-o-andre"]
    assert achados[0].motivo == "marcada como sempre relevante"


async def test_orcamento_limita_quantas_entram(memorias, vetores):
    for i in range(12):
        gravar(memorias, f"banco-{i}", f"Nota {i} sobre banco de dados Postgres.")

    achados = await memoria.recuperar("banco de dados", limite=4)
    assert len(achados) == 4


async def test_uso_e_registrado_para_a_manutencao(memorias, vetores):
    gravar(memorias, "banco", "O CRM usa Supabase como banco de dados.")
    await memoria.recuperar("qual o banco?")
    await memoria.recuperar("banco de dados do projeto")

    assert db.memoria_linha("banco")["usos"] == 2
    assert db.memoria_linha("banco")["usado_em"]


async def test_memoria_substituida_nao_volta_ao_prompt(memorias, vetores):
    gravar(memorias, "firebase", "O CRM usa Firebase como banco.")
    gravar(memorias, "supabase", "O CRM usa Supabase como banco.")
    memoria.substituir("firebase", "supabase")

    achados = await memoria.recuperar("qual o banco do CRM?")
    assert [a.doc.name for a in achados] == ["supabase"]


async def test_sem_gerador_de_vetores_a_memoria_nao_some(memorias, monkeypatch):
    """Degradar é aceitável; ficar sem a memória do André, não."""
    monkeypatch.setattr(embeddings, "disponivel", lambda: False)

    async def falhar(*a, **k):
        raise embeddings.EmbeddingError("sem provedor")

    monkeypatch.setattr(embeddings, "gerar_um", falhar)
    gravar(memorias, "banco", "O CRM usa Supabase.")

    achados = await memoria.recuperar("qualquer pergunta")
    assert [a.doc.name for a in achados] == ["banco"]
    assert achados[0].motivo == "entrou por importância e recência"


# --------------------------------------------------------------------------
# Duplicatas
# --------------------------------------------------------------------------


async def test_duplicata_e_detectada_antes_de_gravar(memorias, vetores):
    gravar(memorias, "prefere-postgres", "Prefere Postgres como banco de dados.")
    parecidas = await memoria.semelhantes("Gosta de Postgres para banco de dados.")
    assert [a.doc.name for a in parecidas] == ["prefere-postgres"]


async def test_memoria_quase_igual_substitui_em_vez_de_somar(memorias, vetores):
    gravar(memorias, "prefere-postgres", "Prefere Postgres como banco de dados.")
    memoria.sincronizar()

    resultado = await memoria.guardar(
        "gosta-de-postgres", "Gosta de Postgres para banco de dados."
    )

    assert resultado["resultado"] == "substituida"
    ativos = [d.name for d in COLECOES["memories"].ativos()]
    assert ativos == ["gosta-de-postgres"]
    # A antiga continua no disco, marcada.
    antiga = COLECOES["memories"].get("prefere-postgres")
    assert antiga.status == docs.SUBSTITUIDA
    assert antiga.superseded_by == "gosta-de-postgres"


async def test_memoria_diferente_e_criada_normalmente(memorias, vetores):
    gravar(memorias, "prefere-postgres", "Prefere Postgres como banco de dados.")
    memoria.sincronizar()

    resultado = await memoria.guardar(
        "impressora-epson", "A impressora Epson trava no WPS."
    )
    assert resultado["resultado"] == "criada"
    assert len(COLECOES["memories"].ativos()) == 2


# --------------------------------------------------------------------------
# Contradição
# --------------------------------------------------------------------------


def test_substituir_preserva_o_historico(memorias):
    gravar(memorias, "firebase", "O CRM usa Firebase.")
    gravar(memorias, "supabase", "O CRM usa Supabase.")

    assert memoria.substituir("firebase", "supabase") is True

    antiga = COLECOES["memories"].get("firebase")
    nova = COLECOES["memories"].get("supabase")
    assert antiga.status == docs.SUBSTITUIDA
    assert antiga.superseded_by == "supabase"
    assert nova.supersedes == "firebase"
    assert nova.ativa
    # Nada foi apagado: o registro de que já foi Firebase continua legível.
    assert (memorias / "firebase.md").exists()
    assert "O CRM usa Firebase" in (memorias / "firebase.md").read_text(encoding="utf-8")


def test_substituir_memoria_inexistente_nao_faz_nada(memorias):
    gravar(memorias, "supabase", "O CRM usa Supabase.")
    assert memoria.substituir("nao-existe", "supabase") is False


def test_arquivar_e_reativar(memorias):
    gravar(memorias, "antiga", "Coisa velha.")
    assert memoria.arquivar("antiga") is True
    assert COLECOES["memories"].ativos() == []

    assert memoria.reativar("antiga") is True
    assert [d.name for d in COLECOES["memories"].ativos()] == ["antiga"]


def test_render_nao_inclui_substituida(memorias):
    gravar(memorias, "firebase", "O CRM usa Firebase.")
    gravar(memorias, "supabase", "O CRM usa Supabase.")
    memoria.substituir("firebase", "supabase")

    texto = COLECOES["memories"].render()
    assert "Supabase" in texto and "Firebase" not in texto


# --------------------------------------------------------------------------
# Escopo de projeto
# --------------------------------------------------------------------------


async def test_memoria_do_projeto_ganha_do_global_empatado(memorias, vetores):
    gravar(memorias, "global-postgres", "Prefere Postgres como banco de dados.",
           scope="global")
    gravar(memorias, "livia-sqlite", "O projeto usa SQLite como banco de dados.",
           scope="project:livia")

    achados = await memoria.recuperar(
        "qual banco de dados?", escopo="project:livia"
    )
    assert achados[0].doc.name == "livia-sqlite"
    assert "projeto" in achados[0].motivo


async def test_preferencia_global_continua_aparecendo_no_projeto(memorias, vetores):
    """Fase 17: escopo prioriza, não exclui. As duas verdades convivem."""
    gravar(memorias, "global-postgres", "Prefere Postgres como banco de dados.",
           scope="global")
    gravar(memorias, "livia-sqlite", "O projeto usa SQLite como banco de dados.",
           scope="project:livia")

    nomes = [
        a.doc.name
        for a in await memoria.recuperar("banco de dados", escopo="project:livia")
    ]
    assert set(nomes) == {"livia-sqlite", "global-postgres"}


def test_projetos_saem_das_proprias_memorias(memorias):
    gravar(memorias, "backend", "Usa FastAPI.", scope="project:crm-direcional")
    gravar(memorias, "tema", "Interface escura.", scope="global")
    assert "crm-direcional" in memoria.projetos_conhecidos()


def test_detecta_projeto_citado_na_mensagem(memorias):
    gravar(memorias, "backend", "Usa FastAPI.", scope="project:crm-direcional")
    assert memoria.detectar_projeto(
        "no crm-direcional, como está o backend?"
    ) == "project:crm-direcional"


def test_sem_evidencia_nao_chuta_projeto(memorias):
    """Palpite errado aqui puxaria o contexto do projeto errado."""
    gravar(memorias, "backend", "Usa FastAPI.", scope="project:crm-direcional")
    assert memoria.detectar_projeto("me explica o que é uma API REST") is None


def test_projeto_da_mensagem_atual_ganha_do_historico(memorias):
    gravar(memorias, "a", "x", scope="project:crm-direcional")
    gravar(memorias, "b", "y", scope="project:loja-virtual")

    escopo = memoria.detectar_projeto(
        "agora vamos falar da loja-virtual",
        [{"role": "user", "content": "sobre o crm-direcional..."}],
    )
    assert escopo == "project:loja-virtual"


def test_pasta_do_workspace_tambem_nomeia_projeto(memorias, workspace, monkeypatch):
    (workspace / "CRM-DIRECIONAL").mkdir()
    monkeypatch.setattr(config, "WORKSPACE", workspace)
    assert memoria.detectar_projeto(
        "abre o crm-direcional pra mim"
    ) == "project:crm-direcional"


# --------------------------------------------------------------------------
# Explicabilidade
# --------------------------------------------------------------------------


async def test_explica_por_que_lembrou(memorias, vetores):
    """Fase 22: a explicação sai da conta feita, não de um texto gerado."""
    gravar(memorias, "banco", "O CRM usa Supabase como banco de dados.")
    achados = await memoria.recuperar("qual o banco de dados?")

    explicacao = memoria.explicar(achados)
    assert explicacao[0]["nome"] == "banco"
    assert explicacao[0]["pesou_mais"] == "semelhanca"
    assert 0 < explicacao[0]["nota"] <= 1


# --------------------------------------------------------------------------
# Manutenção
# --------------------------------------------------------------------------


async def test_manutencao_relata_sem_aplicar_por_padrao(memorias, vetores):
    gravar(memorias, "prefere-postgres", "Prefere Postgres como banco de dados.")
    gravar(memorias, "gosta-postgres", "Gosta de Postgres para banco de dados.")
    memoria.sincronizar()

    relatorio = await memoria.manutencao()
    assert relatorio["aplicado"] is False
    assert relatorio["duplicatas"]
    # Nada mudou no disco.
    assert len(COLECOES["memories"].ativos()) == 2


async def test_manutencao_aplicada_resolve_a_duplicata(memorias, vetores):
    gravar(memorias, "prefere-postgres", "Prefere Postgres como banco de dados.")
    gravar(memorias, "gosta-postgres", "Gosta de Postgres para banco de dados.")
    memoria.sincronizar()

    await memoria.manutencao(aplicar=True)
    assert len(COLECOES["memories"].ativos()) == 1
    assert len(COLECOES["memories"].all()) == 2, "a outra tinha que ficar no disco"
