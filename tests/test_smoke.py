"""Smoke das rotas com o test client do Flask (login simulado via sessão)."""
import pytest

GITHUB_ID_TESTE = 987654321


@pytest.fixture()
def client():
    import app as app_mod
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


@pytest.fixture()
def logado(client):
    from database import upsert_usuario
    id_usuario = upsert_usuario(github_id=GITHUB_ID_TESTE, login="dev-teste",
                                nome="Teste")
    with client.session_transaction() as s:
        s["_user_id"] = str(id_usuario)
    return id_usuario


@pytest.fixture()
def repo_logado(client, logado):
    from database import (desvincular_e_limpar, link_usuario_repositorio,
                          upsert_repositorio)
    id_repo = upsert_repositorio("resumo-teste",
                                 "https://github.com/teste-fap/resumo-teste.git")
    link_usuario_repositorio(logado, id_repo)
    yield id_repo
    desvincular_e_limpar(logado, id_repo)


def test_health_publico(client):
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("rota", ["/", "/api/repos", "/api/coleta/status",
                                  "/repo/1", "/api/repo/1/periodos"])
def test_rotas_protegidas_redirecionam_para_login(client, rota):
    resp = client.get(rota)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_logout_somente_post(client):
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
    assert "/login" in client.post("/logout").headers["Location"]


def test_post_de_repos_tambem_protegido(client):
    resp = client.post("/repos", json={"url": "https://github.com/a/b"})
    assert resp.status_code == 302
    assert client.put("/repos/1", json={}).status_code == 302


def test_login_dev_bloqueado_fora_de_localhost(client):
    resp = client.get("/login/dev",
                      environ_overrides={"REMOTE_ADDR": "10.9.8.7"})
    assert resp.status_code == 404


def test_login_dev_disponivel_sem_oauth(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "OAUTH_CONFIGURADO", False)
    resp = client.get("/login/dev")
    assert resp.status_code == 302


def test_login_renderiza_botao_demo_sem_oauth(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "OAUTH_CONFIGURADO", False)
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"modo desenvolvimento" in resp.data
    assert b"Entrar com GitHub" not in resp.data


def test_login_renderiza_github_com_oauth(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "OAUTH_CONFIGURADO", True)
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Entrar com GitHub" in resp.data
    assert b"modo desenvolvimento" not in resp.data


def test_login_mostra_mensagem_amigavel(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "OAUTH_CONFIGURADO", True)
    resp = client.get("/login?erro=acesso_negado")
    assert resp.status_code == 200
    assert b"acesso negado na tela do GitHub" in resp.data


def test_callback_mapeia_acesso_negado(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "OAUTH_CONFIGURADO", True)
    with client.session_transaction() as s:
        s["oauth_state"] = "estado-teste"
    resp = client.get("/callback?state=estado-teste&error=access_denied")
    assert resp.status_code == 302
    assert "erro=acesso_negado" in resp.headers["Location"]


def test_callback_mapeia_erro_generico(client, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "OAUTH_CONFIGURADO", True)
    with client.session_transaction() as s:
        s["oauth_state"] = "estado-teste"
    resp = client.get("/callback?state=estado-teste&error=application_suspended")
    assert resp.status_code == 302
    assert "erro=github_application_suspended" in resp.headers["Location"]


def test_fluxo_logado_completo(client, logado):
    assert client.get("/").status_code == 200

    repos = client.get("/api/repos")
    assert repos.status_code == 200
    assert isinstance(repos.get_json(), list)

    status = client.get("/api/coleta/status")
    assert status.status_code == 200
    assert "estado" in status.get_json()

    # repo inexistente não pertence ao usuário logado -> 403
    assert client.get("/repo/9999999").status_code == 403
    assert client.get("/api/repo/9999999/resumo").status_code == 403
    assert client.get("/api/repo/9999999/github").status_code == 403


def test_resumo_padrao_sem_coleta(client, repo_logado):
    resp = client.get(f"/api/repo/{repo_logado}/resumo")
    assert resp.status_code == 200
    dados = resp.get_json()
    assert dados["janela"] is None
    assert dados["commits_total"] == 0
    assert dados["autores_periodo"] == []
    assert dados["autores_linhas"] == []
    assert dados["curva"] is not None


@pytest.mark.parametrize("tipo,rotulo", [
    ("1d", "último dia"),
    ("7d", "últimos 7 dias"),
    ("3m", "últimos 3 meses"),
    ("6m", "últimos 6 meses"),
])
def test_resumo_seletor_de_periodo(client, repo_logado, tipo, rotulo):
    resp = client.get(f"/api/repo/{repo_logado}/resumo?periodo={tipo}")
    assert resp.status_code == 200
    dados = resp.get_json()
    assert dados["janela"]["tipo"] == tipo
    assert dados["janela"]["rotulo"] == rotulo
    assert dados["curva"] is not None


