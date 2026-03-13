import socket
import subprocess
import psycopg
from app.config import Settings, ENV_FILE


_CACHED_HOSTADDR: str | None = None
_CACHED_HOSTNAME: str | None = None
_CACHED_FAMILY: int | None = None


def _resolve_ip(hostname: str) -> tuple[str | None, int | None]:
    """
    Prefer IPv4 when available, but fall back to IPv6 if that is all we have.
    Returns: (hostaddr, family)
    """
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(hostname, 5432, family=family, type=socket.SOCK_STREAM)
            if infos:
                return infos[0][4][0], family
        except Exception:
            continue

    # macOS fallback: system resolver can sometimes see records Python doesn't
    try:
        out = subprocess.check_output(
            ["dscacheutil", "-q", "host", "-a", "name", hostname],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        ipv4 = None
        ipv6 = None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("ip_address:"):
                ipv4 = line.split(":", 1)[1].strip()
            elif line.startswith("ipv6_address:"):
                ipv6 = line.split(":", 1)[1].strip()

        if ipv4:
            return ipv4, socket.AF_INET
        if ipv6:
            return ipv6, socket.AF_INET6
    except Exception:
        pass

    return None, None


def get_conn() -> psycopg.Connection:
    global _CACHED_HOSTADDR, _CACHED_HOSTNAME, _CACHED_FAMILY

    # Load fresh settings each time (avoids stale global settings in long-running server)
    s = Settings()

    if not s.supabase_db_host or not s.supabase_db_password:
        raise RuntimeError("Missing DB credentials (SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD) in .env")

    hostaddr = None
    family = None

    if _CACHED_HOSTNAME == s.supabase_db_host and _CACHED_HOSTADDR:
        hostaddr = _CACHED_HOSTADDR
        family = _CACHED_FAMILY
    else:
        resolved, resolved_family = _resolve_ip(s.supabase_db_host)
        if resolved:
            _CACHED_HOSTNAME = s.supabase_db_host
            _CACHED_HOSTADDR = resolved
            _CACHED_FAMILY = resolved_family
            hostaddr = resolved
            family = resolved_family

    conn_kwargs = dict(
        host=(hostaddr or s.supabase_db_host),
        port=s.supabase_db_port,
        dbname=s.supabase_db_name,
        user=s.supabase_db_user,
        password=s.supabase_db_password,
        sslmode=s.supabase_db_sslmode,
        prepare_threshold=None,
    )

    return psycopg.connect(**conn_kwargs)
