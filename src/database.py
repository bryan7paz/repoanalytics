"""Conexão com o PostgreSQL e helpers de carga (ETL)."""
import base64
import hashlib
import logging
import math
import os
import threading
from contextlib import contextmanager

import pandas as pd
from psycopg2.pool import ThreadedConnectionPool
from cryptography.fernet import Fernet, InvalidToken

from config import DB_CONFIG, MESES_ANALISE, PROJ_ROOT

log = logging.getLogger("fap.db")

_pool = None
_pool_lock = threading.Lock()

_fernet = None


def _chave_token():
    """Fernet key derivada de SESSION_SECRET (cacheada por processo)."""
    global _fernet
    if _fernet is None:
        segredo = os.getenv("SESSION_SECRET") or "fap-local"
        digesto = hashlib.pbkdf2_hmac("sha256", segredo.encode(),
                                      b"fap-token-v1", 200_000)
        _fernet = Fernet(base64.urlsafe_b64encode(digesto))
    return _fernet


def cifrar_token(token):
    """Cifra o token OAuth do usuário antes de persistir.

    Sem SESSION_SECRET não cifra (chave fallback fraca) - o token não é
    armazenado e a coleta usa o token do sistema.
    """
    if not token:
        return None
    if not os.getenv("SESSION_SECRET"):
        log.warning("SESSION_SECRET ausente - token do usuário não será armazenado.")
        return None
    return _chave_token().encrypt(token.encode()).decode()


def decifrar_token(valor):
    """Lê o token cifrado; tolera plaintext legado e blob órfão de outra chave."""
    if not valor:
        return None
    try:
        return _chave_token().decrypt(valor.encode()).decode()
    except InvalidToken:
        if valor.startswith("gAAAAA"):
            return None  # cifrado com outra SESSION_SECRET - usa o token do sistema
        return valor  # gravação legada em plaintext (antes da criptografia)


def init_schema():
    """Executa sql/schema.sql (idempotente) + migrações para bancos antigos."""
    schema_path = PROJ_ROOT / "sql" / "schema.sql"
    with open(schema_path, encoding="utf-8") as f:
        sql = f.read()
    migracoes = [
        # bancos criados antes do pivot: remove o modelo antigo de frameworks
        "ALTER TABLE Repositorio DROP COLUMN IF EXISTS id_framework",
        "DROP TABLE IF EXISTS Framework",
        "ALTER TABLE Repositorio ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMP",
        # coluna nunca preenchida (resquício do modelo antigo)
        "ALTER TABLE Repositorio DROP COLUMN IF EXISTS estrelas",
        # repos de donos diferentes podem ter o mesmo nome: identidade pela URL
        "ALTER TABLE Repositorio DROP CONSTRAINT IF EXISTS repositorio_nome_key",
        # o valor é a mediana do TTFR - nome antigo divergia da implementação
        """DO $ren$ BEGIN
             IF EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'metrica_sustentabilidade'
                          AND column_name = 'ttfr_medio_dias') THEN
               ALTER TABLE Metrica_Sustentabilidade
                 RENAME COLUMN ttfr_medio_dias TO ttfr_mediano_dias;
             END IF;
           END $ren$;""",
    ]
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            for m in migracoes:
                cur.execute(m)
    log.info("Schema aplicado (%s).", schema_path)


def repositorios_pendentes():
    """Ids dos repositórios vinculados a usuários ainda sem coleta concluída."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT r.id_repositorio
                FROM Repositorio r
                JOIN Usuario_Repositorio ur USING (id_repositorio)
                WHERE r.atualizado_em IS NULL
                ORDER BY r.id_repositorio
                """
            )
            return [row[0] for row in cur.fetchall()]


def marcar_coletado(id_repositorios):
    """Registra a conclusão da coleta para os repositórios informados."""
    if not id_repositorios:
        return
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE Repositorio SET atualizado_em = CURRENT_TIMESTAMP "
                "WHERE id_repositorio IN %s",
                (tuple(id_repositorios),),
            )


