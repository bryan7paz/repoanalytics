"""Instalação rápida da FAP: automata o que dá e imprime o que falta.

Uso (não precisa de venv — ele mesmo cria):
    python setup.py
"""
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
VENV = RAIZ / "fap_env"
VENV_PY = VENV / ("Scripts" if os.name == "nt" else "bin") / "python.exe"
ENV_EXEMPLO = RAIZ / ".env.example"
ENV = RAIZ / ".env"

pendentes = []


def _ok(msg):
    print(f"  [ok] {msg}")


def _pendente(msg):
    print(f"  [!!] {msg}")
    pendentes.append(msg)


def _rodar(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _porta_aberta(porta=5432, timeout=2):
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=timeout):
            return True
    except OSError:
        return False


def _ler_env():
    dados = {}
    if ENV.exists():
        for linha in ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if "=" in linha and not linha.startswith("#"):
                chave, valor = linha.split("=", 1)
                dados[chave.strip()] = valor.strip()
    return dados


def _token_util(valor):
    """Token só conta se existir e não for o placeholder do .env.example."""
    return bool(valor and valor.strip() != "coloque_seu_token_aqui")


def passo_python():
    print("\n1) Python")
    v = sys.version_info[:2]
    if (3, 9) <= v <= (3, 13):
        _ok(f"Python {v[0]}.{v[1]} suportado")
    else:
        _pendente(f"Python {v[0]}.{v[1]} detectado — o projeto é testado no "
                  "3.12/3.13; se o app não iniciar, instale o 3.13 "
                  "(https://www.python.org/downloads/), apague a pasta fap_env "
                  "e rode este script de novo")


def passo_venv():
    print("\n2) Ambiente virtual")
    if VENV_PY.exists():
        atual = "%d.%d" % sys.version_info[:2]
        r = _rodar([str(VENV_PY), "-c",
                    "import sys; print('%d.%d' % sys.version_info[:2])"])
        versao_venv = (r.stdout or "").strip()
        if r.returncode == 0 and versao_venv and versao_venv != atual:
            _pendente(f"venv existe mas foi criado com o Python {versao_venv} "
                      f"(este script está rodando no {atual}) — apague a pasta "
                      "fap_env e rode este script de novo")
        else:
            _ok(f"venv já existe ({VENV.name})")
        return
    r = _rodar([sys.executable, "-m", "venv", str(VENV)])
    if r.returncode == 0:
        _ok("venv criado")
    else:
        _pendente("criar o venv manualmente: python -m venv fap_env")


def passo_deps():
    print("\n3) Dependências")
    if not VENV_PY.exists():
        return
    print("   instalando (pode demorar na 1ª vez)...")
    r = _rodar([str(VENV_PY), "-m", "pip", "install", "-r",
                str(RAIZ / "requirements.txt"), "-q"])
    if r.returncode == 0:
        _ok("dependências instaladas")
    else:
        _pendente("pip install falhou: " + (r.stderr or "").strip()[:200])


def passo_git():
    print("\n4) Git (obrigatório para o PyDriller)")
    if shutil.which("git"):
        _ok("git encontrado")
    else:
        _pendente("git não encontrado — instale em https://git-scm.com/download/win")


def passo_env():
    print("\n5) Arquivo .env")
    if not ENV.exists():
        if ENV_EXEMPLO.exists():
            shutil.copy(ENV_EXEMPLO, ENV)
            _ok(".env criado a partir do .env.example")
        else:
            ENV.write_text("", encoding="utf-8")
            _ok(".env vazio criado")
    env = _ler_env()
    if env.get("SESSION_SECRET"):
        _ok("SESSION_SECRET configurado")
    else:
        with open(ENV, "a", encoding="utf-8") as f:
            f.write(f"SESSION_SECRET={secrets.token_hex(32)}\n")
        _ok("SESSION_SECRET gerado e salvo no .env")
    env = _ler_env()
    if _token_util(env.get("GITHUB_TOKEN")):
        _ok("GITHUB_TOKEN configurado")
    else:
        _pendente("GITHUB_TOKEN ausente ou ainda no placeholder — sem token a "
                  "coleta de TTFR não fecha (60 req/h). Gere em "
                  "https://github.com/settings/tokens e cole no .env")


