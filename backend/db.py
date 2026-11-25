"""The database pool and the one helper every module uses to run SQL."""
import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://reader:reader@127.0.0.1:5433/reader")  # not "localhost": it tries IPv6 first and hangs

# check: test a connection before using it. After the laptop sleeps, the saved connections are dead;
# without this the first requests after waking up fail.
pool = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row, "autocommit": True}, open=False,
                      check=ConnectionPool.check_connection)


def q(sql: str, params: tuple = ()) -> list[dict]:
    with pool.connection() as conn:
        cur = conn.execute(sql, params or None)  # None: a "%" in SQL without parameters is plain text
        return cur.fetchall() if cur.description else []
