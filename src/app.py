"""Backend Flask: login OAuth GitHub, gestão de repositórios e análises."""
import csv
import io
import logging
import os
import secrets
import threading
from datetime import datetime, timezone

import requests as rq
from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pandas as pd

import analises
from config import GITHUB_TOKEN, MESES_ANALISE
from database import (connection, init_schema, repositorios_pendentes,
                      repositorios_vinculados, repositorios_do_usuario,
                      repositorio_por_id, ultimo_periodo, usuario_dono,
                      upsert_usuario, upsert_repositorio,
                      link_usuario_repositorio,
                      buscar_usuario, marcar_coletado,
                      resumo_repos_usuario, renomear_exibicao,
                      atualizar_url_repo, marcar_pendente, autores_linhas)
import status
from collect.pydriller_collect import executar as coletar_code_churn
from collect.github_metrics import (executar as coletar_metricas_sociais,
                                    _parse_owner_repo, buscar_contribuidores,
                                    listar_releases)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("fap")

app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "..", "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "..", "static"),
)
app.secret_key = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
if not os.getenv("SESSION_SECRET"):
    log.warning("SESSION_SECRET ausente — sessões não sobrevivem a restarts "
                "e tokens OAuth de usuários não são armazenados.")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
OAUTH_CONFIGURADO = bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para continuar."


class Usuario(UserMixin):
    def __init__(self, dados):
        self.id = dados["id_usuario"]
        self.github_id = dados["github_id"]
        self.login = dados["login"]
        self.nome = dados.get("nome")
        self.avatar_url = dados.get("avatar_url")
        self.access_token = dados.get("access_token")


@login_manager.user_loader
def _carregar_usuario(id_usuario):
    dados = buscar_usuario(int(id_usuario))
    return Usuario(dados) if dados else None


def _token_usuario():
    """Token do usuário logado; cai para o do sistema se ausente."""
    if current_user.is_authenticated and getattr(current_user, "access_token", None):
        return current_user.access_token
    return GITHUB_TOKEN or None


# ---------------------------------------------------------------------------
# Autenticação (OAuth GitHub)
# ---------------------------------------------------------------------------

ERROS_LOGIN = {
    "state_invalido": "sessão expirada — tente novamente.",
    "acesso_negado": "acesso negado na tela do GitHub — autorize a FAP para entrar.",
    "sem_codigo": "o GitHub não devolveu o código de autorização.",
    "falha_troca_token": "falha ao trocar o código pelo token.",
    "falha_perfil": "falha ao consultar seu perfil no GitHub.",
}


@app.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("meus_repos"))
    erro = request.args.get("erro")
    return render_template("login.html", oauth=OAUTH_CONFIGURADO, erro=erro,
                           erro_msg=ERROS_LOGIN.get(erro))


@app.route("/login/github")
def login_github():
    if not OAUTH_CONFIGURADO:
        abort(404)
    state = secrets.token_hex(16)
    session["oauth_state"] = state
    return redirect(
        "https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}&scope=read:user&state={state}"
    )