def renomear_exibicao(id_usuario, id_repositorio, nome_exibicao):
    """Atualiza o nome de exibição do repositório para o usuário."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE Usuario_Repositorio SET nome_exibicao = %s "
                "WHERE id_usuario = %s AND id_repositorio = %s",
                (nome_exibicao, id_usuario, id_repositorio),
            )


def atualizar_url_repo(id_repositorio, url, nome):
    """Atualiza a URL canônica (e o nome) do repositório no banco."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE Repositorio SET url = %s, nome = %s "
                "WHERE id_repositorio = %s",
                (url, nome, id_repositorio),
            )


def marcar_pendente(id_repositorio):
    """Marca o repositório como pendente (dispara re-coleta no próximo ciclo)."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE Repositorio SET atualizado_em = NULL "
                "WHERE id_repositorio = %s",
                (id_repositorio,),
            )


def resumo_repos_usuario(id_repositorios):
    """Resumo por repositório (visão macro do gestor): commits na janela,
    autores distintos e últimos indicadores sociais (para o score).

    Retorna dict {id_repositorio: {"commits": int, "autores": int,
    "ttfr": float|None, "bus_factor": int|None, "churn_relativo": float|None}}.
    """
    if not id_repositorios:
        return {}
    inicio = (pd.Timestamp.now() - pd.DateOffset(months=MESES_ANALISE)) \
        .replace(day=1).date()
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_repositorio, SUM(commits)
                FROM Metrica_Diaria
                WHERE id_repositorio IN %s AND dia >= %s
                GROUP BY id_repositorio
                """,
                (tuple(id_repositorios), inicio),
            )
            commits = {row[0]: int(row[1] or 0) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT id_repositorio, COUNT(DISTINCT autor)
                FROM Metrica_Autor_Mensal
                WHERE id_repositorio IN %s AND mes >= %s
                GROUP BY id_repositorio
                """,
                (tuple(id_repositorios), inicio.replace(day=1)),
            )
            autores = {row[0]: int(row[1]) for row in cur.fetchall()}
            cur.execute(
                """
                SELECT DISTINCT ON (id_repositorio)
                       id_repositorio, ttfr_mediano_dias, bus_factor,
                       churn_relativo
                FROM Metrica_Sustentabilidade
                WHERE id_repositorio IN %s
                ORDER BY id_repositorio, periodo_fim DESC
                """,
                (tuple(id_repositorios),),
            )
            sociais = {row[0]: row for row in cur.fetchall()}
    resumo = {}
    for rid in id_repositorios:
        s = sociais.get(rid)
        resumo[rid] = {
            "commits": commits.get(rid, 0),
            "autores": autores.get(rid, 0),
            "ttfr": float(s[1]) if s and s[1] is not None else None,
            "bus_factor": int(s[2]) if s and s[2] is not None else None,
            "churn_relativo": float(s[3]) if s and s[3] is not None else None,
        }
    return resumo


def _clean(valor):
    """Converte valores inválidos (NaN) em None para gravação no banco."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    return valor


def _health_check(conn):
    """Ping simples para validar que a conexão ainda está viva."""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:
        return False


def get_connection():
    """Conexão do pool (thread-safe, criado na primeira chamada)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(1, 10, **DB_CONFIG)
    for _ in range(3):
        conn = _pool.getconn()
        if _health_check(conn):
            return conn
        log.warning("Conexão do pool falhou health-check, descartando.")
        try:
            conn.close()
        except Exception:
            pass
    # Se chegou aqui, todas as tentativas falharam - tenta recriar o pool
    log.error("Todas as tentativas de conexão saudável falharam - recriando pool.")
    with _pool_lock:
        _pool = ThreadedConnectionPool(1, 10, **DB_CONFIG)
    return _pool.getconn()


def _devolver_connection(conn):
    try:
        _pool.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _devolver_connection(conn)


# ---------------------------------------------------------------------------
# Usuários (OAuth GitHub)
# ---------------------------------------------------------------------------

def upsert_usuario(github_id, login, nome=None, avatar_url=None, access_token=None):
    """Cria ou atualiza o usuário pelo github_id e retorna o id_usuario."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO Usuario (github_id, login, nome, avatar_url, access_token)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (github_id) DO UPDATE SET
                    login = EXCLUDED.login,
                    nome = EXCLUDED.nome,
                    avatar_url = EXCLUDED.avatar_url,
                    access_token = EXCLUDED.access_token
                RETURNING id_usuario
                """,
                (github_id, login, nome, avatar_url, cifrar_token(access_token)),
            )
            return cur.fetchone()[0]


def buscar_usuario(id_usuario):
    """Dict do usuário (com o access_token decifrado) ou None."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_usuario, github_id, login, nome, avatar_url, access_token "
                "FROM Usuario WHERE id_usuario = %s",
                (id_usuario,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = ["id_usuario", "github_id", "login", "nome", "avatar_url",
                    "access_token"]
            dados = dict(zip(cols, row))
            dados["access_token"] = decifrar_token(dados["access_token"])
            return dados


# ---------------------------------------------------------------------------
# Repositórios e vínculo com usuário
# ---------------------------------------------------------------------------

def upsert_repositorio(nome, url):
    """Garante que o repositório existe (chave: URL canônica) e retorna o id."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO Repositorio (nome, url) VALUES (%s, %s)
                ON CONFLICT (url) DO UPDATE SET nome = EXCLUDED.nome
                RETURNING id_repositorio
                """,
                (nome, url),
            )
            return cur.fetchone()[0]


def link_usuario_repositorio(id_usuario, id_repositorio, nome_exibicao=None):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO Usuario_Repositorio (id_usuario, id_repositorio, nome_exibicao)
                VALUES (%s, %s, %s)
                ON CONFLICT (id_usuario, id_repositorio)
                DO UPDATE SET nome_exibicao = EXCLUDED.nome_exibicao
                """,
                (id_usuario, id_repositorio, nome_exibicao),
            )


def desvincular_e_limpar(id_usuario, id_repositorio):
    """Remove o vínculo do usuário; se não sobrar dono, apaga o repo.

    A exclusão do Repositorio propaga (cascade) para todas as métricas.
    Retorna True se o repositório foi apagado.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM Usuario_Repositorio "
                "WHERE id_usuario = %s AND id_repositorio = %s",
                (id_usuario, id_repositorio),
            )
            cur.execute(
                "SELECT 1 FROM Usuario_Repositorio WHERE id_repositorio = %s",
                (id_repositorio,),
            )
            if cur.fetchone() is not None:
                return False
            cur.execute(
                "DELETE FROM Repositorio WHERE id_repositorio = %s",
                (id_repositorio,),
            )
            return True


