"""Motor de coleta com PyDriller: code churn por dia carregado em Metrica_Diaria."""
import logging
import os
import subprocess

import pandas as pd
from pydriller import Repository

from config import MESES_ANALISE, PROJ_ROOT
from database import (
    get_repositorios,
    insert_metrica_autor_mensal,
    insert_metrica_autor_linhas,
    insert_metrica_autor_dia,
    insert_metrica_diaria,
    insert_metrica_sustentabilidade_commits,
)
from collect.github_metrics import _parse_owner_repo
import status

log = logging.getLogger("fap.pydriller")

CLONE_DIR = os.path.join(PROJ_ROOT, "data", "repos")


def _caminho_local(url_repo: str):
    """Diretório do clone derivado da URL (owner__repo) — não colide entre
    repositórios de donos diferentes com o mesmo nome."""
    owner, nome = _parse_owner_repo(url_repo)
    return os.path.join(CLONE_DIR, f"{owner}__{nome}")


def _repo_local(url_repo: str):
    """Garante o clone atualizado e retorna o caminho local.

    Clona se não existe; senão fetch + reset (o PyDriller não puxa
    atualizações de clones existentes — sem o refresh, a re-coleta usaria
    commits velhos).
    """
    caminho = _caminho_local(url_repo)
    if os.path.isdir(os.path.join(caminho, ".git")):
        subprocess.run(["git", "-C", caminho, "fetch", "--all", "--prune",
                        "--quiet"], check=True, capture_output=True, timeout=600)
        # Atualiza a referencia do HEAD remoto (necessario quando a branch
        # default foi renomeada, ex.: master -> main, para o reset nao falhar).
        subprocess.run(["git", "-C", caminho, "remote", "set-head", "origin", "-a"],
                       check=True, capture_output=True, timeout=600)
        subprocess.run(["git", "-C", caminho, "reset", "--hard", "origin/HEAD",
                        "--quiet"], check=True, capture_output=True, timeout=600)
        subprocess.run(["git", "-C", caminho, "clean", "-fd", "--quiet"],
                       check=True, capture_output=True, timeout=600)
    else:
        os.makedirs(CLONE_DIR, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", url_repo, caminho],
                       check=True, capture_output=True, timeout=1800)
    return caminho


def _eh_bot_nome(nome):
    """Identifica autores automatizados pelo nome (ex.: dependabot[bot]).

    Cobre a convenção de Apps do GitHub ([bot]) e padrões comuns de bots.
    Contas automatizadas não representam pessoas e distorceriam Bus Factor,
    curva de concentração e contagens de commits.
    """
    nome = (nome or "").lower().strip()
    return (
        nome.endswith("[bot]")
        or nome.endswith("-bot")
        or "dependabot" in nome
        or "github-actions" in nome
        or "renovate" in nome
    )


def coletar_commits(caminho_repo: str, since=None):
    """Retorna DataFrame com commits de autores humanos: dia, autor, linhas.

    Commits de contas automatizadas (dependabot[bot] etc.) são excluídos.
    """
    if since is None:
        since = pd.Timestamp.now() - pd.DateOffset(months=MESES_ANALISE)
    registros = []
    bots = 0
    for commit in Repository(caminho_repo, since=since).traverse_commits():
        if _eh_bot_nome(commit.author.name):
            bots += 1
            continue
        added = sum(m.added_lines for m in commit.modified_files)
        deleted = sum(m.deleted_lines for m in commit.modified_files)
        registros.append(
            {
                "dia": commit.committer_date.date(),
                "autor": commit.author.name,
                "lines_added": added,
                "lines_deleted": deleted,
            }
        )
    if bots:
        log.info("%d commits de bots filtrados em %s", bots, caminho_repo)
    return pd.DataFrame(registros)


def agregar_por_dia(df: pd.DataFrame, id_repositorio: int) -> pd.DataFrame:
    """Agrupa commits por dia e calcula as métricas agregadas."""
    if df.empty:
        return pd.DataFrame(
            columns=[
                "id_repositorio", "dia", "commits",
                "autores_distintos", "lines_added", "lines_deleted",
            ]
        )
    g = df.groupby("dia").agg(
        commits=("autor", "count"),
        autores_distintos=("autor", "nunique"),
        lines_added=("lines_added", "sum"),
        lines_deleted=("lines_deleted", "sum"),
    ).reset_index()
    g.insert(0, "id_repositorio", id_repositorio)
    return g