@app.route("/callback")
def callback():
    if not OAUTH_CONFIGURADO:
        abort(404)
    if request.args.get("state") != session.pop("oauth_state", None):
        return redirect(url_for("login", erro="state_invalido"))
    erro_github = request.args.get("error")
    if erro_github:
        erro = ("acesso_negado" if erro_github == "access_denied"
                else f"github_{erro_github}")
        return redirect(url_for("login", erro=erro))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("login", erro="sem_codigo"))
    try:
        resp = rq.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            timeout=15,
        )
        dados = resp.json()
    except rq.RequestException:
        return redirect(url_for("login", erro="falha_troca_token"))
    token = dados.get("access_token")
    if not token:
        log.error("OAuth sem access_token: %s", dados)
        return redirect(url_for("login", erro="falha_troca_token"))
    try:
        resp = rq.get(
            "https://api.github.com/user",
            headers={"Accept": "application/vnd.github+json",
                     "Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        perfil = resp.json()
    except rq.RequestException:
        return redirect(url_for("login", erro="falha_perfil"))
    id_usuario = upsert_usuario(
        github_id=perfil["id"],
        login=perfil["login"],
        nome=perfil.get("name"),
        avatar_url=perfil.get("avatar_url"),
        access_token=token,
    )
    login_user(Usuario(buscar_usuario(id_usuario)))
    return redirect(url_for("meus_repos"))


@app.route("/login/dev")
def login_dev():
    """Fallback local quando o OAuth ainda não está configurado.

    Somente a partir de 127.0.0.1/::1: se o app um dia sair do localhost,
    a rota some (404) em vez de virar entrada livre na plataforma.
    """
    if OAUTH_CONFIGURADO or request.remote_addr not in ("127.0.0.1", "::1"):
        abort(404)
    id_usuario = upsert_usuario(github_id=0, login="dev",
                                nome="Desenvolvimento")
    login_user(Usuario(buscar_usuario(id_usuario)))
    return redirect(url_for("meus_repos"))


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def meus_repos():
    repos = repositorios_do_usuario(current_user.id)
    return render_template("meus_repos.html", repos=repos,
                           meses=MESES_ANALISE)


@app.route("/repo/<int:id_repositorio>")
@login_required
def repo_detalhe(id_repositorio):
    if not usuario_dono(current_user.id, id_repositorio):
        abort(403)
    repo = repositorio_por_id(id_repositorio)
    if not repo:
        abort(404)
    return render_template("repo_detalhe.html", repo=repo,
                           meses=MESES_ANALISE)


@app.route("/api/health")
def health():
    return jsonify({"status": "ok",
                    "timestamp": datetime.now(timezone.utc).isoformat()})


@app.route("/api/coleta/status")
@login_required
def api_coleta_status():
    return jsonify(status.snapshot())


@app.route("/api/repos")
@login_required
def api_repos():
    """Lista JSON dos repositórios do usuário com visão macro (polling)."""
    repos = repositorios_do_usuario(current_user.id)
    coleta = status.snapshot()
    coletando = coleta["estado"] == "coletando"
    resumo = resumo_repos_usuario([r["id_repositorio"] for r in repos])
    for r in repos:
        r["coletando"] = bool(coletando and coleta.get("repo_atual") == r["nome"])
        r["atualizado_em"] = str(r["atualizado_em"]) if r["atualizado_em"] else None
        m = resumo.get(r["id_repositorio"], {})
        r["commits"] = m.get("commits", 0)
        r["autores"] = m.get("autores", 0)
        if r["coletado"] and m:
            s = analises.score_sustentabilidade({
                "commits": m.get("commits"),
                "bus_factor": m.get("bus_factor"),
                "ttfr": m.get("ttfr"),
                "churn_relativo": m.get("churn_relativo"),
            })
            r["score"] = s["score"]
        else:
            r["score"] = None
    return jsonify(repos)


# ---------------------------------------------------------------------------
# CRUD de repositórios
# ---------------------------------------------------------------------------

@app.route("/repos", methods=["POST"])
@login_required
def adicionar_repo():
    data = request.get_json(silent=True) or {}
    nome_exibicao = (data.get("nome") or "").strip()
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify(erro="Informe a URL do repositório."), 400
    try:
        owner, repo_name = _parse_owner_repo(url)
    except ValueError:
        return jsonify(erro="URL inválida. Use https://github.com/owner/repo"), 400

    token = _token_usuario()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = rq.get(f"https://api.github.com/repos/{owner}/{repo_name}",
                      headers=headers, timeout=15)
    except rq.RequestException:
        return jsonify(erro="Falha ao consultar o GitHub. Tente novamente."), 502
    if resp.status_code == 404:
        return jsonify(erro="Repositório não encontrado no GitHub."), 404
    if resp.status_code != 200:
        return jsonify(erro=f"GITHUB respondeu HTTP {resp.status_code}."), 502
    info = resp.json()

    canonica = info.get("html_url", f"https://github.com/{owner}/{repo_name}") + ".git"
    id_repositorio = upsert_repositorio(info.get("name") or repo_name, canonica)
    link_usuario_repositorio(current_user.id, id_repositorio,
                             nome_exibicao or info.get("name") or repo_name)

    if id_repositorio in repositorios_pendentes():
        threading.Thread(target=tarefa_mineracao, args=([id_repositorio], token),
                         name=f"coleta-repo-{id_repositorio}",
                         daemon=True).start()
    return jsonify(ok=True, id_repositorio=id_repositorio)


@app.route("/repos/<int:id_repositorio>", methods=["PUT"])
@login_required
def editar_repo(id_repositorio):
    """Edita o repositório do usuário: novo nome de exibição e/ou nova URL.

    A nova URL é validada no GitHub; ao trocá-la, o repositório volta a ficar
    pendente e a coleta roda em background (re-coleta dos dados).
    """
    if not usuario_dono(current_user.id, id_repositorio):
        abort(403)
    data = request.get_json(silent=True) or {}
    novo_nome = (data.get("nome") or "").strip()
    nova_url = (data.get("url") or "").strip()
    if not novo_nome and not nova_url:
        return jsonify(erro="Informe um novo nome ou uma nova URL."), 400
    token = _token_usuario()
    re_coletando = False
    if nova_url:
        try:
            owner, repo_name = _parse_owner_repo(nova_url)
        except ValueError:
            return jsonify(erro="URL inválida. Use https://github.com/owner/repo"), 400
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            resp = rq.get(f"https://api.github.com/repos/{owner}/{repo_name}",
                          headers=headers, timeout=15)
        except rq.RequestException:
            return jsonify(erro="Falha ao consultar o GitHub. Tente novamente."), 502
        if resp.status_code == 404:
            return jsonify(erro="Repositório não encontrado no GitHub."), 404
        if resp.status_code != 200:
            return jsonify(erro=f"GITHUB respondeu HTTP {resp.status_code}."), 502
        info = resp.json()
        canonica = info.get("html_url", f"https://github.com/{owner}/{repo_name}") + ".git"
        atualizar_url_repo(id_repositorio, canonica,
                           info.get("name") or repo_name)
        if not novo_nome:
            novo_nome = info.get("name") or repo_name
        marcar_pendente(id_repositorio)
        re_coletando = True
    renomear_exibicao(current_user.id, id_repositorio, novo_nome)
    if re_coletando:
        threading.Thread(target=tarefa_mineracao, args=([id_repositorio], token),
                         name=f"coleta-repo-{id_repositorio}",
                         daemon=True).start()
    return jsonify(ok=True, re_coletando=re_coletando)


# ---------------------------------------------------------------------------
# APIs de dados do repositório
# ---------------------------------------------------------------------------

def _num(v):
    """Float seguro: None para ausente/NaN (NaN != NaN)."""
    try:
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _inteiro(v):
    f = _num(v)
    return int(f) if f is not None else None


def _linha_metricas(row):
    return {
        "periodo_inicio": str(row["periodo_inicio"]),
        "periodo_fim": str(row["periodo_fim"]),
        "ttfr": _num(row["ttfr_mediano_dias"]),
        "bus_factor": _inteiro(row["bus_factor"]),
        "churn_relativo": _num(row["churn_relativo"]),
        "issues_abertas": _inteiro(row["issues_abertas"]),
        "issues_fechadas": _inteiro(row["issues_fechadas"]),
        "cadencia_releases": _num(row["cadencia_releases"]),
    }


def _metricas_latest(id_repositorio):
    with connection() as conn:
        df = pd.read_sql(
            """
            SELECT periodo_inicio, periodo_fim, ttfr_mediano_dias, bus_factor,
                   churn_relativo, issues_abertas, issues_fechadas,
                   contribuidores_ativos, cadencia_releases
            FROM Metrica_Sustentabilidade
            WHERE id_repositorio = %s
            ORDER BY periodo_inicio DESC LIMIT 1
            """,
            conn, params=(id_repositorio,),
        )
    if df.empty:
        return None
    return _linha_metricas(df.iloc[0])


def _historico_score(id_repositorio):
    """Score de cada período coletado (commits somados pela janela do período)."""
    with connection() as conn:
        linhas = pd.read_sql(
            """
            SELECT ms.periodo_inicio, ms.periodo_fim, ms.ttfr_mediano_dias,
                   ms.bus_factor, ms.churn_relativo,
                   COALESCE((
                       SELECT SUM(d.commits) FROM Metrica_Diaria d
                       WHERE d.id_repositorio = ms.id_repositorio
                         AND d.dia BETWEEN ms.periodo_inicio AND ms.periodo_fim
                   ), 0) AS commits
            FROM Metrica_Sustentabilidade ms
            WHERE ms.id_repositorio = %s
            ORDER BY ms.periodo_inicio
            """,
            conn, params=(id_repositorio,),
        )
    historico = []
    for r in linhas.itertuples():
        s = analises.score_sustentabilidade({
            "commits": int(r.commits) if r.commits else None,
            "bus_factor": _inteiro(r.bus_factor),
            "ttfr": _num(r.ttfr_mediano_dias),
            "churn_relativo": _num(r.churn_relativo),
        })
        historico.append({
            "periodo_inicio": str(r.periodo_inicio),
            "periodo_fim": str(r.periodo_fim),
            "score": s["score"],
        })
    return historico


def _janela_exibicao(id_repositorio):
    """(inicio_filtro, periodo) das telas do repo.

    Com coleta concluída: filtra a partir do `periodo_inicio` gravado e
    devolve (inicio, fim) do último período — os números não driftam entre
    coletas. Sem coleta: cai para a janela corrente (hoje − MESES_ANALISE,
    dia 1) e periodo=None.
    """
    periodo = ultimo_periodo(id_repositorio)
    if periodo:
        inicio, fim = periodo
        return inicio, {"inicio": str(inicio), "fim": str(fim)}
    inicio = (pd.Timestamp.now()
              - pd.DateOffset(months=MESES_ANALISE)).replace(day=1).date()
    return inicio, None


JANELAS_RESUMO = {
    "1d": ("último dia",
           lambda: (pd.Timestamp.now() - pd.Timedelta(1, unit="D")).date()),
    "7d": ("últimos 7 dias",
           lambda: (pd.Timestamp.now() - pd.Timedelta(7, unit="D")).date()),
    "3m": ("últimos 3 meses",
           lambda: (pd.Timestamp.now() - pd.DateOffset(months=3)).date()),
    "6m": ("últimos 6 meses",
           lambda: (pd.Timestamp.now() - pd.DateOffset(months=6)).date()),
}


def _serie_diaria(id_repositorio, inicio):
    """Série dia a dia (commits, linhas +/−) desde `inicio`."""
    with connection() as conn:
        return pd.read_sql(
            """
            SELECT dia, SUM(commits) AS commits, SUM(lines_added) AS add,
                   SUM(lines_deleted) AS del
            FROM Metrica_Diaria
            WHERE id_repositorio = %s AND dia >= %s
            GROUP BY dia ORDER BY dia
            """,
            conn, params=(id_repositorio, inicio),
        )


def _totais_periodo(id_repositorio, inicio):
    """Totais de commits/linhas do repo desde `inicio` (janela corrente)."""
    with connection() as conn:
        df = pd.read_sql(
            """
            SELECT COALESCE(SUM(commits), 0) AS commits,
                   COALESCE(SUM(lines_added), 0) AS add,
                   COALESCE(SUM(lines_deleted), 0) AS del
            FROM Metrica_Diaria
            WHERE id_repositorio = %s AND dia >= %s
            """,
            conn, params=(id_repositorio, inicio),
        )
    r = df.iloc[0]
    return {"commits": int(r["commits"]), "linhas_add": int(r["add"]),
            "linhas_del": int(r["del"])}


def _dados_comparacao(id_repositorio):
    """Pacote completo de um repo para a tela de comparação."""
    repo = repositorio_por_id(id_repositorio)
    if not repo:
        return None
    inicio, periodo = _janela_exibicao(id_repositorio)
    totais = _totais_periodo(id_repositorio, inicio)
    metricas = _metricas_latest(id_repositorio)
    score = analises.score_sustentabilidade({
        "commits": totais["commits"] if totais["commits"] else None,
        "bus_factor": metricas["bus_factor"] if metricas else None,
        "ttfr": metricas["ttfr"] if metricas else None,
        "churn_relativo": metricas["churn_relativo"] if metricas else None,
    })
    serie = [
        {"dia": str(r.dia), "commits": int(r.commits)}
        for r in _serie_diaria(id_repositorio, inicio).itertuples()
    ]
    return {
        "repo": repo,
        "totais": totais,
        "metricas": metricas,
        "periodo": periodo,
        "score": score,
        "curva": analises.curva_concentracao(id_repositorio, inicio),
        "serie": serie,
    }


@app.route("/comparar")
@login_required
def comparar():
    """Comparação lado a lado dos repositórios do usuário (`?ids=1,2`)."""
    ids = []
    for parte in request.args.get("ids", "").split(","):
        parte = parte.strip()
        if parte.isdigit():
            ids.append(int(parte))
    dados = [
        d for d in (_dados_comparacao(i) for i in ids
                    if usuario_dono(current_user.id, i))
        if d
    ]
    return render_template("comparar.html", dados=dados, meses=MESES_ANALISE)


@app.route("/api/repo/<int:id_repositorio>/resumo")
@login_required
def api_repo_resumo(id_repositorio):
    if not usuario_dono(current_user.id, id_repositorio):
        abort(403)
    repo = repositorio_por_id(id_repositorio)
    if not repo:
        abort(404)
    inicio_padrao, janela_padrao = _janela_exibicao(id_repositorio)
    p = request.args.get("periodo", "").strip()
    if p in JANELAS_RESUMO:
        rotulo, _calc = JANELAS_RESUMO[p]
        inicio = _calc()
        janela = {"tipo": p, "rotulo": rotulo}
    else:
        p = None
        inicio, janela = inicio_padrao, janela_padrao
    serie = _serie_diaria(id_repositorio, inicio)
    with connection() as conn:
        autores = pd.read_sql(
            """
            SELECT autor, SUM(commits) AS commits
            FROM Metrica_Autor_Dia
            WHERE id_repositorio = %s AND dia >= %s
            GROUP BY autor ORDER BY commits DESC LIMIT 5
            """,
            conn, params=(id_repositorio, inicio),
        )
        if autores.empty and p is None:
            autores = pd.read_sql(
                """
                SELECT autor, SUM(commits) AS commits
                FROM Metrica_Autor_Mensal
                WHERE id_repositorio = %s AND mes >= %s
                GROUP BY autor ORDER BY commits DESC LIMIT 5
                """,
                conn, params=(id_repositorio, inicio),
            )

    metricas = _metricas_latest(id_repositorio)
    commits_total = int(serie["commits"].sum()) if not serie.empty else 0
    add_total = int(serie["add"].sum()) if not serie.empty else 0
    del_total = int(serie["del"].sum()) if not serie.empty else 0

    # score e curva falam do período coletado; o seletor recorta só a atividade
    serie_padrao = serie if p is None else _serie_diaria(id_repositorio, inicio_padrao)
    commits_score = (int(serie_padrao["commits"].sum())
                     if not serie_padrao.empty else 0)

    score = analises.score_sustentabilidade({
        "commits": commits_score if commits_score else None,
        "bus_factor": metricas["bus_factor"] if metricas else None,
        "ttfr": metricas["ttfr"] if metricas else None,
        "churn_relativo": metricas["churn_relativo"] if metricas else None,
    })

    coleta = status.snapshot()
    coletando = coleta["estado"] == "coletando" and (
        coleta.get("repo_atual") == repo["nome"]
    )

    return jsonify({
        "repo": repo,
        "coletando": coletando,
        "metricas": metricas,
        "janela": janela,
        "commits_total": commits_total,
        "linhas_add": add_total,
        "linhas_del": del_total,
        "serie": [
            {"dia": str(r.dia), "commits": int(r.commits)}
            for r in serie.itertuples()
        ],
        "autores_periodo": [
            {"autor": r.autor, "commits": int(r.commits)}
            for r in autores.itertuples()
        ],
        "autores_linhas": autores_linhas(id_repositorio),
        "curva": analises.curva_concentracao(id_repositorio, inicio_padrao),
        "historico_score": _historico_score(id_repositorio),
        "score": score,
    })


@app.route("/api/repo/<int:id_repositorio>/github")
@login_required
def api_repo_github(id_repositorio):
    """Dados ao vivo da API do GitHub: contribuidores e releases."""
    if not usuario_dono(current_user.id, id_repositorio):
        abort(403)
    repo = repositorio_por_id(id_repositorio)
    if not repo:
        abort(404)
    try:
        owner, nome_repo = _parse_owner_repo(repo["url"])
    except ValueError:
        return jsonify(erro="URL do repositório inválida."), 400
    token = _token_usuario()
    try:
        contribuidores = buscar_contribuidores(owner, nome_repo, token=token)
        releases = listar_releases(owner, nome_repo, token=token)
    except Exception as e:  # noqa: BLE001 - queremos repassar o erro ao cliente
        log.exception("Falha ao consultar GitHub para %s", repo["nome"])
        return jsonify(erro=f"Falha ao consultar a API do GitHub ({e.__class__.__name__})."), 502
    return jsonify({"contribuidores": contribuidores, "releases": releases})


@app.route("/repo/<int:id_repositorio>/relatorio")
@login_required
def repo_relatorio(id_repositorio):
    """Relatório .docx do repositório (score, métricas, gráficos)."""
    if not usuario_dono(current_user.id, id_repositorio):
        abort(403)
    dados = _dados_comparacao(id_repositorio)
    if not dados:
        abort(404)
    from relatorio import gerar_relatorio
    buf = gerar_relatorio(dados)
    nome = dados["repo"]["nome"].replace(" ", "-")
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"relatorio-{nome}.docx",
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".wordprocessingml.document",
    )


# ---------------------------------------------------------------------------
# Snapshot avaliável (tabela consolidada por período coletado)
# ---------------------------------------------------------------------------

def _parse_periodo(valor):
    """'2026-03-25|2026-09-25' -> (date, date) ou None."""
    if not valor or "|" not in valor:
        return None
    ini, fim = valor.split("|", 1)
    try:
        return (pd.Timestamp(ini).date(), pd.Timestamp(fim).date())
    except ValueError:
        return None


def _periodos_do_usuario(id_usuario):
    """Períodos coletados com métricas de repositórios do usuário."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ms.periodo_inicio, ms.periodo_fim
                FROM Metrica_Sustentabilidade ms
                JOIN Usuario_Repositorio ur ON ur.id_repositorio = ms.id_repositorio
                WHERE ur.id_usuario = %s
                ORDER BY ms.periodo_inicio DESC
                """,
                (id_usuario,),
            )
            return [(row[0].isoformat(), row[1].isoformat()) for row in cur.fetchall()]


