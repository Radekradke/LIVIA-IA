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
