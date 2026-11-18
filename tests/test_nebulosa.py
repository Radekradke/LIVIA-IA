"""Nebulosa: temperatura, funil, gravidade e a Livia como jardineira.

Os testes que mais importam aqui são os que travam a regra de convivência:
a Livia sugere e o André decide. Proposta não vira ideia sozinha, sugestão
não executa sozinha, gate não se preenche sozinho.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from livia import config, db, nebulosa
from livia.server import app
from tests.test_memoria import vetores  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _ceu_vazio(tmp_path, monkeypatch):
    with db._connect() as conn:
        for tabela in ("ideia_sugestoes", "ideia_ligacoes", "ideia_gates", "ideia_notas",
                       "ideia_eventos", "ideia_layouts", "constelacao_nomes", "ideias", "sistemas"):
            conn.execute(f"DELETE FROM {tabela}")
    from livia.store import lessons

    monkeypatch.setattr(lessons, "directory", tmp_path / "lessons")
    (tmp_path / "lessons").mkdir()
    monkeypatch.setattr(config, "NEBULOSA_ENABLED", True)
    monkeypatch.setattr(config, "NEBULOSA_MIN_PADRAO", 3)
    nebulosa._ULTIMA_RODADA.clear()
    yield


def _envelhecer(id_: int, horas: float) -> None:
    """Empurra o último retrato de temperatura para o passado."""
    with db._connect() as conn:
        linha = conn.execute("SELECT calor_em, mexida_em FROM ideias WHERE id = ?", (id_,)).fetchone()
        antes = datetime.fromisoformat(linha["calor_em"]) - timedelta(hours=horas)
        conn.execute(
            "UPDATE ideias SET calor_em = ?, mexida_em = ?, estagio_em = ? WHERE id = ?",
            (antes.isoformat(), antes.isoformat(), antes.isoformat(), id_),
        )


def _preencher(id_: int, destino: str) -> None:
    for g in nebulosa.faltando(nebulosa.obter(id_), destino):
        nebulosa.responder_gate(id_, g.chave, "4" if g.tipo == "nota" else f"resposta {g.chave}")


# ── temperatura ───────────────────────────────────────────────────────────


def test_ideia_nasce_quente_e_esfria_pela_meia_vida():
    i = nebulosa.criar("Jogo de caravanas com deusa de sal")
    assert i["calor"] == nebulosa.PESOS["criar"]
    _envelhecer(i["id"], nebulosa.POR_CHAVE["faisca"].meia_vida_h)
    assert nebulosa.publico(nebulosa.obter(i["id"]))["calor"] == pytest.approx(10.0, abs=0.2)


def test_estagio_mais_avancado_esfria_mais_devagar():
    a = nebulosa.criar("ideia a")
    b = nebulosa.criar("ideia b")
    _preencher(b["id"], "validada")
    nebulosa.mudar_estagio(b["id"], "validada")
    for i in (a, b):
        with db._connect() as conn:
            conn.execute("UPDATE ideias SET calor = 50 WHERE id = ?", (i["id"],))
        _envelhecer(i["id"], 24 * 7)
    fa = nebulosa.publico(nebulosa.obter(a["id"]))["calor"]
    fb = nebulosa.publico(nebulosa.obter(b["id"]))["calor"]
    assert fb > fa


def test_arrastar_nao_aquece_mas_editar_aquece():
    i = nebulosa.criar("ideia")
    _envelhecer(i["id"], 48)
    frio = nebulosa.publico(nebulosa.obter(i["id"]))["calor"]
    assert nebulosa.editar(i["id"], x=300, y=200)["calor"] == pytest.approx(frio, abs=0.1)
    assert nebulosa.editar(i["id"], conteudo="ideia com mais texto")["calor"] > frio + 4


def test_abrir_aquece_no_maximo_uma_vez_por_hora():
    i = nebulosa.criar("ideia")
    primeiro = nebulosa.abrir(i["id"])["calor"]
    segundo = nebulosa.abrir(i["id"])["calor"]
    assert primeiro == pytest.approx(segundo, abs=0.1)


def test_calor_tem_teto():
    i = nebulosa.criar("ideia")
    for n in range(40):
        nebulosa.editar(i["id"], conteudo=f"versão {n}")
    assert nebulosa.publico(nebulosa.obter(i["id"]))["calor"] <= nebulosa.CALOR_MAX


# ── funil e gates ─────────────────────────────────────────────────────────


def test_subir_sem_gate_e_recusado_com_a_lista_do_que_falta():
    i = nebulosa.criar("ideia")
    with pytest.raises(nebulosa.GateFaltando) as erro:
        nebulosa.mudar_estagio(i["id"], "rascunho")
    assert [g.chave for g in erro.value.pendentes] == ["problema"]


def test_pular_estagio_exige_os_gates_do_caminho_inteiro():
    i = nebulosa.criar("ideia")
    nebulosa.responder_gate(i["id"], "problema", "resolve X")
    with pytest.raises(nebulosa.GateFaltando) as erro:
        nebulosa.mudar_estagio(i["id"], "validada")
    assert "mvp" in {g.chave for g in erro.value.pendentes}
    _preencher(i["id"], "validada")
    assert nebulosa.mudar_estagio(i["id"], "validada")["estagio"] == "validada"


def test_descer_e_arquivar_nao_pedem_gate():
    i = nebulosa.criar("ideia")
    _preencher(i["id"], "rascunho")
    nebulosa.mudar_estagio(i["id"], "rascunho")
    assert nebulosa.mudar_estagio(i["id"], "faisca")["estagio"] == "faisca"
    arq = nebulosa.mudar_estagio(i["id"], "arquivada", motivo="sem tempo")
    assert arq["estagio"] == "arquivada" and arq["motivo_arquivo"] == "sem tempo"


def test_reviver_devolve_ao_estagio_de_antes_e_aquece():
    i = nebulosa.criar("ideia")
    _preencher(i["id"], "rascunho")
    nebulosa.mudar_estagio(i["id"], "rascunho")
    nebulosa.mudar_estagio(i["id"], "arquivada")
    _envelhecer(i["id"], 24 * 30)
    viva = nebulosa.reviver(i["id"])
    assert viva["estagio"] == "rascunho"
    assert viva["calor"] >= nebulosa.PESOS["reviver"]


def test_nota_de_gate_aceita_so_1_a_5():
    i = nebulosa.criar("ideia")
    assert nebulosa.responder_gate(i["id"], "esforco", "uns 3, acho")["gates"]["esforco"] == "3"
    with pytest.raises(nebulosa.NebulosaError):
        nebulosa.responder_gate(i["id"], "esforco", "muito")
    with pytest.raises(nebulosa.NebulosaError):
        nebulosa.responder_gate(i["id"], "gate-inventado", "x")


def test_juntar_arquiva_a_absorvida_sem_apagar_nada():
    a = nebulosa.criar("App de estêncil", titulo="Ghostline")
    b = nebulosa.criar("Converter foto em estêncil de tatuagem")
    nebulosa.responder_gate(b["id"], "problema", "decalque manual demora")
    fica = nebulosa.juntar(a["id"], b["id"])
    assert "decalque" not in fica["conteudo"]  # o gate não vira texto...
    # ...e o texto da absorvida vem junto, como nota (uma lua nova)
    assert any("Converter foto" in n["texto"] for n in nebulosa.notas(a["id"]))
    assert fica["gates"]["problema"] == "decalque manual demora"
    sai = nebulosa.obter(b["id"])
    assert sai["estagio"] == "arquivada" and sai["motivo_arquivo"].startswith("juntada")


# ── a regra de convivência ────────────────────────────────────────────────


def test_proposta_da_livia_nao_conta_ate_ser_adotada():
    p = nebulosa.criar("pergunta-ponte", origem="livia:ponte", proposta=True)
    assert nebulosa.resumo()["total"] == 0
    assert nebulosa.resumo()["propostas"] == 1
    nebulosa.adotar(p["id"])
    assert nebulosa.resumo()["total"] == 1


def test_sugestao_nao_executa_sozinha_e_recusa_silencia():
    i = nebulosa.criar("ideia velha")
    s = nebulosa.sugerir("arquivar", i["id"], motivo="apagou")
    assert nebulosa.obter(i["id"])["estagio"] == "faisca"
    nebulosa.decidir_sugestao(s["id"], aceitar=False)
    assert nebulosa.sugerir("arquivar", i["id"], motivo="apagou de novo") is None


def test_aceitar_sugestao_executa_a_acao():
    i = nebulosa.criar("ideia velha")
    s = nebulosa.sugerir("arquivar", i["id"], motivo="apagou")
    nebulosa.decidir_sugestao(s["id"], aceitar=True)
    assert nebulosa.obter(i["id"])["estagio"] == "arquivada"


def test_aceitar_promover_com_gate_faltando_mantem_a_sugestao_pendente():
    i = nebulosa.criar("ideia quente")
    s = nebulosa.sugerir("promover", i["id"], motivo="em brasa")
    with pytest.raises(nebulosa.GateFaltando):
        nebulosa.decidir_sugestao(s["id"], aceitar=True)
    assert any(x["id"] == s["id"] for x in nebulosa.sugestoes())


async def test_regras_sugerem_promover_quente_e_arquivar_apagada():
    quente = nebulosa.criar("ideia quente")
    with db._connect() as conn:
        conn.execute("UPDATE ideias SET calor = 90 WHERE id = ?", (quente["id"],))
    with db._connect() as conn:
        antes = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        conn.execute("UPDATE ideias SET estagio_em = ? WHERE id = ?", (antes, quente["id"]))
    fria = nebulosa.criar("ideia fria")
    _envelhecer(fria["id"], 24 * 40)
    tipos = {(s["tipo"], s["ideia_id"]) for s in await nebulosa.gerar_sugestoes(forcar=True)}
    assert ("promover", quente["id"]) in tipos
    assert ("arquivar", fria["id"]) in tipos


# ── semântica: gravidade, busca, menção, pesca ────────────────────────────


async def test_gravidade_junta_ideias_do_mesmo_assunto(vetores):
    a = nebulosa.criar("migrar o banco para postgres")
    b = nebulosa.criar("backup do banco de dados sqlite")
    c = nebulosa.criar("tema escuro para a interface")
    d = nebulosa.criar("cor e visual da ui")
    g = await nebulosa.gravidade(0.3)
    grupos = [set(c_["membros"]) for c_ in g["constelacoes"]]
    assert {a["id"], b["id"]} in grupos
    assert {c["id"], d["id"]} in grupos


async def test_ligacao_manual_vira_mola_mesmo_sem_semelhanca(vetores):
    a = nebulosa.criar("migrar o banco para postgres")
    b = nebulosa.criar("tema escuro para a interface")
    nebulosa.ligar(a["id"], b["id"])
    g = await nebulosa.gravidade(0.5)
    assert any({e["a"], e["b"]} == {a["id"], b["id"]} for e in g["arestas"])


async def test_falar_da_ideia_no_chat_aquece(vetores):
    i = nebulosa.criar("migrar o banco para postgres")
    _envelhecer(i["id"], 24 * 6)
    antes = nebulosa.publico(nebulosa.obter(i["id"]))["calor"]
    aquecidas = await nebulosa.aquecer_por_mencao("e aquela do banco postgres?", conversa=7)
    assert [x["id"] for x in aquecidas] == [i["id"]]
    assert nebulosa.publico(nebulosa.obter(i["id"]))["calor"] > antes
    # Na mesma conversa, insistir não esquenta de novo.
    assert await nebulosa.aquecer_por_mencao("banco postgres de novo", conversa=7) == []


async def test_mencao_nao_aquece_ideia_sem_relacao(vetores):
    nebulosa.criar("migrar o banco para postgres")
    assert await nebulosa.aquecer_por_mencao("qual a melhor impressora epson?") == []


async def test_pesca_so_com_marcador_explicito(vetores):
    assert await nebulosa.pescar("o deploy de ontem deu certo") is None
    p = await nebulosa.pescar("tive uma ideia: publicar a livia num servidor docker com backup")
    assert p and p["proposta"] and p["origem"] == "livia:conversa"


async def test_pesca_nao_duplica_ideia_existente(vetores):
    nebulosa.criar("publicar o servidor com docker")
    assert await nebulosa.pescar("tive uma ideia: publicar o servidor com docker") is None


async def test_contexto_traz_as_ideias_da_pergunta(vetores):
    i = nebulosa.criar("migrar o banco para postgres", titulo="Migração Postgres")
    texto, ideias = await nebulosa.contexto("como faço a migração do banco?", "André")
    assert "# A Nebulosa" in texto and "Migração Postgres" in texto
    assert ideias and ideias[0]["id"] == i["id"]


# ── padrões ───────────────────────────────────────────────────────────────


def test_padrao_so_aparece_com_evidencia_minima():
    for n in range(2):
        nebulosa.mudar_estagio(nebulosa.criar(f"ideia {n}")["id"], "arquivada")
    assert not [p for p in nebulosa.padroes() if p["chave"].startswith("funil-faisca")]


def test_padrao_de_conversao_e_vira_licao_com_aprovacao():
    for n in range(4):
        i = nebulosa.criar(f"ideia {n}")
        if n == 0:
            _preencher(i["id"], "rascunho")
            nebulosa.mudar_estagio(i["id"], "rascunho")
        else:
            nebulosa.mudar_estagio(i["id"], "arquivada", motivo="sem tempo")
    lista = {p["chave"]: p for p in nebulosa.padroes()}
    assert "funil-faisca-rascunho" in lista
    assert "3 de" not in lista["funil-faisca-rascunho"]["descricao"]  # 1 de 4
    assert "motivo-arquivo" in lista
    licao = nebulosa.virar_licao("funil-faisca-rascunho")
    assert licao["name"] == "nebulosa-funil-faisca-rascunho"
    assert {p["chave"]: p for p in nebulosa.padroes()}["funil-faisca-rascunho"]["ja_licao"]


# ── layout ────────────────────────────────────────────────────────────────


def test_desfazer_volta_o_ceu_de_antes_da_gravidade():
    i = nebulosa.criar("ideia", x=10, y=20)
    nebulosa.guardar_layout()
    nebulosa.mover_varias([{"id": i["id"], "x": 500, "y": 600}])
    nebulosa.desfazer_layout()
    linha = nebulosa.obter(i["id"])
    assert (linha["pos_x"], linha["pos_y"]) == (10, 20)


# ── ferramentas e comandos ────────────────────────────────────────────────


async def test_ferramenta_de_estagio_devolve_o_que_falta_ao_modelo():
    from livia import ferramentas

    i = nebulosa.criar("ideia")
    texto, ok = await ferramentas.executar_async("mudar_estagio", {"id": i["id"], "estagio": "rascunho"})
    assert not ok and "problema" in texto
    texto, ok = await ferramentas.executar_async(
        "preencher_gate", {"id": i["id"], "gate": "problema", "valor": "demora"}
    )
    assert ok and nebulosa.respostas(i["id"])["problema"] == "demora"
    # Gravado com a origem da conversa: dá para saber depois quem escreveu.
    with db._connect() as conn:
        origem = conn.execute("SELECT origem FROM ideia_gates WHERE ideia_id = ?", (i["id"],)).fetchone()
    assert origem["origem"] == "livia:conversa"


def test_catalogo_inclui_a_nebulosa():
    from livia import ferramentas

    nomes = {f["name"] for f in ferramentas.catalogo()}
    assert {"criar_ideia", "preencher_gate", "mudar_estagio"} <= nomes


async def test_comandos_de_chat():
    criada = await nebulosa.comando("/ideia", "app de estêncil para tatuagem")
    assert "#" in criada
    assert "app de estêncil" in await nebulosa.comando("/ideias", "")
    promover = await nebulosa.comando("/promover", criada.split("#")[1].split("*")[0])
    assert "problema" in promover


# ── rotas ─────────────────────────────────────────────────────────────────


@pytest.fixture
def logado():
    with TestClient(app, base_url="https://testserver") as c:
        assert c.post("/api/entrar", json={"senha": config.PASSWORD}).status_code == 200
        yield c


def test_rotas_exigem_sessao():
    with TestClient(app, base_url="https://testserver") as c:
        assert c.get("/api/nebulosa").status_code == 401


def test_rota_de_estagio_devolve_409_com_as_perguntas(logado):
    i = logado.post("/api/nebulosa/ideias", json={"conteudo": "ideia nova"}).json()["ideia"]
    r = logado.post(f"/api/nebulosa/ideias/{i['id']}/estagio", json={"estagio": "rascunho"})
    assert r.status_code == 409
    assert r.json()["faltando"][0]["chave"] == "problema"
    r = logado.post(f"/api/nebulosa/ideias/{i['id']}/gates",
                    json={"respostas": {"problema": "x"}, "promover_para": "rascunho"})
    assert r.status_code == 200 and r.json()["ideia"]["estagio"] == "rascunho"


def test_estado_e_assets(logado):
    logado.post("/api/nebulosa/ideias", json={"conteudo": "uma ideia"})
    dados = logado.get("/api/nebulosa").json()
    assert len(dados["ideias"]) == 1 and dados["estagios"][0]["chave"] == "faisca"
    assert logado.get("/assets/nebulosa.js").status_code == 200
    assert logado.get("/assets/galaxia.js").status_code == 200
    assert logado.get("/assets/cosmos.js").status_code == 200
    assert logado.get("/assets/../.env").status_code == 404


# ── a conversa mexe no céu ────────────────────────────────────────────────


@pytest.fixture
def chat(monkeypatch):
    import respx

    from livia import auth

    monkeypatch.setattr(config, "PROVIDERS", ["groq"])
    monkeypatch.setattr(config, "GROQ_API_KEY", "x")
    monkeypatch.setattr(config, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(config, "OLLAMA_ENABLED", False)
    monkeypatch.setattr(config, "WEB_ENABLED", False)
    monkeypatch.setattr(config, "AUTO_LEARN", False)
    with respx.mock:
        from tests.test_contexto import GROQ, sse_groq
        import httpx

        respx.post(GROQ).mock(return_value=httpx.Response(200, text=sse_groq("certo")))
        yield TestClient(app), {auth.COOKIE: auth.criar_token()}


def _eventos(chat, mensagem):
    import json

    c, cookie = chat
    r = c.post("/api/chat", json={"message": mensagem}, cookies=cookie)
    return [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]


def test_falar_de_uma_ideia_no_chat_aquece_e_avisa_a_interface(chat, vetores):
    i = nebulosa.criar("migrar o banco para postgres")
    _envelhecer(i["id"], 24 * 5)
    eventos = _eventos(chat, "e aquela história do banco postgres?")
    neb = [e for e in eventos if e.get("type") == "nebulosa"]
    assert neb and neb[0]["aquecidas"][0]["id"] == i["id"]
    assert eventos[-1]["type"] == "done"


def test_contar_uma_ideia_no_chat_vira_proposta(chat, vetores):
    eventos = _eventos(chat, "tive uma ideia: publicar a livia num servidor docker")
    neb = [e for e in eventos if e.get("type") == "nebulosa"]
    assert neb and neb[0]["pescada"]["proposta"] is True
    assert nebulosa.resumo()["total"] == 0  # proposta não conta até adotar


def test_comando_ideia_pelo_chat(chat, vetores):
    eventos = _eventos(chat, "/ideia bot que avisa quando a tinta acaba")
    texto = "".join(e["text"] for e in eventos if e.get("type") == "delta")
    assert "Faísca" in texto and nebulosa.resumo()["total"] == 1


def test_prompt_conhece_a_nebulosa(vetores):
    from livia import context

    nebulosa.criar("migrar o banco para postgres", titulo="Migração Postgres")
    assert "# A Nebulosa" in context.build_system_prompt()


# ── sistemas solares e notas ──────────────────────────────────────────────


def test_sistema_agrupa_ideias_e_o_sol_brilha_com_as_mais_quentes():
    a = nebulosa.criar("ideia a", x=100, y=0)
    b = nebulosa.criar("ideia b", x=300, y=0)
    sist = nebulosa.criar_sistema("Jogos", ideias=[a["id"], b["id"]])
    assert set(sist["membros"]) == {a["id"], b["id"]}
    assert (sist["x"], sist["y"]) == (200, 0)          # nasce no meio delas
    assert nebulosa.publico(nebulosa.obter(a["id"]))["sistema_nome"] == "Jogos"
    assert sist["calor"] == pytest.approx(nebulosa.PESOS["criar"] + nebulosa.PESOS["sistema"], abs=0.5)


def test_tirar_do_sistema_e_apagar_sistema_nao_apaga_ideia():
    a = nebulosa.criar("ideia a")
    sist = nebulosa.criar_sistema("Lifting", ideias=[a["id"]])
    nebulosa.colocar_no_sistema(a["id"], None, x=10, y=20)
    linha = nebulosa.obter(a["id"])
    assert linha["sistema_id"] is None and (linha["pos_x"], linha["pos_y"]) == (10, 20)
    nebulosa.colocar_no_sistema(a["id"], "lifting")      # pelo nome, sem caixa
    assert nebulosa.apagar_sistema(sist["id"])
    assert nebulosa.obter(a["id"])["sistema_id"] is None
    assert nebulosa.obter(a["id"]) is not None


def test_nota_e_lua_aquece_e_entra_no_detalhe():
    i = nebulosa.criar("ideia")
    _envelhecer(i["id"], 48)
    frio = nebulosa.publico(nebulosa.obter(i["id"]))["calor"]
    nota = nebulosa.adicionar_nota(i["id"], "pensei numa versão mobile")
    detalhe = nebulosa.detalhar(i["id"])
    assert detalhe["n_notas"] == 1 and detalhe["notas"][0]["texto"] == "pensei numa versão mobile"
    assert detalhe["calor"] > frio
    nebulosa.editar_nota(nota["id"], "versão mobile primeiro")
    assert nebulosa.notas(i["id"])[0]["texto"] == "versão mobile primeiro"
    assert nebulosa.apagar_nota(nota["id"]) and nebulosa.notas(i["id"]) == []


def test_anotar_pela_livia_cria_nota_e_nao_mexe_no_texto():
    i = nebulosa.criar("texto original")
    nebulosa.anotar(i["id"], "um pensamento depois", origem="livia:conversa")
    assert nebulosa.obter(i["id"])["conteudo"] == "texto original"
    assert nebulosa.notas(i["id"])[0]["origem"] == "livia:conversa"


async def test_livia_sugere_sistema_para_constelacao_solta_e_so_cria_se_aceitar(vetores):
    ids = [nebulosa.criar(t)["id"] for t in (
        "migrar o banco para postgres", "backup do banco de dados sqlite", "banco de dados replica")]
    lista = await nebulosa.gerar_sugestoes(forcar=True)
    sug = [s for s in lista if s["tipo"] == "sistema"]
    assert sug and set(sug[0]["dados"]["membros"]) <= set(ids)
    assert nebulosa.sistemas() == []                     # nada criado sozinho
    nebulosa.decidir_sugestao(sug[0]["id"], aceitar=True)
    assert len(nebulosa.sistemas()) == 1


async def test_ferramentas_de_sistema():
    from livia import ferramentas

    a = nebulosa.criar("ideia a")
    texto, ok = await ferramentas.executar_async("criar_sistema", {"nome": "Livro", "ideias": [a["id"]]})
    assert ok and "Livro" in texto
    texto, ok = await ferramentas.executar_async("colocar_no_sistema", {"id": a["id"], "sistema": "nenhum"})
    assert ok and nebulosa.obter(a["id"])["sistema_id"] is None
    assert "Livro" in await nebulosa.comando("/sistemas", "")


def test_rotas_de_sistema_e_nota(logado):
    i = logado.post("/api/nebulosa/ideias", json={"conteudo": "ideia"}).json()["ideia"]
    s = logado.post("/api/nebulosa/sistemas", json={"nome": "Jogos", "ideias": [i["id"]]}).json()["sistema"]
    assert logado.get("/api/nebulosa").json()["sistemas"][0]["nome"] == "Jogos"
    n = logado.post(f"/api/nebulosa/ideias/{i['id']}/notas", json={"texto": "lua"}).json()["nota"]
    assert logado.get(f"/api/nebulosa/ideias/{i['id']}").json()["ideia"]["notas"][0]["id"] == n["id"]
    assert logado.post(f"/api/nebulosa/ideias/{i['id']}/sistema", json={"sistema": None, "x": 1, "y": 2}).status_code == 200
    assert logado.delete(f"/api/nebulosa/sistemas/{s['id']}").json()["ok"]