def _linhas_snapshot(id_usuario, ini, fim):
    """Uma linha por repo do usuário, com as métricas fixas do período."""
    with connection() as conn:
        df = pd.read_sql(
            """
            SELECT r.nome, r.url, COALESCE(ur.nome_exibicao, r.nome) AS rotulo,
                   ms.bus_factor, ms.ttfr_mediano_dias, ms.churn_relativo,
                   ms.issues_abertas, ms.issues_fechadas, ms.cadencia_releases,
                   COALESCE((
                       SELECT SUM(d.commits) FROM Metrica_Diaria d
                       WHERE d.id_repositorio = r.id_repositorio
                         AND d.dia BETWEEN ms.periodo_inicio AND ms.periodo_fim
                   ), 0) AS commits
            FROM Usuario_Repositorio ur
            JOIN Repositorio r ON r.id_repositorio = ur.id_repositorio
            LEFT JOIN Metrica_Sustentabilidade ms
                   ON ms.id_repositorio = r.id_repositorio
                  AND ms.periodo_inicio = %s AND ms.periodo_fim = %s
            WHERE ur.id_usuario = %s
            ORDER BY r.nome
            """,
            conn, params=(ini, fim, id_usuario),
        )
    linhas = []
    for r in df.itertuples():
        s = analises.score_sustentabilidade({
            "commits": int(r.commits) if r.commits else None,
            "bus_factor": _inteiro(r.bus_factor),
            "ttfr": _num(r.ttfr_mediano_dias),
            "churn_relativo": _num(r.churn_relativo),
        })
        linhas.append({
            "rotulo": r.rotulo,
            "url": r.url,
            "commits": int(r.commits),
            "bus_factor": _inteiro(r.bus_factor),
            "ttfr": _num(r.ttfr_mediano_dias),
            "churn_relativo": _num(r.churn_relativo),
            "cadencia": _num(r.cadencia_releases),
            "issues_abertas": _inteiro(r.issues_abertas),
            "issues_fechadas": _inteiro(r.issues_fechadas),
            "score": s["score"],
        })
    return linhas


