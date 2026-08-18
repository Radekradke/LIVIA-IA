"""Isolamento dos testes.

Este arquivo roda ANTES de qualquer import de `livia`, e isso é essencial.
`store.py` guarda os caminhos no momento do import, e `load_dotenv` não
sobrescreve variável de ambiente já definida — então é aqui, e só aqui, que
dá para desviar os testes dos dados reais do André.

As chaves de API são substituídas por valores falsos de propósito: mesmo que
um teste escape do mock, ele não gasta cota nem vaza nada.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="livia-testes-"))

os.environ["LIVIA_DATA_DIR"] = str(_TMP)
os.environ["LIVIA_WORKSPACE"] = str(_TMP / "workspace")
os.environ["GEMINI_API_KEY"] = "chave-falsa-gemini"
os.environ["GROQ_API_KEY"] = "chave-falsa-groq"
os.environ["LIVIA_PASSWORD"] = "senha-de-teste"
os.environ["LIVIA_AUTO_LEARN"] = "0"
os.environ["LIVIA_WEB"] = "0"
os.environ["LIVIA_TOOLS"] = "1"

import pytest  # noqa: E402

from livia import config  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Pasta de trabalho limpa e exclusiva para cada teste."""
    destino = tmp_path / "workspace"
    destino.mkdir()
    monkeypatch.setattr(config, "WORKSPACE", destino)
    return destino


@pytest.fixture
def dados(tmp_path, monkeypatch):
    """Pasta de dados limpa e exclusiva para cada teste."""
    destino = tmp_path / "dados"
    destino.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", destino)
    return destino