def passo_postgres():
    print("\n6) PostgreSQL")
    if _porta_aberta():
        _ok("PostgreSQL já está rodando (porta 5432)")
        return
    ctl = None
    for v in ("16", "17", "15"):
        candidato = Path("C:/Program Files/PostgreSQL") / v / "bin" / "pg_ctl.exe"
        if candidato.exists():
            ctl = candidato
            break
    pgdata = None
    for candidato in (Path.home() / "pgdata", Path("C:/PostgreSQL/16/data")):
        if (candidato / "PG_VERSION").exists():
            pgdata = candidato
            break
    if not (ctl and pgdata):
        _pendente("PostgreSQL parado e sem instalação padrão encontrada — "
                  "instale em https://www.postgresql.org/download/windows/ "
                  "ou suba com: pg_ctl -D <caminho-do-cluster> start")
        return
    print(f"   subindo ({pgdata})...")
    _rodar([str(ctl), "-D", str(pgdata), "-l", str(RAIZ / "pg_startup.log"),
            "start"])
    if _porta_aberta():
        _ok("PostgreSQL iniciado")
    else:
        _pendente("falha ao subir o PostgreSQL — suba manualmente")


def passo_banco():
    print("\n7) Banco de dados 'fap'")
    if not _porta_aberta() or not VENV_PY.exists():
        return
    script = (
        "import os, psycopg2\n"
        "c = psycopg2.connect(host=os.getenv('DB_HOST','localhost'),"
        " port=int(os.getenv('DB_PORT','5432')), "
        "user=os.getenv('DB_USER','postgres'),"
        " password=os.getenv('DB_PASSWORD',''), dbname='postgres',"
        " connect_timeout=5)\n"
        "c.autocommit = True\n"
        "cur = c.cursor()\n"
        "cur.execute(\"SELECT 1 FROM pg_database WHERE datname='fap'\")\n"
        "if cur.fetchone() is None:\n"
        "    cur.execute('CREATE DATABASE fap')\n"
        "    print('banco fap criado')\n"
        "else:\n"
        "    print('banco fap ja existe')\n"
        "c.close()\n"
    )
    r = _rodar([str(VENV_PY), "-c", script], cwd=str(RAIZ))
    if r.returncode == 0:
        _ok((r.stdout or "banco pronto").strip())
    else:
        _pendente("falha ao criar/verificar o banco (senha certa no .env?): "
                  + (r.stderr or "").strip()[:200])


def passo_schema():
    print("\n8) Schema")
    if not _porta_aberta() or not VENV_PY.exists():
        return
    r = _rodar([str(VENV_PY), "-c",
                "import sys; sys.path.insert(0, 'src'); "
                "from database import init_schema; init_schema()"],
               cwd=str(RAIZ))
    if r.returncode == 0:
        _ok("schema aplicado (init_schema)")
    else:
        _pendente("falha ao aplicar o schema: " + (r.stderr or "").strip()[:200])


def passo_oauth():
    print("\n9) Login com GitHub (opcional)")
    env = _ler_env()
    if env.get("GITHUB_CLIENT_ID") and env.get("GITHUB_CLIENT_SECRET"):
        _ok("OAuth App configurado — botão 'Entrar com GitHub' ativo")
    else:
        _pendente("sem OAuth App o login cai no modo desenvolvimento — "
                  "crie em https://github.com/settings/developers "
                  "(callback: http://127.0.0.1:5000/callback) e preencha "
                  "GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET no .env")


def main():
    print("=== Instalação da FAP ===")
    passo_python()
    passo_venv()
    passo_deps()
    passo_git()
    passo_env()
    passo_postgres()
    passo_banco()
    passo_schema()
    passo_oauth()
    print("\n=== Resumo ===")
    if pendentes:
        print("Falta só isso (manual):")
        for p in pendentes:
            print("  -", p)
    else:
        print("Tudo pronto! Para rodar:")
        print("  Windows: rode rodar.bat (ou cd src && "
              r"..\fap_env\Scripts\python.exe app.py)")
    print()


if __name__ == "__main__":
    main()
