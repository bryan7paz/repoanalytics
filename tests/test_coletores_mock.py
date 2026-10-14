"""Coletores do GitHub com rede 100% mockada (lib responses).

Cobre: paginação, filtro de PRs, bots ignorados, retry em rate limit
e contagem de releases dentro da janela.
"""
import responses
from responses import registries

from collect.github_metrics import (API_BASE, _cursor_proxima_pagina, _get,
                                    _primeiro_comentario_detalhe,
                                    buscar_issues, buscar_releases,
                                    calcular_ttfr_mediano)

OWNER, REPO = "faptc", "demo"


def _issue(numero, state="open", criada="2026-09-01T10:00:00Z", pr=False):
    iss = {"number": numero, "state": state, "created_at": criada,
           "user": {"login": "alguem", "type": "User"}}
    if pr:
        iss["pull_request"] = {"url": "https://api.github.com/x"}
    return iss


@responses.activate(registry=registries.OrderedRegistry)
def test_buscar_issues_pagina_ate_acabar():
    pagina1 = [_issue(i) for i in range(1, 101)]   # 100 + Link next -> continua
    pagina2 = [_issue(101, state="closed")]         # sem Link next -> para
    proxima = (f"{API_BASE}/repos/{OWNER}/{REPO}/issues"
               "?per_page=100&after=c2VnMA==")
    responses.get(f"{API_BASE}/repos/{OWNER}/{REPO}/issues", json=pagina1,
                  headers={"Link": f'<{proxima}>; rel="next"'})
    responses.get(f"{API_BASE}/repos/{OWNER}/{REPO}/issues", json=pagina2)

    issues = buscar_issues(OWNER, REPO, "2026-03-01T00:00:00Z")

    assert len(issues) == 101
    assert issues[-1]["number"] == 101
    # o GitHub exige cursor (after=) - nunca paginacao por page=
    assert "&page=" not in responses.calls[0].request.url
    assert "after=" in responses.calls[1].request.url


def test_cursor_proxima_pagina_parseia_link():
    headers = {
        "Link": ('<https://api.github.com/x?per_page=100&after=abc%3D%3D>; '
                 'rel="next", '
                 '<https://api.github.com/x?per_page=100&before=zzz>; '
                 'rel="prev"'),
    }
    assert _cursor_proxima_pagina(headers) == "abc=="
    assert _cursor_proxima_pagina({}) is None
    assert _cursor_proxima_pagina(
        {"Link": '<https://api.github.com/x>; rel="last"'}) is None


@responses.activate
def test_primeiro_comentario_detalhe_ignora_bots():
    responses.get(
        f"{API_BASE}/repos/{OWNER}/{REPO}/issues/7/comments",
        json=[
            {"created_at": "2026-09-02T08:00:00Z",
             "user": {"login": "dependabot[bot]", "type": "Bot"}},
            {"created_at": "2026-09-03T09:30:00Z",
             "user": {"login": "maria", "type": "User"}},
        ],
    )
    data, login = _primeiro_comentario_detalhe(OWNER, REPO, 7)
    assert data == "2026-09-03T09:30:00Z"
    assert login == "maria"


@responses.activate
def test_primeiro_comentario_detalhe_sem_resposta_retorna_none():
    responses.get(f"{API_BASE}/repos/{OWNER}/{REPO}/issues/9/comments",
                  json=[])
    assert _primeiro_comentario_detalhe(OWNER, REPO, 9) == (None, None)


@responses.activate
def test_ttfr_mediana_ignora_pr_e_issue_sem_resposta():
    issues = [
        _issue(1, pr=True),   # PR: não entra no cálculo
        _issue(2),            # issue aberta dia 1, respondida ~1,5 dia depois
        _issue(3),            # issue sem comentário humano: fora
    ]
    responses.get(f"{API_BASE}/repos/{OWNER}/{REPO}/issues/2/comments",
                  json=[{"created_at": "2026-09-02T22:00:00Z",
                         "user": {"login": "maria", "type": "User"}}])
    responses.get(f"{API_BASE}/repos/{OWNER}/{REPO}/issues/3/comments", json=[])

    mediana, qtd, pessoas = calcular_ttfr_mediano(OWNER, REPO, issues)

    assert qtd == 1
    assert abs(mediana - 1.5) < 1e-6
    assert pessoas == 2  # criador da issue + quem respondeu


@responses.activate
def test_ttfr_sem_resposta_mesmo_conta_pessoas_ativas():
    issues = [_issue(5, criada="2026-09-05T00:00:00Z")]
    responses.get(f"{API_BASE}/repos/{OWNER}/{REPO}/issues/5/comments", json=[])
    mediana, qtd, pessoas = calcular_ttfr_mediano(OWNER, REPO, issues)
    assert mediana is None
    assert qtd == 0
    assert pessoas == 1  # quem abriu a issue entra nas pessoas ativas


@responses.activate
def test_buscar_releases_conta_somente_dentro_da_janela():
    responses.get(
        f"{API_BASE}/repos/{OWNER}/{REPO}/releases",
        json=[
            {"tag_name": "v2.0", "published_at": "2026-08-01T00:00:00Z"},   # dentro
            {"tag_name": "v1.0", "published_at": "2026-01-01T00:00:00Z"},   # fora
            {"tag_name": "draft", "published_at": None},                     # sem data
        ],
    )
    assert buscar_releases(OWNER, REPO, "2026-03-01T00:00:00Z") == 1


@responses.activate(registry=registries.OrderedRegistry)
def test_get_retenta_depois_de_rate_limit(monkeypatch):
    # não dorme de verdade no teste
    import collect.github_metrics as gm
    monkeypatch.setattr(gm.time, "sleep", lambda *_: None)

    url = f"{API_BASE}/repos/{OWNER}/{REPO}"
    responses.get(url, status=403,
                  body='{"message":"API rate limit exceeded"}',
                  headers={"Retry-After": "1"})
    responses.get(url, json={"ok": True})

    resp = _get(url)

    assert resp.json() == {"ok": True}
    assert len(responses.calls) == 2
