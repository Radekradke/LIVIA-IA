"""Memória episódica: registrar o que aconteceu e aprender com o padrão.

O teste mais importante deste arquivo é o que NÃO deixa marcar sucesso sem
evidência. É a diferença entre uma assistente que aprende com a realidade e
uma que aprende com a própria autoconfiança — e a segunda vira, em três
meses, uma coleção de heurísticas erradas ditas com firmeza.
"""

from __future__ import annotations

import numpy as np
import pytest

from livia import config, db, experiencia
from livia.store import lessons, skills
from tests.test_memoria import VetorFalso, vetores  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _limpo(tmp_path, monkeypatch):
    """Banco de experiências e pastas de lição/skill vazios por teste."""
    for linha in db.experiencia_todas():
        db.experiencia_apagar(int(linha["id"]))
    for candidata in db.candidata_listar("pendente"):
        db.candidata_situacao(int(candidata["id"]), "descartada")

    monkeypatch.setattr(lessons, "directory", tmp_path / "lessons")
    monkeypatch.setattr(skills, "directory", tmp_path / "skills")
    (tmp_path / "lessons").mkdir()
    (tmp_path / "skills").mkdir()

    monkeypatch.setattr(config, "EXPERIENCE_ENABLED", True)
    yield
    for linha in db.experiencia_todas():
        db.experiencia_apagar(int(linha["id"]))


def acao(nome: str, ok: bool = True, resultado: str = "") -> dict[str, object]:
    return {"nome": nome, "ok": ok, "resultado": resultado}


# ── sinais de sucesso e falha ─────────────────────────────────────────────


def test_ferramenta_que_rodou_conta_como_sucesso():
    sinais = experiencia.avaliar([acao("escrever_arquivo")])
    assert sinais.sucesso is True
    assert "sem erro" in sinais.motivo


def test_ferramenta_que_falhou_conta_como_falha():
    sinais = experiencia.avaliar([acao("ler_arquivo", ok=False, resultado="não existe")])
    assert sinais.sucesso is False
    assert "ler_arquivo" in sinais.motivo


def test_resposta_bonita_nao_e_evidencia_de_nada():
    """O erro clássico: achar que respondeu = acertou."""
    sinais = experiencia.avaliar([], resposta="Pronto! Resolvi tudo pra você. ✅")
    assert sinais.sucesso is None


def test_correcao_do_usuario_derruba_o_sucesso_aparente():
    """Mesmo com tudo rodando, se ele corrige logo depois, falhou."""
    sinais = experiencia.avaliar(
        [acao("escrever_arquivo")], proxima_mensagem="não é isso, era o outro arquivo"
    )
    assert sinais.sucesso is False
    assert "corrigiu" in sinais.motivo


def test_confirmacao_do_usuario_vale_como_sucesso():
    sinais = experiencia.avaliar([], proxima_mensagem="perfeito, funcionou")
    assert sinais.sucesso is True


@pytest.mark.parametrize(
    "frase",
    [
        "não é isso",
        "isso está errado",
        "na verdade, o banco é Supabase",
        "corrigindo: são 40 horas",
        "não usamos mais Firebase",
        "migramos para Supabase",
        "não funcionou",
    ],
)
def test_frases_de_correcao_sao_reconhecidas(frase):
    assert experiencia.e_correcao(frase) is True


@pytest.mark.parametrize(
    "frase",
    [
        "não sei se entendi",
        "acho que sim",
        "e como faço isso no Windows?",
        "não precisa fazer agora",
    ],
)
def test_conversa_normal_nao_vira_correcao(frase):
    """`não` sozinho aparece o tempo todo. Falso positivo aqui reescreveria
    memória certa por causa de uma frase inocente."""
    assert experiencia.e_correcao(frase) is False


@pytest.mark.parametrize(
    "frase",
    ["prefiro tabelas a listas", "a partir de agora, responda em inglês"],
)
def test_preferencia_declarada_e_reconhecida(frase):
    assert experiencia.e_preferencia(frase) is True


# ── registro ──────────────────────────────────────────────────────────────