def agregar_por_mes_autor(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa commits por mês (primeiro dia) e autor — base da curva de concentração."""
    if df.empty:
        return pd.DataFrame(columns=["mes", "autor", "commits"])
    tmp = df.copy()
    tmp["mes"] = pd.to_datetime(tmp["dia"]).dt.to_period("M").dt.start_time.dt.date
    g = tmp.groupby(["mes", "autor"]).size().reset_index(name="commits")
    return g


def agregar_por_dia_autor(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa commits por dia e autor — visão "quem comitou naquele dia"."""
    if df.empty:
        return pd.DataFrame(columns=["dia", "autor", "commits"])
    g = df.groupby(["dia", "autor"]).size().reset_index(name="commits")
    return g


def agregar_por_autor_linhas(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega por autor: commits, linhas adicionadas, removidas e reescritas.

    Linhas "reescritas" aproximam o código modificado nas duas direções no
    mesmo commit (min de added/deleted por commit) — visão macro do gestor.
    """
    if df.empty:
        return pd.DataFrame(columns=["autor", "commits", "linhas_adicionadas",
                                     "linhas_removidas", "linhas_reescritas"])
    tmp = df.copy()
    tmp["reescritas"] = tmp[["lines_added", "lines_deleted"]].min(axis=1)
    g = tmp.groupby("autor").agg(
        commits=("autor", "count"),
        linhas_adicionadas=("lines_added", "sum"),
        linhas_removidas=("lines_deleted", "sum"),
        linhas_reescritas=("reescritas", "sum"),
    ).reset_index()
    return g


def calcular_bus_factor(df: pd.DataFrame):
    """Menor k tal que a soma das k maiores contribuições > 50% do total."""
    if df.empty:
        return None
    contagens = df["autor"].value_counts().sort_values(ascending=False)
    total = contagens.sum()
    acumulado, k = 0, 0
    for c in contagens:
        acumulado += c
        k += 1
        if acumulado > 0.5 * total:
            break
    return k


EXTENSOES_BINARIAS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".avif",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".gz", ".tgz", ".7z", ".rar",
    ".jar", ".class", ".so", ".dll", ".exe", ".dylib",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
}


def contar_loc(caminho_repo: str):
    """Conta linhas dos arquivos rastreados pelo git (qualquer linguagem).

    Arquivos binários são ignorados.
    """
    try:
        saida = subprocess.run(
            ["git", "-C", caminho_repo, "ls-files"],
            capture_output=True, text=True, timeout=120,
        )
        arquivos = [a for a in saida.stdout.splitlines() if a.strip()]
        if not arquivos:
            return 0
        total = 0
        for a in arquivos:
            if os.path.splitext(a)[1].lower() in EXTENSOES_BINARIAS:
                continue
            try:
                with open(os.path.join(caminho_repo, a), "r", errors="ignore") as f:
                    total += sum(1 for _ in f)
            except OSError:
                continue
        return total
    except Exception:
        log.exception("Falha ao contar LOC para %s", caminho_repo)
        return 0


def calcular_churn_relativo(df: pd.DataFrame, caminho_repo: str):
    """Razão entre o churn acumulado no período e o LOC atual do repositório."""
    if df.empty:
        return None
    churn = df["lines_added"].sum() + df["lines_deleted"].sum()
    loc = contar_loc(caminho_repo)
    return (churn / loc) if loc else None


def executar(ids=None, inicio=None, fim=None):
    """Coleta PyDriller para todos os repositórios ou apenas os informados em `ids`.

    inicio/fim (dates): janela do período compartilhada com o outro motor —
    o orquestrador passa os MESMOS valores para os dois, garantindo linhas
    casadas no banco. None = calcula a janela corrente.
    Retorna (feitos, falhas): ids coletados e nomes dos repos que falharam
    (um repo ruim não derruba mais o lote).
    """
    repos = get_repositorios(ids)
    if repos.empty:
        log.warning("Nenhum repositório para coletar (ids=%s).", ids)
        return [], []

    os.makedirs(CLONE_DIR, exist_ok=True)

    if inicio is None:
        inicio = (pd.Timestamp.now() - pd.DateOffset(months=MESES_ANALISE)).replace(day=1).date()
    if fim is None:
        fim = pd.Timestamp.now().date()
    since = pd.Timestamp(inicio)

    metricas_periodo = []
    feitos, falhas = [], []
    total = len(repos)
    status.atualizar(
        estado="coletando",
        etapa="pydriller",
        total_repos=total,
        repos_concluidos=0,
        repo_atual=None,
        mensagem="Analisando commits com PyDriller.",
    )
    for i, repo in enumerate(repos.itertuples()):
        try:
            log.info("Clonando/analisando: %s", repo.url)
            status.atualizar(repo_atual=repo.nome, repos_concluidos=i)
            caminho = _repo_local(repo.url)
            df = coletar_commits(caminho, since=since)
            agregado = agregar_por_dia(df, repo.id_repositorio)

            log.info("%d commits extraídos para %s", len(df), repo.nome)

            if not agregado.empty:
                insert_metrica_diaria(agregado)

            por_mes = agregar_por_mes_autor(df)
            insert_metrica_autor_mensal(por_mes, repo.id_repositorio, inicio)

            por_autor = agregar_por_autor_linhas(df)
            insert_metrica_autor_linhas(por_autor, repo.id_repositorio)

            por_dia_autor = agregar_por_dia_autor(df)
            insert_metrica_autor_dia(por_dia_autor, repo.id_repositorio)

            metricas_periodo.append(
                {
                    "id_repositorio": repo.id_repositorio,
                    "periodo_inicio": inicio,
                    "periodo_fim": fim,
                    "bus_factor": calcular_bus_factor(df),
                    "churn_relativo": calcular_churn_relativo(df, caminho),
                }
            )
            feitos.append(repo.id_repositorio)
        except Exception:
            log.exception("Falha ao coletar commits de %s", repo.nome)
            falhas.append(repo.nome)
        finally:
            status.atualizar(repos_concluidos=i + 1)

    insert_metrica_sustentabilidade_commits(pd.DataFrame(metricas_periodo))
    log.info("Passo 2 concluído (%d ok, %d falhas).", len(feitos), len(falhas))
    return feitos, falhas


if __name__ == "__main__":
    executar()