@app.route("/snapshot")
@login_required
def snapshot():
    periodos = _periodos_do_usuario(current_user.id)
    pedido = _parse_periodo(request.args.get("periodo"))
    linhas, selecionado = [], None
    if pedido:
        ini, fim = pedido
        selecionado = f"{ini.isoformat()}|{fim.isoformat()}"
        linhas = _linhas_snapshot(current_user.id, ini, fim)
    return render_template("snapshot.html", periodos=periodos,
                           selecionado=selecionado, linhas=linhas)


def _celula_csv(valor):
    """Neutraliza fórmulas no CSV exportado (=, +, -, @ viram texto seguro)."""
    texto = "" if valor is None else str(valor)
    if texto.startswith(("=", "+", "-", "@")):
        return "'" + texto
    return texto


@app.route("/snapshot.csv")
@login_required
def snapshot_csv():
    pedido = _parse_periodo(request.args.get("periodo"))
    if not pedido:
        abort(400)
    ini, fim = pedido
    saida = io.StringIO()
    w = csv.writer(saida)
    w.writerow(["repositorio", "url", "commits", "bus_factor", "ttfr_dias",
                "churn_relativo", "cadencia_releases_mes", "issues_abertas",
                "issues_fechadas", "score"])
    for l in _linhas_snapshot(current_user.id, ini, fim):
        w.writerow([_celula_csv(l["rotulo"]), _celula_csv(l["url"]),
                    _celula_csv(l["commits"]), _celula_csv(l["bus_factor"]),
                    _celula_csv(l["ttfr"]), _celula_csv(l["churn_relativo"]),
                    _celula_csv(l["cadencia"]), _celula_csv(l["issues_abertas"]),
                    _celula_csv(l["issues_fechadas"]), _celula_csv(l["score"])])
    return Response(
        saida.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=snapshot-{ini}-a-{fim}.csv"},
    )


