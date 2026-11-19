"""O sono da memória: revisa de madrugada, só aplica com o André."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from livia import config, memoria, sono
from livia.store import COLECOES


@pytest.fixture(autouse=True)
def _dados(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)


def _rel(dup=0, obs=0):
    return {"duplicatas": [{"mantida": f"a{i}", "parecida": f"b{i}", "semelhanca": .97} for i in range(dup)],
            "obsoletas": [{"nome": f"o{i}", "dias_sem_uso": 200} for i in range(obs)],
            "conflitos": [], "licoes": {"heuristicas": [], "anti_patterns": [], "candidatas": []},
            "arquivadas": 0, "fundidas": 0}


async def test_revisar_so_olha_e_deixa_pendente(monkeypatch):
    chamadas = []

    async def manut(**kw):
        chamadas.append(kw)
        return _rel(dup=2, obs=1)
    monkeypatch.setattr(memoria, "manutencao", manut)
    dados = await sono.revisar()
    assert chamadas == [{"aplicar": False}]                 # de madrugada NÃO aplica
    assert dados["contagem"] == {"repetidas": 2, "esquecidas": 1, "licoes": 0}
    assert sono.pendente()["situacao"] == "pendente"
    assert "2 par(es)" in sono.texto(dados)


async def test_nada_pra_fazer_nao_incomoda(monkeypatch):
    async def manut(**kw):
        return _rel()
    monkeypatch.setattr(memoria, "manutencao", manut)
    await sono.revisar()
    assert sono.pendente() is None


async def test_aplicar_funde_e_dispensar_some(monkeypatch):
    chamadas = []

    async def manut(**kw):
        chamadas.append(kw)
        return {**_rel(dup=1), "fundidas": 1}
    monkeypatch.setattr(memoria, "manutencao", manut)
    await sono.revisar()
    r = await sono.aplicar()
    assert chamadas[-1] == {"aplicar": True, "fundir_textos": True}
    assert r["resultado"]["fundidas"] == 1 and sono.pendente() is None
    await sono.revisar()
    sono.dispensar()
    assert sono.pendente() is None


def test_proxima_rodada_e_de_madrugada_no_fuso_do_brasil(monkeypatch):
    monkeypatch.setattr(config, "SONO_HORA", "03:30")
    br = timezone(timedelta(hours=-3))
    assert sono.proxima_rodada(datetime(2026, 11, 19, 1, 0, tzinfo=br)) == datetime(2026, 11, 19, 3, 30, tzinfo=br)
    assert sono.proxima_rodada(datetime(2026, 11, 19, 7, 0, tzinfo=br)) == datetime(2026, 11, 20, 3, 30, tzinfo=br)


async def test_fundir_junta_o_detalhe_das_duas_e_falha_sem_quebrar(monkeypatch, tmp_path):
    from livia import brain

    store = COLECOES["memories"]
    monkeypatch.setattr(store, "directory", tmp_path / "mem")
    a = store.save("banco-crm", "O CRM usa Postgres.", kind="fact")
    b = store.save("banco-do-crm", "O CRM roda em Postgres na Neon.", kind="fact")

    async def junta(*_a, **_k):
        return {"descricao": "O CRM usa Postgres 16, hospedado na Neon.", "corpo": ""}
    monkeypatch.setattr(brain, "structured", junta)
    assert await memoria.fundir(a, b)
    nova = store.get("banco-do-crm")
    assert "Postgres 16" in nova.description and nova.meta.get("merged_from") == "banco-crm"

    async def quebra(*_a, **_k):
        raise RuntimeError("sem modelo")
    monkeypatch.setattr(brain, "structured", quebra)
    assert await memoria.fundir(a, nova) is False          # sem modelo, faxina segue sem fusão