def test_experiencia_e_gravada_com_o_veredito():
    id_ = experiencia.registrar(
        "configurar impressora",
        acoes=[acao("escrever_arquivo")],
        resultado="arquivo de configuração criado",
    )
    assert id_

    linha = db.experiencia_listar(1)[0]
    assert linha["tarefa"] == "configurar impressora"
    assert linha["sucesso"] is True
    assert linha["acoes"][0]["nome"] == "escrever_arquivo"


def test_bate_papo_nao_vira_experiencia():
    """Sem ação e sem veredito, não há experiência — só conversa."""
    assert experiencia.registrar("oi, tudo bem?") is None
    assert db.experiencia_listar(10) == []


def test_desligar_o_registro_e_respeitado(monkeypatch):
    monkeypatch.setattr(config, "EXPERIENCE_ENABLED", False)
    assert experiencia.registrar("x", acoes=[acao("calcular")]) is None


def test_erro_da_ferramenta_fica_guardado():
    experiencia.registrar(
        "ler configuração",
        acoes=[acao("ler_arquivo", ok=False, resultado="O arquivo não existe.")],
    )
    assert "não existe" in db.experiencia_listar(1)[0]["erro"]


# ── recuperação semântica ─────────────────────────────────────────────────


async def test_tarefa_parecida_traz_a_experiencia_certa(vetores):
    """Fase 8: meses depois, sem lembrar as palavras exatas."""
    experiencia.registrar(
        "impressora Epson não conecta pelo WPS",
        acoes=[acao("ler_arquivo", ok=False)],
    )
    experiencia.registrar(
        "migrar o banco de dados para Postgres", acoes=[acao("escrever_arquivo")]
    )

    achados = await experiencia.recuperar("a impressora parou de imprimir de novo")
    assert len(achados) == 1
    assert "Epson" in achados[0]["tarefa"]


async def test_tarefa_sem_relacao_nao_traz_nada(vetores):
    experiencia.registrar("impressora Epson travou no WPS", acoes=[acao("ler_arquivo")])
    assert await experiencia.recuperar("como faço deploy com docker") == []


async def test_formatacao_diz_o_que_funcionou_e_o_que_falhou(vetores):
    experiencia.registrar("impressora no WPS", acoes=[acao("calcular", ok=False)])
    achados = await experiencia.recuperar("impressora")
    bloco = experiencia.formatar(achados)
    assert "falhou" in bloco
    assert "histórico, não regra" in bloco


# ── heurísticas ───────────────────────────────────────────────────────────


async def test_um_caso_nao_vira_regra(vetores, monkeypatch):
    """Fase 10: virar regra a partir de um caso é aprender superstição."""
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    experiencia.registrar("impressora Epson no WPS", acoes=[acao("escrever_arquivo")])

    relatorio = await experiencia.consolidar(aplicar=True)
    assert relatorio["heuristicas"] == []
    assert lessons.count() == 0


