import psycopg
from app.config import Settings, ENV_FILE


def get_conn() -> psycopg.Connection:
    # Load fresh settings each time (avoids stale global settings in long-running server)
    s = Settings(_env_file=str(ENV_FILE))

    if not s.supabase_db_host or not s.supabase_db_password:
        raise RuntimeError("Missing DB credentials (SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD) in .env")

    conninfo = (
        f"host={s.supabase_db_host} "
        f"port={s.supabase_db_port} "
        f"dbname={s.supabase_db_name} "
        f"user={s.supabase_db_user} "
        f"password={s.supabase_db_password} "
        f"sslmode={s.supabase_db_sslmode}"
    )
    return psycopg.connect(conninfo)
