"""Utilitários puros dos coletores - sem rede e sem banco."""
import subprocess

import pandas as pd
import pytest

from collect.github_metrics import _eh_bot, _parse_owner_repo
from collect.pydriller_collect import (_eh_bot_nome, agregar_por_dia,
                                       agregar_por_mes_autor,
                                       calcular_bus_factor,
                                       calcular_churn_relativo, contar_loc,
                                       coletar_commits)


@pytest.mark.parametrize("url,esperado", [
    ("https://github.com/pallets/flask", ("pallets", "flask")),
    ("https://github.com/pallets/flask.git", ("pallets", "flask")),
    ("https://github.com/pallets/flask/", ("pallets", "flask")),
    ("https://github.com/django/django.git", ("django", "django")),
    ("https://github.com/owner/meu.gitrepo", ("owner", "meu.gitrepo")),
    ("https://github.com/owner/repo.GIT", ("owner", "repo")),
])
def test_parse_owner_repo(url, esperado):
    assert _parse_owner_repo(url) == esperado


@pytest.mark.parametrize("url", [
    "https://github.com/soloumo",
    "https://github.com/",
    "url qualquer",
])
def test_parse_url_invalida_levanta_value_error(url):
    with pytest.raises(ValueError):
        _parse_owner_repo(url)


@pytest.mark.parametrize("user,esperado", [
    ({"login": "dependabot[bot]", "type": "Bot"}, True),
    ({"login": "meu-bot", "type": "User"}, True),
    ({"login": "github-actions[bot]", "type": "User"}, True),
    ({"login": "bryan7paz", "type": "User"}, False),
    ({"login": "psf", "type": "Organization"}, False),
])
def test_eh_bot(user, esperado):
    assert _eh_bot(user) is esperado


def test_bus_factor_sem_dados():
    assert calcular_bus_factor(pd.DataFrame({"autor": []})) is None


def test_bus_factor_autor_unico():
    assert calcular_bus_factor(pd.DataFrame({"autor": ["a"] * 10})) == 1


def test_bus_factor_duas_pessoas_dominantes():
    df = pd.DataFrame({"autor": ["a"] * 60 + ["b"] * 40})
    assert calcular_bus_factor(df) == 1


def test_bus_factor_metade_exata():
    df = pd.DataFrame({"autor": ["a"] * 50 + ["b"] * 50})
    assert calcular_bus_factor(df) == 2


def test_eh_bot_nome():
    assert _eh_bot_nome("dependabot[bot]")
    assert _eh_bot_nome("github-actions[bot]")
    assert _eh_bot_nome("renovate[bot]")
    assert _eh_bot_nome("ci-bot")
    assert not _eh_bot_nome("Abbott")
    assert not _eh_bot_nome("Ana Silva")
    assert not _eh_bot_nome("")
    assert not _eh_bot_nome(None)


def test_coletar_commits_ignora_bots(tmp_path):
    """End-to-end com repo local: commits de bots saem do df inteiro."""
    def _commit(nome, email, msg):
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", f"user.name={nome}",
             "-c", f"user.email={email}", "commit", "--allow-empty",
             "-q", "-m", msg],
            check=True,
        )

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _commit("dependabot[bot]", "bot@b", "bump")
    _commit("Ana Silva", "a@a", "fix")
    _commit("ci-bot", "ci@c", "pipeline")

    df = coletar_commits(str(tmp_path))

    assert len(df) == 1
    assert df.iloc[0]["autor"] == "Ana Silva"
    # e o Bus Factor calculado sobre o df filtrado já é o humano
    assert calcular_bus_factor(df) == 1


def _repo_git_tmp(tmp_path):
    """git repo mínimo rastreando arquivos (init + add, sem commit)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)


def test_contar_loc_ignora_binarios_pela_extensao(tmp_path):
    (tmp_path / "codigo.py").write_text("x = 1\n\ny = 2\n", encoding="utf-8")
    (tmp_path / "imagem.png").write_bytes(b"\x89PNG\nlinhas fake\noutra\n")
    _repo_git_tmp(tmp_path)
    assert contar_loc(str(tmp_path)) == 3


def test_churn_relativo_sobre_o_loc(tmp_path):
    (tmp_path / "codigo.py").write_text("a = 1\nb = 2\n", encoding="utf-8")  # LOC 2
    _repo_git_tmp(tmp_path)
    df = pd.DataFrame({"autor": ["a"], "lines_added": [4], "lines_deleted": [2],
                       "dia": [pd.Timestamp("2026-09-01").date()]})
    assert calcular_churn_relativo(df, str(tmp_path)) == 3.0  # (4+2)/2


def test_agregar_por_dia():
    df = pd.DataFrame({
        "dia": [pd.Timestamp("2026-09-01").date(),
                pd.Timestamp("2026-09-01").date(),
                pd.Timestamp("2026-09-02").date()],
        "autor": ["ana", "ana", "bia"],
        "lines_added": [10, 5, 7],
        "lines_deleted": [1, 2, 3],
    })
    g = agregar_por_dia(df, 42)
    assert len(g) == 2
    dia1 = g[g["dia"] == pd.Timestamp("2026-09-01").date()].iloc[0]
    assert dia1["commits"] == 2 and dia1["autores_distintos"] == 1
    assert dia1["lines_added"] == 15 and dia1["lines_deleted"] == 3
    assert dia1["id_repositorio"] == 42


def test_agregar_por_mes_autor():
    df = pd.DataFrame({
        "dia": [pd.Timestamp("2026-08-01").date(),
                pd.Timestamp("2026-08-15").date(),
                pd.Timestamp("2026-09-01").date()],
        "autor": ["ana", "bia", "ana"],
        "lines_added": [1, 1, 1],
        "lines_deleted": [0, 0, 0],
    })
    g = agregar_por_mes_autor(df)
    ana_agosto = g[(g["autor"] == "ana")
                   & (g["mes"] == pd.Timestamp("2026-08-01").date())].iloc[0]
    assert ana_agosto["commits"] == 1
    assert len(g) == 3
