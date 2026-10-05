"""Helpers do database.py — criptografia do token e upserts (usa o banco de teste)."""
from datetime import date

import pandas as pd

from database import (buscar_usuario, cifrar_token, connection,
                      decifrar_token, desvincular_e_limpar,
                      insert_metrica_diaria, link_usuario_repositorio,
                      repositorio_por_id, upsert_repositorio, upsert_usuario)


def test_cifrar_sem_session_secret_nao_armazena(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    assert cifrar_token("qualquer-token") is None


def test_decifrar_token_plaintext_legado():
    assert decifrar_token("token-puro-antigo") == "token-puro-antigo"


def test_decifrar_token_blob_de_outra_chave():
    assert decifrar_token("gAAAAAchave-errada") is None


def test_upsert_usuario_token_cifrado_roundtrip():
    id_usuario = upsert_usuario(github_id=765432101, login="teste-fap-token",
                                access_token="token-de-teste-123")
    dados = buscar_usuario(id_usuario)
    assert dados["access_token"] == "token-de-teste-123"


def test_upsert_repositorio_mesma_url_retorna_o_mesmo_id():
    id1 = upsert_repositorio("limpar-teste",
                             "https://github.com/teste-fap/limpar-teste.git")
    id2 = upsert_repositorio("limpar-teste",
                             "https://github.com/teste-fap/limpar-teste.git")
    assert id1 == id2


def test_upsert_repositorio_mesmo_nome_urls_diferentes():
    id1 = upsert_repositorio("framework",
                             "https://github.com/teste-fap/framework.git")
    id2 = upsert_repositorio("framework",
                             "https://github.com/teste-fap/outro-framework.git")
    try:
        assert id1 != id2
    finally:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM Repositorio WHERE id_repositorio IN %s",
                            ((id1, id2),))


def test_desvincular_e_limpar_apaga_repo_sem_dono():
    id_usuario = upsert_usuario(github_id=765432100, login="teste-fap",
                                nome="Teste")
    id_repo = upsert_repositorio("limpar-teste",
                                 "https://github.com/teste-fap/limpar-teste.git")
    link_usuario_repositorio(id_usuario, id_repo)

    apagado = desvincular_e_limpar(id_usuario, id_repo)

    assert apagado is True
    assert repositorio_por_id(id_repo) is None


def test_insert_metrica_diaria_limpa_dias_antigos_da_janela():
    """Dias da janela sem commits perdem as linhas antigas (anti-stale)."""
    id_repo = upsert_repositorio(
        "diaria-stale", "https://github.com/teste-fap/diaria-stale.git")
    try:
        base = pd.DataFrame({
            "id_repositorio": [id_repo, id_repo],
            "dia": [date(2026, 3, 10), date(2026, 3, 11)],
            "commits": [3, 2],
            "autores_distintos": [1, 1],
            "lines_added": [10, 5],
            "lines_deleted": [2, 1],
        })
        janela = (date(2026, 3, 1), date(2026, 3, 31))
        insert_metrica_diaria(base, id_repo, *janela)

        # segunda coleta da mesma janela sem o dia 11 (ex.: passou a ser
        # filtrado como bot) — a linha velha precisa sumir, não sobreviver
        insert_metrica_diaria(
            base[base["dia"] == date(2026, 3, 10)], id_repo, *janela)

        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*), COALESCE(SUM(commits), 0) "
                    "FROM Metrica_Diaria WHERE id_repositorio = %s",
                    (id_repo,),
                )
                dias, total = cur.fetchone()
        assert (dias, total) == (1, 3)

        # janela totalmente vazia: tudo limpo, inclusive o dia restante
        insert_metrica_diaria(pd.DataFrame(), id_repo, *janela)
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM Metrica_Diaria "
                    "WHERE id_repositorio = %s",
                    (id_repo,),
                )
                assert cur.fetchone()[0] == 0
    finally:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM Metrica_Diaria WHERE id_repositorio = %s",
                    (id_repo,))
                cur.execute(
                    "DELETE FROM Repositorio WHERE id_repositorio = %s",
                    (id_repo,))