def repositorios_vinculados():
    """Ids dos repositórios vinculados a pelo menos um usuário (coleta periódica)."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT r.id_repositorio
                FROM Repositorio r
                JOIN Usuario_Repositorio ur USING (id_repositorio)
                ORDER BY r.id_repositorio
                """
            )
            return [row[0] for row in cur.fetchall()]


def repositorios_do_usuario(id_usuario):
    """Lista os repositórios do usuário com status de coleta."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id_repositorio, r.nome, r.url, ur.nome_exibicao,
                       r.atualizado_em,
                       (r.atualizado_em IS NOT NULL) AS coletado
                FROM Usuario_Repositorio ur
                JOIN Repositorio r ON r.id_repositorio = ur.id_repositorio
                WHERE ur.id_usuario = %s
                ORDER BY ur.criado_em DESC
                """,
                (id_usuario,),
            )
            cols = ["id_repositorio", "nome", "url", "nome_exibicao",
                    "atualizado_em", "coletado"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def usuario_dono(id_usuario, id_repositorio):
    """True se o usuário acompanha esse repositório."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM Usuario_Repositorio "
                "WHERE id_usuario = %s AND id_repositorio = %s",
                (id_usuario, id_repositorio),
            )
            return cur.fetchone() is not None


def autores_linhas(id_repositorio, limite=5):
    """Top autores com estatísticas de linhas (visão macro do gestor).

    Retorna lista ordenada por commits desc: [{"autor", "commits",
    "linhas_adicionadas", "linhas_removidas", "linhas_reescritas"}, ...]
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT autor, commits, linhas_adicionadas, linhas_removidas,
                       linhas_reescritas
                FROM Metrica_Autor_Linhas
                WHERE id_repositorio = %s
                ORDER BY commits DESC, linhas_adicionadas DESC
                LIMIT %s
                """,
                (id_repositorio, limite),
            )
            cols = ["autor", "commits", "linhas_adicionadas",
                    "linhas_removidas", "linhas_reescritas"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def repositorio_por_id(id_repositorio):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id_repositorio, nome, url FROM Repositorio "
                "WHERE id_repositorio = %s",
                (id_repositorio,),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = ["id_repositorio", "nome", "url"]
            return dict(zip(cols, row))


def ultimo_periodo(id_repositorio):
    """(periodo_inicio, periodo_fim) da última coleta do repo, ou None."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT periodo_inicio, periodo_fim FROM Metrica_Sustentabilidade "
                "WHERE id_repositorio = %s "
                "ORDER BY periodo_inicio DESC LIMIT 1",
                (id_repositorio,),
            )
            return cur.fetchone()