def test_resumo_periodo_invalido_cai_no_padrao(client, repo_logado):
    resp = client.get(f"/api/repo/{repo_logado}/resumo?periodo=batata")
    assert resp.status_code == 200
    dados = resp.get_json()
    assert "tipo" not in (dados["janela"] or {})


def test_resumo_periodo_personalizado(client, repo_logado):
    resp = client.get(f"/api/repo/{repo_logado}/resumo"
                      "?periodo=2026-01-01%7C2026-06-30")
    assert resp.status_code == 200
    dados = resp.get_json()
    assert dados["janela"]["tipo"] == "custom"
    assert dados["janela"]["inicio"] == "2026-01-01"
    assert dados["janela"]["fim"] == "2026-06-30"


def test_resumo_periodo_personalizado_invertido_cai_no_padrao(client,
                                                              repo_logado):
    resp = client.get(f"/api/repo/{repo_logado}/resumo"
                      "?periodo=2026-06-30%7C2026-01-01")
    assert resp.status_code == 200
    dados = resp.get_json()
    assert "tipo" not in (dados["janela"] or {})


def test_periodos_logado_lista_vazia(client, repo_logado):
    resp = client.get(f"/api/repo/{repo_logado}/periodos")
    assert resp.status_code == 200
    assert resp.get_json() == {"periodos": []}


def test_periodos_outro_repo_403(client, logado):
    assert client.get("/api/repo/9999999/periodos").status_code == 403


def test_comparar_sem_login_redireciona(client):
    assert client.get("/comparar?ids=1,2").status_code == 302


def test_comparar_logado_sem_ids_mostra_vazio(client, logado):
    resp = client.get("/comparar")
    assert resp.status_code == 200
    assert "Nenhum repositório selecionado" in resp.get_data(as_text=True)


def test_comparar_filtra_repos_de_outros_usuarios(client, logado):
    with client.session_transaction() as s:
        assert s.get("_user_id") == str(logado)
    resp = client.get("/comparar?ids=9999999")
    assert resp.status_code == 200
    assert "Nenhum repositório selecionado" in resp.get_data(as_text=True)


def test_adicionar_repo_com_url_invalida(client, logado):
    resp = client.post("/repos", json={"url": "https://github.com/so-um-dono"})
    assert resp.status_code == 400
    assert "erro" in resp.get_json()


def test_adicionar_repo_sem_url(client, logado):
    resp = client.post("/repos", json={})
    assert resp.status_code == 400


@pytest.fixture()
def repo_teste(logado):
    """Repo vinculado ao usuário logado (removido do BD ao fim do teste)."""
    from database import upsert_repositorio, link_usuario_repositorio, connection
    id_repo = upsert_repositorio("repo-fap-teste",
                                 "https://github.com/fap-teste/repo-fap.git")
    link_usuario_repositorio(logado, id_repo, "Repo Teste")
    yield id_repo
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Repositorio WHERE id_repositorio = %s",
                        (id_repo,))


def test_editar_repo_renomeia(client, logado, repo_teste):
    resp = client.put(f"/repos/{repo_teste}", json={"nome": "Nome Novo"})
    assert resp.status_code == 200
    assert resp.get_json().get("ok") is True
    repos = client.get("/api/repos").get_json()
    r = next(x for x in repos if x["id_repositorio"] == repo_teste)
    assert r["nome_exibicao"] == "Nome Novo"


def test_editar_repo_url_invalida(client, logado, repo_teste):
    resp = client.put(f"/repos/{repo_teste}",
                      json={"url": "https://github.com/so-um-dono"})
    assert resp.status_code == 400
    assert "erro" in resp.get_json()


def test_editar_repo_sem_dados(client, logado, repo_teste):
    resp = client.put(f"/repos/{repo_teste}", json={})
    assert resp.status_code == 400
    assert "erro" in resp.get_json()


def test_editar_repo_de_outro_usuario(client, logado):
    assert client.put("/repos/9999999", json={"nome": "X"}).status_code == 403


def test_delete_repo_foi_removido(client, logado, repo_teste):
    assert client.delete(f"/repos/{repo_teste}").status_code == 405


def test_api_repos_tem_visao_macro(client, logado, repo_teste):
    repos = client.get("/api/repos").get_json()
    r = next(x for x in repos if x["id_repositorio"] == repo_teste)
    assert "commits" in r and "autores" in r and "score" in r
