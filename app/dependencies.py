from typing import Generator
import psycopg
from .database import get_conn


def db_conn() -> Generator[psycopg.Connection, None, None]:
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()
