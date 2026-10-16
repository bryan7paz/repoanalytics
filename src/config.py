import os
import warnings
from pathlib import Path
from dotenv import load_dotenv

warnings.filterwarnings(
    "ignore",
    message="pandas only supports SQLAlchemy connectable",
    category=UserWarning,
)

PROJ_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJ_ROOT / ".env")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "fap"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
}

def _token_efetivo(valor):
    """Placeholder do .env.example não é token: vira '' (chamadas anônimas)."""
    valor = valor.strip()
    return "" if valor == "coloque_seu_token_aqui" else valor


GITHUB_TOKEN = _token_efetivo(os.getenv("GITHUB_TOKEN", ""))

MESES_ANALISE = int(os.getenv("MESES_ANALISE", "6"))