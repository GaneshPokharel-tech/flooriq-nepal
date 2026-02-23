from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from urllib.parse import quote_plus

from app.config import Settings, ENV_FILE

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# We are not using SQLAlchemy ORM models yet.
# Migrations are managed as explicit Alembic revisions (op.execute / raw SQL).
target_metadata = None


def _build_sqlalchemy_url() -> str:
    s = Settings(_env_file=str(ENV_FILE))

    if not s.supabase_db_host or not s.supabase_db_password:
        raise RuntimeError("Missing DB credentials (SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD) in .env")

    user = quote_plus(str(s.supabase_db_user))
    pwd = quote_plus(str(s.supabase_db_password))
    host = str(s.supabase_db_host)
    db = str(s.supabase_db_name)

    return f"postgresql+psycopg://{user}:{pwd}@{host}:{s.supabase_db_port}/{db}?sslmode={s.supabase_db_sslmode}"



def run_migrations_offline() -> None:
    url = _build_sqlalchemy_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _build_sqlalchemy_url()
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
