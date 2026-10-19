"""Regras do setup.py e do .env que valem para quem baixa o projeto."""
import pathlib
import sys

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import setup
from config import _token_efetivo


def test_placeholder_do_token_nao_conta_como_configurado():
    assert not setup._token_util("coloque_seu_token_aqui")
    assert not setup._token_util("  coloque_seu_token_aqui  ")
    assert not setup._token_util("")
    assert not setup._token_util(None)


def test_token_real_conta_como_configurado():
    assert setup._token_util("ghp_exemplo_real")


def test_placeholder_vira_token_vazio_no_config():
    assert _token_efetivo("coloque_seu_token_aqui") == ""
    assert _token_efetivo("  coloque_seu_token_aqui ") == ""
    assert _token_efetivo("ghp_exemplo_real") == "ghp_exemplo_real"


def test_passo_banco_repassa_credenciais_do_env_para_o_processo_filho(monkeypatch):
    """Sem isso o instalador connecta com senha vazia e acusa falha."""
    capturado = {}

    class Resultado:
        returncode = 0
        stdout = "banco fap ja existe"
        stderr = ""

    def _rodar_falso(cmd, **kw):
        capturado["cmd"] = cmd
        capturado["kw"] = kw
        return Resultado()

    monkeypatch.setattr(setup, "_rodar", _rodar_falso)
    monkeypatch.setattr(setup, "_porta_aberta", lambda *a, **k: True)
    # qualquer caminho existente serve: o passo so checa .exists()
    monkeypatch.setattr(setup, "VENV_PY", pathlib.Path(__file__))
    monkeypatch.setattr(setup, "_ler_env", lambda: {
        "DB_HOST": "localhost",
        "DB_PORT": "5432",
        "DB_USER": "postgres",
        "DB_PASSWORD": "senha_secreta",
    })

    setup.passo_banco()

    ambiente = capturado["kw"]["env"]
    assert ambiente["DB_PASSWORD"] == "senha_secreta"
    assert ambiente["DB_USER"] == "postgres"
    assert ambiente["DB_PORT"] == "5432"
    # a senha nao pode vazar na linha de comando
    assert "senha_secreta" not in " ".join(capturado["cmd"])