# ---------------------------------------------------------------------------
# Coleta em background
# ---------------------------------------------------------------------------

_coleta_lock = threading.Lock()


def tarefa_mineracao(ids=None, token=None):
    """Executa os dois motores para `ids` (ou todos) e atualiza o status.

    Uma coleta por vez: quando outra está em andamento, a nova execução fica
    em espera numa thread e roda logo em seguida (em vez de ser descartada).
    Repos concluídos nos dois motores são marcados mesmo se a execução falhar
    depois.
    """
    if _coleta_lock.acquire(blocking=False):
        try:
            _tarefa_mineracao(ids, token)
        finally:
            _coleta_lock.release()
        return
    log.warning("Coleta em andamento — nova execução entrou em espera.")
    threading.Thread(target=_coleta_em_espera, args=(ids, token),
                     name="coleta-espera", daemon=True).start()


def _coleta_em_espera(ids=None, token=None):
    """Espera a coleta em andamento terminar (até 2h) e executa a sua."""
    if _coleta_lock.acquire(timeout=7200):
        try:
            _tarefa_mineracao(ids, token)
        finally:
            _coleta_lock.release()
    else:
        log.warning("Desistiu de esperar a coleta em andamento (timeout 2h).")


def _tarefa_mineracao(ids=None, token=None):
    rotulo = f"ids={ids}" if ids else "todos"
    log.info("Iniciando coleta (%s)", rotulo)
    status.atualizar(
        estado="coletando",
        etapa=None,
        repo_atual=None,
        repos_concluidos=0,
        total_repos=len(ids) if ids else 0,
        mensagem="Coleta em andamento.",
    )

    # janela calculada UMA vez: os dois motores gravam o mesmo período
    inicio = (pd.Timestamp.now() - pd.DateOffset(months=MESES_ANALISE)).replace(day=1).date()
    fim = pd.Timestamp.now().date()

    erros = []
    try:
        feitos_churn, falhas_churn = coletar_code_churn(ids, inicio=inicio,
                                                        fim=fim)
    except Exception:
        log.exception("Erro no Passo 2 (PyDriller)")
        erros.append("PyDriller")
        feitos_churn, falhas_churn = [], []
    try:
        feitos_gh, falhas_gh = coletar_metricas_sociais(ids, token=token,
                                                        inicio=inicio, fim=fim)
    except Exception:
        log.exception("Erro no Passo 3 (GitHub API)")
        erros.append("GitHub API")
        feitos_gh, falhas_gh = [], []

    # alvo = o que AMBOS os motores realmente coletaram (repos pulados por
    # um motor ficam pendentes e são reprocessados na próxima coleta)
    comuns = sorted(set(feitos_churn) & set(feitos_gh))
    falhas = sorted(set(falhas_churn) | set(falhas_gh))
    try:
        marcar_coletado(comuns)
    except Exception:
        log.exception("Falha ao registrar conclusão da coleta")

    if erros:
        mensagem = "Falha em: " + ", ".join(erros)
    elif falhas:
        mensagem = f"Coleta concluída — falhou em {len(falhas)} repositório(s)."
    else:
        mensagem = "Coleta concluída."
    status.atualizar(estado="erro" if erros else "concluido",
                     etapa=None, repo_atual=None, mensagem=mensagem)
    if falhas:
        log.warning("Repositórios com falha (ficam pendentes p/ retry): %s",
                    falhas)
    log.info("Coleta finalizada (%s)", rotulo)


