import socket
import psycopg
from app.config import Settings, ENV_FILE


def _resolve_ipv4(hostname: str) -> str | None:
    """
    GitHub Actions runners sometimes fail IPv6 connections to Supabase.
    Resolve and prefer IPv4 when available.
    """
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
        if infos:
            return infos[0][4][0]
    except Exception:
        return None
    return None


def get_conn() -> psycopg.Connection:
    # Load fresh settings each time (avoids stale global settings in long-running server)
    s = Settings(_env_file=str(ENV_FILE))

    if not s.supabase_db_host or not s.supabase_db_password:
        raise RuntimeError("Missing DB credentials (SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD) in .env")

    hostaddr = _resolve_ipv4(s.supabase_db_host)

    # host= keeps TLS/SNI happy; hostaddr= forces actual connection target (IPv4).
    base = (
        f"host={s.supabase_db_host} "
        f"port={s.supabase_db_port} "
        f"dbname={s.supabase_db_name} "
        f"user={s.supabase_db_user} "
        f"password={s.supabase_db_password} "
        f"sslmode={s.supabase_db_sslmode}"
    )
    if hostaddr:
        base = f"hostaddr={hostaddr} " + base

    return psycopg.connect(base)