# ---------------------------------------------------------------------------
# Métricas (ETL)
# ---------------------------------------------------------------------------

def insert_metrica_diaria(df: pd.DataFrame, id_repositorio: int,
                          inicio, fim):
    """Substitui a série diária de um repositório na janela (limpa e recarrega).

    Dias da janela sem commits perdem as linhas antigas - evita que valores de
    coletas anteriores (ex.: pré-filtro de bots) sobrevivam à re-coleta.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM Metrica_Diaria "
                "WHERE id_repositorio = %s AND dia BETWEEN %s AND %s",
                (id_repositorio, inicio, fim),
            )
            if df.empty:
                log.info(
                    "Metrica_Diaria: janela %s..%s do repo %d sem commits.",
                    inicio, fim, id_repositorio,
                )
                return
            sql = """
                INSERT INTO Metrica_Diaria
                    (id_repositorio, dia, commits, autores_distintos,
                     lines_added, lines_deleted)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id_repositorio, dia) DO UPDATE SET
                    commits = EXCLUDED.commits,
                    autores_distintos = EXCLUDED.autores_distintos,
                    lines_added = EXCLUDED.lines_added,
                    lines_deleted = EXCLUDED.lines_deleted,
                    atualizado_em = CURRENT_TIMESTAMP
            """
            rows = [
                (
                    id_repositorio, r.dia, r.commits, r.autores_distintos,
                    r.lines_added, r.lines_deleted,
                )
                for r in df.itertuples()
            ]
            cur.executemany(sql, rows)
    log.info("%d linhas inseridas/atualizadas em Metrica_Diaria.", len(rows))


def insert_metrica_autor_mensal(df: pd.DataFrame, id_repositorio: int,
                                mes_inicio):
    """Substitui a série mensal por autor de um repositório (limpa e recarrega).

    Espera colunas: mes (date), autor, commits.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM Metrica_Autor_Mensal "
                "WHERE id_repositorio = %s AND mes >= %s",
                (id_repositorio, mes_inicio),
            )
            if not df.empty:
                cur.executemany(
                    """
                    INSERT INTO Metrica_Autor_Mensal
                        (id_repositorio, mes, autor, commits)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id_repositorio, mes, autor)
                    DO UPDATE SET commits = EXCLUDED.commits
                    """,
                    [(id_repositorio, r.mes, r.autor, r.commits)
                     for r in df.itertuples()],
                )
    log.info("Metrica_Autor_Mensal atualizada para o repo %d.", id_repositorio)