def tarefa_agendada():
    """Coleta periódica: apenas os repositórios vinculados a algum usuário."""
    try:
        ids = repositorios_vinculados()
    except Exception:
        log.exception("Falha ao listar repositórios vinculados")
        return
    if not ids:
        status.atualizar(estado="concluido", etapa=None, repo_atual=None,
                         mensagem="Nenhum repositório vinculado.")
        return
    tarefa_mineracao(ids)


def iniciar_autocoleta():
    """Garante o schema e coleta os repositórios pendentes em background."""
    try:
        init_schema()
    except Exception:
        log.exception("Falha ao aplicar schema")
        status.atualizar(estado="erro", mensagem="Falha ao aplicar o schema do banco.")
        return
    try:
        pendentes = repositorios_pendentes()
    except Exception:
        log.exception("Falha ao verificar dados existentes")
        status.atualizar(estado="erro", mensagem="Falha ao consultar o banco.")
        return
    if pendentes:
        log.info("Repositórios pendentes: %s — coleta em background.", pendentes)
        status.atualizar(estado="coletando",
                         mensagem="Banco com dados pendentes — coleta iniciada.",
                         repos_concluidos=0, total_repos=len(pendentes))
        threading.Thread(target=tarefa_mineracao, args=(pendentes, None),
                         name="coleta-boot", daemon=True).start()
    else:
        status.atualizar(estado="concluido",
                         mensagem="Nenhum repositório pendente.",
                         repo_atual=None)


INTERVALO_DIAS = int(os.getenv("MINERACAO_INTERVALO_DIAS", "7"))
scheduler = BackgroundScheduler()
scheduler.add_job(
    tarefa_agendada,
    trigger=IntervalTrigger(days=INTERVALO_DIAS),
    id="mineracao",
    replace_existing=True,
)
scheduler.start()
if os.getenv("FAP_SEM_AUTOCOLETA") != "1":
    iniciar_autocoleta()


if __name__ == "__main__":
    from waitress import serve
    serve(app,
          host=os.getenv("FAP_HOST", "127.0.0.1"),
          port=int(os.getenv("PORT", "5000")))
