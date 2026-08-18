"""Fase 9 — ela consultar a própria cabeça, e segredo não entrar nela.

O prompt já dizia "peça o conteúdo completo de um item se precisar", mas não
existia como pedir. Metade destes testes cobre esse buraco; a outra metade
cobre as três portas por onde uma chave de API poderia virar memória.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from livia import config, consulta, ferramentas, learner, segredos, server
from livia.ferramentas import FerramentaError
from livia.store import memory, skills


@pytest.fixture(autouse=True)
def gavetas(tmp_path, monkeypatch):
    """Memórias e skills numa pasta limpa por teste.

    Os Store são criados no import com o caminho de então, então é o
    `directory` de cada um que precisa ser desviado — trocar config.DATA_DIR
    aqui não teria efeito nenhum.
    """
    mem = tmp_path / "memory"
    ski = tmp_path / "skills"
    mem.mkdir()
    ski.mkdir()
    monkeypatch.setattr(memory, "directory", mem)
    monkeypatch.setattr(skills, "directory", ski)
    return mem, ski


@pytest.fixture
def povoado():
    memory.save("prefere-postgres", "Prefere Postgres a MySQL em projetos novos.",
                "Já conhece a administração e não quer aprender outra.", kind="preferencia")
    memory.save("usa-windows", "Trabalha no Windows 10 com PowerShell.", kind="referencia")
    memory.save("projeto-livia", "Está construindo a Livia, assistente pessoal em Python.",
                "Custo zero é a restrição que governa tudo.", kind="projeto")
    skills.save("fazer-deploy", "Como o André publica no Render.",
                "1. git push\n2. o Render reconstrói sozinho\n3. conferir /saude", kind="manual")
    skills.save("formato-de-commit", "Formato de mensagem de commit que ele usa.",
                "Primeira linha imperativa, corpo explicando o porquê.", kind="manual")


# --------------------------------------------------------------------------
# Buscar
# --------------------------------------------------------------------------


def test_acha_memoria_pela_palavra_do_meio_da_descricao(povoado):
    texto = consulta.buscar_memorias("postgres")
    assert "prefere-postgres" in texto
    assert "não quer aprender outra" in texto, "o corpo deveria vir também"


def test_acento_nao_impede_o_encontro(povoado):
    """'memória' e 'memoria' têm que se encontrar."""
    memory.save("area-de-trabalho", "Organiza a área de trabalho por projeto.")
    assert "area-de-trabalho" in consulta.buscar_memorias("área")
    assert "area-de-trabalho" in consulta.buscar_memorias("AREA")


def test_o_nome_pesa_mais_que_o_corpo(povoado):
    """Quem escolheu o nome escolheu pensando no assunto."""
    memory.save("nota-solta", "Assunto sem relação.", "Menciona postgres de passagem.")
    texto = consulta.buscar_memorias("postgres")

    assert texto.index("prefere-postgres") < texto.index("nota-solta")


def test_frase_inteira_ganha_de_palavras_soltas(povoado):
    memory.save("deploy-no-render", "Faz deploy no Render pelo git push.")
    memory.save("deploy-generico", "Fala sobre deploy.")
    memory.save("render-generico", "Fala sobre Render.")

    texto = consulta.buscar_memorias("deploy no render")
    assert texto.index("deploy-no-render") < texto.index("deploy-generico")


def test_busca_sem_resultado_lista_o_que_existe(povoado):
    """Dizer só 'não achei' faria ela concluir que a gaveta está vazia."""
    texto = consulta.buscar_memorias("astronomia babilônica")

    assert "Nenhuma memória" in texto
    assert "prefere-postgres" in texto, "deveria mostrar o índice"


def test_gaveta_vazia_diz_que_esta_vazia():
    assert "nenhuma memória" in consulta.buscar_memorias("qualquer").lower()
    assert "nenhuma skill" in consulta.buscar_skills("qualquer").lower()


def test_pergunta_so_de_palavras_comuns_nao_devolve_vazio(povoado):
    """Se tirar as palavras vazias sobrar nada, buscar as originais é melhor
    que responder como se a coleção estivesse vazia."""
    texto = consulta.buscar_memorias("o que")
    assert texto.strip()


def test_termo_em_branco_e_recusado(povoado):
    with pytest.raises(FerramentaError):
        consulta.buscar_memorias("   ")


def test_quantas_limita_o_resultado(povoado):
    for i in range(10):
        memory.save(f"nota-{i}", "Assunto sobre deploy e servidores.")
    texto = consulta.buscar_memorias("deploy", quantas=2)
    assert texto.startswith("2 de ")


def test_quantas_tem_teto_proprio(povoado):
    for i in range(30):
        memory.save(f"nota-{i}", "Assunto sobre deploy.")
    texto = consulta.buscar_memorias("deploy", quantas=999)
    assert texto.startswith(f"{consulta.LIMITE_RESULTADOS} de ")


def test_skills_sao_uma_gaveta_separada(povoado):
    """Buscar skill não pode devolver memória, nem o contrário."""
    assert "fazer-deploy" in consulta.buscar_skills("deploy")
    assert "prefere-postgres" not in consulta.buscar_skills("deploy")
    assert "fazer-deploy" not in consulta.buscar_memorias("deploy")


# --------------------------------------------------------------------------
# Ler
# --------------------------------------------------------------------------


def test_le_a_skill_inteira(povoado):
    texto = consulta.ler_skill("fazer-deploy")
    assert "git push" in texto and "conferir /saude" in texto


def test_nome_errado_sugere_o_parecido(povoado):
    """Ela chuta 'deploy' e o arquivo é 'fazer-deploy'."""
    with pytest.raises(FerramentaError) as erro:
        consulta.ler_skill("deploy")
    assert "fazer-deploy" in str(erro.value)


def test_nome_inexistente_sem_parecido_apenas_recusa(povoado):
    with pytest.raises(FerramentaError) as erro:
        consulta.ler_memoria("astronomia")
    assert "astronomia" in str(erro.value)


def test_nome_em_branco_e_recusado():
    with pytest.raises(FerramentaError):
        consulta.ler_memoria("")


def test_corpo_gigante_e_cortado(monkeypatch):
    monkeypatch.setattr(consulta, "LIMITE_CORPO", 100)
    memory.save("longa", "Uma memória longa.", "x" * 500)

    texto = consulta.ler_memoria("longa")
    assert "cortado" in texto and "500" in texto


# --------------------------------------------------------------------------
# Integração com a cadeia de ferramentas
# --------------------------------------------------------------------------


def test_o_modelo_enxerga_as_quatro_ferramentas():
    nomes = [f["name"] for f in ferramentas.catalogo()]
    for nome in ("buscar_memorias", "ler_memoria", "buscar_skills", "ler_skill"):
        assert nome in nomes
    assert "criar_documento" in nomes and "ler_arquivo" in nomes, "as antigas continuam"


def test_executar_despacha_para_consulta(povoado):
    texto, ok = ferramentas.executar("buscar_memorias", {"termo": "postgres"})
    assert ok and "prefere-postgres" in texto


def test_erro_de_consulta_volta_como_texto(povoado):
    texto, ok = ferramentas.executar("ler_skill", {"nome": "deploy"})
    assert not ok and "fazer-deploy" in texto


def test_resumir_descreve_as_novas():
    assert "memórias" in ferramentas.resumir("buscar_memorias", {"termo": "x"})
    assert "skill" in ferramentas.resumir("ler_skill", {"nome": "y"})
    # As antigas não podem ter sido atropeladas pelo novo despacho.
    assert "calculando" in ferramentas.resumir("calcular", {"expressao": "2+2"})
    assert "documento" in ferramentas.resumir("criar_documento", {"caminho": "a.md"})


def test_o_prompt_conta_que_as_ferramentas_existem():
    from livia import context

    prompt = context.build_system_prompt()
    assert "buscar_memorias" in prompt
    assert "ler_skill" in prompt


# --------------------------------------------------------------------------
# Segredo não vira memória
# --------------------------------------------------------------------------


# Todas as credenciais deste arquivo são SINTÉTICAS. Elas precisam manter o
# formato que `segredos.py` reconhece — senão o teste vira decoração — mas não
# são chave de ninguém.
#
# Eu tinha colocado aqui as chaves reais que o André colou no chat. A proteção
# de push do GitHub barrou, e estava certa: seria chave de verdade permanente
# no histórico do repositório, que é o dano exato que esta fase existe para
# impedir. Corrigido antes de sair da máquina.
@pytest.mark.parametrize(
    "texto",
    [
        "A chave da Groq é gsk_EXEMPLOfalsoNAOuseIsto0000000000000000000000000000",
        "usa sk-or-v1-EXEMPLOfalso0000000000000000000000000000000000000000000000",
        "AIzaEXEMPLOfalsoNAOuseIsto000000000000 é a chave",
        "sk-ant-api03-EXEMPLOfalsoNAOuseIsto000000",
        "token ghp_EXEMPLOfalsoNAOuseIsto000000000000",
        "xoxb-EXEMPLO-falso-NAOuseIsto",
        "AKIAIOSFODNN7EXAMPLE nas credenciais",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
        "-----BEGIN RSA PRIVATE KEY-----",
        "postgres://usuario:senhaSecreta123@localhost:5432/banco",
        "a senha do banco é boi123456",
        "API_KEY=abcdef123456",
        "client_secret: xyz987654321",
    ],
)
def test_reconhece_o_que_e_segredo(texto):
    assert segredos.encontrar(texto), f"passou batido: {texto[:40]}"


@pytest.mark.parametrize(
    "texto",
    [
        "Prefere Postgres a MySQL em projetos novos.",
        "O commit 3a384ad9f2b1c4d5e6f708192a3b4c5d6e7f8091 fechou a fase 5.",
        "Trabalha no Windows 10 com PowerShell 7.",
        "O projeto fica em C:/Users/anascimento/LIVIA e roda na porta 8100.",
        "Usa a chave do Gemini guardada no .env, nunca no código.",
        "O identificador do documento é 550e8400-e29b-41d4-a716-446655440000.",
    ],
)
def test_nao_confunde_coisa_normal_com_segredo(texto):
    """Falso positivo aqui recusa memória boa. O hash de commit e o UUID são
    justamente onde um detector por entropia erraria."""
    assert segredos.encontrar(texto) == [], f"falso positivo: {texto[:40]}"


def test_a_recusa_nunca_repete_o_valor():
    chave = "gsk_EXEMPLOfalsoNAOuseIsto0000000000000000000000000000"
    aviso = segredos.motivo(segredos.encontrar(f"a chave é {chave}"))

    assert chave not in aviso, "a mensagem de recusa vazou a chave"
    assert "Groq" in aviso and ".env" in aviso


def test_confere_todos_os_campos():
    achados = segredos.conferir("nome-limpo", "descrição limpa", "no corpo: gsk_" + "a" * 30)
    assert achados, "o corpo também tem que ser conferido"


@pytest.mark.asyncio
async def test_learner_recusa_memoria_com_chave(monkeypatch, capsys):
    """O prompt pede para não guardar segredo. Pedir não é garantir — aqui o
    modelo desobedece de propósito."""
    monkeypatch.setattr(config, "AUTO_LEARN", True)

    async def modelo_desobediente(*a, **k):
        return {
            "memories": [
                {"name": "chave-da-groq",
                 "description": "A chave da Groq dele é gsk_" + "a" * 40,
                 "kind": "referencia"},
                {"name": "prefere-python",
                 "description": "Prefere Python a JavaScript em projetos novos.",
                 "kind": "preferencia"},
            ]
        }

    monkeypatch.setattr("livia.brain.structured", modelo_desobediente)

    salvas = await learner.extract("aqui está minha chave", "anotado")
    nomes = [s["name"] for s in salvas]

    assert "prefere-python" in nomes, "a memória boa deveria passar"
    assert not any("chave" in n for n in nomes), "a chave foi guardada"
    assert len(memory.all()) == 1

    assert "memória recusada" in capsys.readouterr().out, "silenciar esconderia o caso"


# --------------------------------------------------------------------------
# As outras duas portas
# --------------------------------------------------------------------------


@pytest.fixture
def cliente():
    with TestClient(server.app, base_url="https://testserver") as c:
        assert c.post("/api/entrar", json={"senha": config.PASSWORD}).status_code == 200
        yield c


def test_o_painel_recusa_memoria_com_chave(cliente):
    r = cliente.post("/api/store/memories", json={
        "name": "credenciais",
        "description": "chave da Groq: gsk_" + "b" * 40,
    })

    assert r.status_code == 400
    assert ".env" in r.json()["error"]
    assert memory.all() == []


def test_o_painel_aceita_memoria_normal(cliente):
    r = cliente.post("/api/store/memories", json={
        "name": "prefere-postgres",
        "description": "Prefere Postgres a MySQL.",
    })
    assert r.status_code == 200
    assert len(memory.all()) == 1


def test_o_painel_recusa_skill_com_chave(cliente):
    """A trava vale para as duas gavetas: skill também entra no prompt."""
    r = cliente.post("/api/store/skills", json={
        "name": "chamar-api",
        "description": "Como chamar a API.",
        "body": "use o header Authorization: Bearer sk-ant-api03-" + "c" * 30,
    })
    assert r.status_code == 400
    assert skills.all() == []


def test_o_comando_lembrar_recusa_chave():
    from livia.server import _handle_command

    resposta = _handle_command("/lembrar minha chave é gsk_" + "d" * 40)

    assert resposta and ".env" in resposta
    assert memory.all() == [], "gravou mesmo assim"


def test_o_comando_lembrar_continua_funcionando():
    from livia.server import _handle_command

    resposta = _handle_command("/lembrar prefiro Postgres a MySQL")

    assert resposta and "Gravado" in resposta
    assert len(memory.all()) == 1


@pytest.mark.parametrize(
    "texto",
    [
        "A Groq dá mais tokens grátis por dia que o Gemini.",
        "Prefere guardar senha e login no gerenciador, nunca em arquivo.",
        "O token de acesso expira em uma hora e é renovado sozinho.",
        "Perguntou qual é o limite de token por minuto da Groq.",
        "A credencial fica no cofre da empresa.",
    ],
)
def test_o_vao_da_regra_nao_virou_peneira(texto):
    """A regra de declaração aceita até três palavras entre a palavra-chave e o
    separador. Isso não pode transformar conversa sobre cota em falso positivo —
    memória boa recusada é prejuízo silencioso."""
    assert segredos.encontrar(texto) == [], f"falso positivo: {texto}"


# --------------------------------------------------------------------------
# Radical: o português conjuga tudo
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "termo, palavra",
    [
        ("custa", "custo"),          # medido: falhava antes do radical
        ("publicar", "publica"),     # medido: falhava antes do radical
        ("memorias", "memoria"),
        ("configuracao", "config"),  # prefixo nos dois sentidos
        ("config", "configuracao"),
        ("commits", "commit"),
        ("rapido", "rapida"),
        ("versionar", "versionado"),
    ],
)
def test_flexao_nao_impede_o_encontro(termo, palavra):
    assert consulta._casam(termo, palavra), f"{termo} deveria casar com {palavra}"


@pytest.mark.parametrize(
    "termo, palavra",
    [("gato", "gado"), ("deploy", "postgres"), ("commit", "custo"), ("windows", "windsurf")],
)
def test_o_radical_nao_gruda_o_que_nao_tem_relacao(termo, palavra):
    """Tesoura curta demais junta palavra diferente e a busca vira ruído."""
    assert not consulta._casam(termo, palavra), f"{termo} não deveria casar com {palavra}"


def test_o_radical_nunca_desce_de_quatro_letras():
    for palavra in ("casa", "ir", "e", "sao", "voces", "acoes"):
        assert len(consulta._radical(palavra)) >= min(len(palavra), 4)


def test_pergunta_conjugada_encontra_a_memoria(povoado):
    """Os dois casos que eu media falhando antes de escrever o radical."""
    memory.save("custo-zero", "Custo zero é a restrição que governa o projeto.")
    assert "custo-zero" in consulta.buscar_memorias("quanto isso custa")

    skills.save("publicar-no-render", "Como ele publica no Render.")
    assert "publicar-no-render" in consulta.buscar_skills("como publicar")
