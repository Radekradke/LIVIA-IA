"""Backup: exportar, restaurar e resistir a zip malicioso."""

from __future__ import annotations

import io
import zipfile

from livia import backup, config
from livia.store import memory, skills


def test_ciclo_completo(dados):
    (dados / "memory").mkdir()
    (dados / "skills").mkdir()
    memory.directory = dados / "memory"
    skills.directory = dados / "skills"

    memory.save("prefere-postgres", "Prefere Postgres a MySQL.")
    skills.save("commits", "Padrão de commit.", "Verbo no imperativo.")
    (dados / "personalidade.md").write_text("# Tom\nDireta.\n", encoding="utf-8")

    conteudo, nome = backup.exportar()
    assert conteudo[:2] == b"PK" and nome.endswith(".zip")

    dentro = zipfile.ZipFile(io.BytesIO(conteudo)).namelist()
    assert "personalidade.md" in dentro
    assert any(n.startswith("memory/") for n in dentro)
    assert any(n.startswith("skills/") for n in dentro)


def test_backup_nunca_leva_segredo(dados):
    (dados / "memory").mkdir()
    (dados / "skills").mkdir()
    (config.ROOT / ".env").exists()  # o .env real existe, mas não pode entrar

    conteudo, _ = backup.exportar()
    dentro = zipfile.ZipFile(io.BytesIO(conteudo)).namelist()
    assert not any(".env" in n for n in dentro)
    assert not any("GEMINI_API_KEY" in n for n in dentro)


def test_zip_com_traversal_e_ignorado(dados):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../invasao.md", "nao deveria escapar")
        z.writestr("/absoluto.md", "nem este")
        z.writestr("memory/legitima.md", "---\nname: legitima\ndescription: ok\n---\n")

    contagem = backup.importar(buf.getvalue())

    assert contagem["memorias"] == 1, "só a entrada legítima deveria passar"
    assert not (dados.parent / "invasao.md").exists()
    assert not (config.ROOT.parent / "invasao.md").exists()


def test_zip_com_nome_inesperado_e_ignorado(dados):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("qualquer/coisa.exe", "binario")
        z.writestr("memory/ok.md", "---\nname: ok\ndescription: x\n---\n")

    contagem = backup.importar(buf.getvalue())
    assert contagem["memorias"] == 1
    assert not (dados / "qualquer").exists()


# ── o que a versão nova acrescentou ──────────────────────────────────────


def test_licoes_entram_no_backup(dados, monkeypatch):
    """Lições são conclusões dela; perdê-las apaga o que ela aprendeu."""
    from livia import backup, config
    from livia.store import COLECOES

    pasta = dados / "lessons"
    pasta.mkdir()
    monkeypatch.setattr(COLECOES["lessons"], "directory", pasta)
    (pasta / "evitar-reset.md").write_text(
        "---\nname: evitar-reset\ndescription: Reset completo perde configuração.\n"
        "kind: lesson\n---\n\nEvitar.\n",
        encoding="utf-8",
    )

    zip_bytes, _ = backup.exportar()
    import io, zipfile

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "lessons/evitar-reset.md" in zf.namelist()


def test_biblioteca_leva_o_texto_e_nao_os_vetores(dados):
    """Vetor é reconstruível a partir do texto; num zip ele só pesa."""
    from livia import backup
    import io, zipfile

    livro = dados / "biblioteca" / "curso-de-c"
    livro.mkdir(parents=True)
    (livro / "meta.json").write_text('{"titulo": "Curso de C"}', encoding="utf-8")
    (livro / "trechos.jsonl").write_text('{"pagina": 1, "texto": "ponteiros"}\n',
                                         encoding="utf-8")
    (livro / "vetores.npy").write_bytes(b"\x00" * 5000)

    zip_bytes, _ = backup.exportar()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        nomes = zf.namelist()

    assert "biblioteca/curso-de-c/meta.json" in nomes
    assert "biblioteca/curso-de-c/trechos.jsonl" in nomes
    assert not any(n.endswith(".npy") for n in nomes)


def test_restaurar_recupera_licoes_e_biblioteca(dados, monkeypatch):
    from livia import backup
    from livia.store import COLECOES

    pasta = dados / "lessons"
    pasta.mkdir()
    monkeypatch.setattr(COLECOES["lessons"], "directory", pasta)
    (pasta / "licao.md").write_text(
        "---\nname: licao\ndescription: Uma lição.\nkind: lesson\n---\n\nx\n",
        encoding="utf-8",
    )
    livro = dados / "biblioteca" / "manual"
    livro.mkdir(parents=True)
    (livro / "meta.json").write_text('{"titulo": "Manual"}', encoding="utf-8")

    zip_bytes, _ = backup.exportar()

    (pasta / "licao.md").unlink()
    (livro / "meta.json").unlink()

    contagem = backup.importar(zip_bytes)
    assert contagem["licoes"] == 1
    assert contagem["documentos"] == 1
    assert (pasta / "licao.md").exists()
    assert (livro / "meta.json").exists()


def test_backup_nunca_leva_chave_de_api(dados):
    """A regra que não muda: o .env fica fora, sempre."""
    from livia import backup
    import io, zipfile

    (dados / ".env").write_text("GEMINI_API_KEY=segredo-absoluto\n", encoding="utf-8")

    zip_bytes, _ = backup.exportar()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert ".env" not in zf.namelist()
        conteudo = b"".join(zf.read(n) for n in zf.namelist())
    assert b"segredo-absoluto" not in conteudo