async def test_padrao_repetido_vira_heuristica(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    for _ in range(3):
        experiencia.registrar(
            "impressora Epson não conecta no WPS",
            acoes=[acao("ler_arquivo"), acao("escrever_arquivo")],
        )

    relatorio = await experiencia.consolidar(aplicar=True)
    assert relatorio["heuristicas"]
    assert lessons.count() == 1

    licao = lessons.all()[0]
    assert licao.kind == "lesson"
    assert "impressora" in licao.description.lower()
    assert "Fazer:" in licao.body


async def test_resultado_dividido_nao_vira_regra(vetores, monkeypatch):
    """Metade funcionou, metade falhou: isso é variabilidade, não padrão."""
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    for ok in (True, False, True, False):
        experiencia.registrar(
            "impressora Epson no WPS", acoes=[acao("escrever_arquivo", ok=ok)]
        )

    relatorio = await experiencia.consolidar(aplicar=True)
    assert relatorio["heuristicas"] == []
    assert relatorio["anti_patterns"] == []


async def test_experiencia_sem_veredito_nao_vota(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 2)
    experiencia.registrar("impressora Epson", acoes=[acao("escrever_arquivo")])
    for _ in range(3):
        # Sem ações e sem veredito: nem é gravada.
        experiencia.registrar("impressora Epson")

    relatorio = await experiencia.consolidar()
    assert relatorio["heuristicas"] == []


async def test_consolidar_sem_aplicar_nao_escreve_nada(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 2)
    for _ in range(3):
        experiencia.registrar("impressora Epson no WPS", acoes=[acao("escrever_arquivo")])

    relatorio = await experiencia.consolidar(aplicar=False)
    assert relatorio["heuristicas"]
    assert lessons.count() == 0


# ── anti-patterns ─────────────────────────────────────────────────────────


async def test_falha_repetida_vira_anti_pattern(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    for _ in range(4):
        experiencia.registrar(
            "impressora Epson: reset completo",
            acoes=[acao("escrever_arquivo", ok=False, resultado="perdeu a configuração")],
        )

    relatorio = await experiencia.consolidar(aplicar=True)
    assert relatorio["anti_patterns"]
    assert relatorio["anti_patterns"][0]["falhas"] == 4

    licao = lessons.all()[0]
    assert "Evitar:" in licao.body
    assert "perdeu a configuração" in licao.body


async def test_anti_pattern_diz_o_motivo(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    for _ in range(3):
        experiencia.registrar(
            "deploy direto em produção com docker",
            acoes=[acao("escrever_arquivo", ok=False, resultado="derrubou o servidor")],
        )
    relatorio = await experiencia.consolidar(aplicar=True)
    assert "derrubou o servidor" in relatorio["anti_patterns"][0]["motivo"]


# ── skills candidatas ─────────────────────────────────────────────────────


async def test_procedimento_repetido_vira_candidata_nao_skill(vetores, monkeypatch):
    """Fase 12: ela propõe, o André decide. Nunca o contrário por padrão."""
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    monkeypatch.setattr(config, "SKILL_AUTO_APPROVE", False)
    for _ in range(3):
        experiencia.registrar(
            "publicar o site com docker no servidor",
            acoes=[acao("ler_arquivo"), acao("escrever_arquivo")],
        )

    relatorio = await experiencia.consolidar(aplicar=True)
    assert relatorio["candidatas"]
    assert skills.count() == 0, "nenhuma skill pode nascer sem aprovação"
    assert len(experiencia.candidatas()) == 1


async def test_aprovar_transforma_em_skill_de_verdade(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    for _ in range(3):
        experiencia.registrar(
            "publicar o site com docker no servidor",
            acoes=[acao("ler_arquivo"), acao("escrever_arquivo")],
        )
    await experiencia.consolidar(aplicar=True)

    candidata = experiencia.candidatas()[0]
    resultado = experiencia.aprovar(int(candidata["id"]))

    assert resultado is not None
    assert skills.count() == 1
    assert experiencia.candidatas() == []
    assert "aprovada pelo André" in skills.all()[0].source


async def test_rejeitar_nao_deixa_ela_propor_de_novo(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    for _ in range(3):
        experiencia.registrar(
            "publicar o site com docker", acoes=[acao("ler_arquivo"), acao("escrever_arquivo")]
        )
    await experiencia.consolidar(aplicar=True)

    candidata = experiencia.candidatas()[0]
    assert experiencia.rejeitar(int(candidata["id"])) is True
    assert experiencia.candidatas() == []
    assert skills.count() == 0


async def test_uma_acao_so_nao_e_procedimento(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    for _ in range(3):
        experiencia.registrar("calcular o orçamento", acoes=[acao("calcular")])

    relatorio = await experiencia.consolidar(aplicar=True)
    assert relatorio["candidatas"] == []


async def test_autoaprovacao_exige_autorizacao_explicita(vetores, monkeypatch):
    monkeypatch.setattr(config, "LEARNING_MIN_EXPERIENCES", 3)
    monkeypatch.setattr(config, "SKILL_AUTO_APPROVE", True)
    for _ in range(5):
        experiencia.registrar(
            "publicar o site com docker", acoes=[acao("ler_arquivo"), acao("escrever_arquivo")]
        )

    await experiencia.consolidar(aplicar=True)
    assert skills.count() == 1, "com autorização no .env, e só com 5+ acertos limpos"


# ── estatísticas ──────────────────────────────────────────────────────────


def test_estatisticas_separam_sucesso_de_falha_de_duvida():
    experiencia.registrar("a", acoes=[acao("calcular")])
    experiencia.registrar("b", acoes=[acao("calcular", ok=False)])
    numeros = experiencia.estatisticas()
    assert numeros["sucessos"] == 1
    assert numeros["falhas"] == 1