def insert_metrica_autor_linhas(df: pd.DataFrame, id_repositorio: int):
    """Substitui as estatísticas por autor de um repositório (limpa e recarrega).

    Espera colunas: autor, commits, linhas_adicionadas, linhas_removidas,
    linhas_reescritas.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM Metrica_Autor_Linhas WHERE id_repositorio = %s",
                (id_repositorio,),
            )
            if not df.empty:
                cur.executemany(
                    """
                    INSERT INTO Metrica_Autor_Linhas
                        (id_repositorio, autor, commits, linhas_adicionadas,
                         linhas_removidas, linhas_reescritas)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id_repositorio, autor)
                    DO UPDATE SET commits = EXCLUDED.commits,
                        linhas_adicionadas = EXCLUDED.linhas_adicionadas,
                        linhas_removidas = EXCLUDED.linhas_removidas,
                        linhas_reescritas = EXCLUDED.linhas_reescritas
                    """,
                    [(id_repositorio, r.autor, int(r.commits),
                      int(r.linhas_adicionadas), int(r.linhas_removidas),
                      int(r.linhas_reescritas))
                     for r in df.itertuples()],
                )
    log.info("Metrica_Autor_Linhas atualizada para o repo %d.", id_repositorio)


def insert_metrica_autor_dia(df: pd.DataFrame, id_repositorio: int):
    """Substitui a série diária por autor de um repositório (limpa e recarrega).

    Espera colunas: dia (date), autor, commits.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM Metrica_Autor_Dia WHERE id_repositorio = %s",
                (id_repositorio,),
            )
            if not df.empty:
                cur.executemany(
                    """
                    INSERT INTO Metrica_Autor_Dia
                        (id_repositorio, dia, autor, commits)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id_repositorio, dia, autor)
                    DO UPDATE SET commits = EXCLUDED.commits
                    """,
                    [(id_repositorio, r.dia, r.autor, r.commits)
                     for r in df.itertuples()],
                )
    log.info("Metrica_Autor_Dia atualizada para o repo %d.", id_repositorio)


def insert_metrica_sustentabilidade(df: pd.DataFrame):
    """Insere métricas sociais (TTFR, issues, contribuidores, cadência)."""
    if df.empty:
        return
    sql = """
        INSERT INTO Metrica_Sustentabilidade
            (id_repositorio, periodo_inicio, periodo_fim,
             ttfr_mediano_dias, issues_abertas, issues_fechadas,
             contribuidores_ativos, cadencia_releases)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id_repositorio, periodo_inicio, periodo_fim)
        DO UPDATE SET
            ttfr_mediano_dias = EXCLUDED.ttfr_mediano_dias,
            issues_abertas = EXCLUDED.issues_abertas,
            issues_fechadas = EXCLUDED.issues_fechadas,
            contribuidores_ativos = EXCLUDED.contribuidores_ativos,
            cadencia_releases = EXCLUDED.cadencia_releases,
            atualizado_em = CURRENT_TIMESTAMP
    """
    rows = [
        (
            r.id_repositorio, r.periodo_inicio, r.periodo_fim,
            _clean(r.ttfr_mediano_dias), r.issues_abertas, r.issues_fechadas,
            r.contribuidores_ativos, _clean(r.cadencia_releases),
        )
        for r in df.itertuples()
    ]
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    log.info("%d linhas inseridas/atualizadas em Metrica_Sustentabilidade.", len(rows))


def insert_metrica_sustentabilidade_commits(df: pd.DataFrame):
    """Insere métricas derivadas dos commits (bus_factor, churn_relativo)."""
    if df.empty:
        return
    sql = """
        INSERT INTO Metrica_Sustentabilidade
            (id_repositorio, periodo_inicio, periodo_fim,
             bus_factor, churn_relativo)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id_repositorio, periodo_inicio, periodo_fim)
        DO UPDATE SET
            bus_factor = EXCLUDED.bus_factor,
            churn_relativo = EXCLUDED.churn_relativo,
            atualizado_em = CURRENT_TIMESTAMP
    """
    rows = [
        (
            r.id_repositorio, r.periodo_inicio, r.periodo_fim,
            _clean(r.bus_factor), _clean(r.churn_relativo),
        )
        for r in df.itertuples()
    ]
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
    log.info("%d linhas inseridas/atualizadas em Metrica_Sustentabilidade (commits).", len(rows))


def get_repositorios(ids=None):
    """Retorna DataFrame com id_repositorio, nome, url.

    ids: lista opcional para restringir a coleta a alguns repositórios.
    """
    sql = (
        "SELECT id_repositorio, nome, url "
        "FROM Repositorio ORDER BY nome"
    )
    params = None
    if ids:
        sql = (
            "SELECT id_repositorio, nome, url "
            "FROM Repositorio WHERE id_repositorio IN %s ORDER BY nome"
        )
        params = (tuple(ids),)
    with connection() as conn:
        return pd.read_sql(sql, conn, params=params)
