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
