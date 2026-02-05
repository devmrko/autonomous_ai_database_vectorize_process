"""
Oracle DB connection and query helpers for vector search / knowledge bot.
Uses oracledb with env: DB_USER, DB_PASSWORD, DB_DSN, TNS_ADMIN (optional, for wallet).
"""

import os
import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lazy init
_pool = None
_env_loaded = False


def _load_env():
    """Load .env so TNS_ADMIN/DB_* are set (Oracle client uses TNS_ADMIN at init)."""
    global _env_loaded
    if _env_loaded:
        return
    try:
        from dotenv import load_dotenv
        for path in [
            os.path.join(os.path.dirname(__file__), "..", ".env"),  # project root
            os.path.join(os.getcwd(), ".env"),
            ".env",
        ]:
            abs_path = os.path.abspath(path)
            if os.path.isfile(abs_path):
                load_dotenv(abs_path, override=True)
                logger.info("Loaded .env from %s", abs_path)
                break
    except ImportError:
        pass
    _env_loaded = True


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    _load_env()
    import oracledb

    user = os.getenv("DB_USER")
    password = os.getenv("DB_PASSWORD")
    dsn = os.getenv("DB_DSN")
    # Wallet dir: TNS_ADMIN or WALLET_LOCATION (so tnsnames.ora is found)
    tns_admin = os.getenv("TNS_ADMIN") or os.getenv("WALLET_LOCATION")
    if not all([user, password, dsn]):
        raise ValueError("Set DB_USER, DB_PASSWORD, DB_DSN in environment")

    # Must call init_oracle_client before first connection so tnsnames.ora is read from wallet
    if tns_admin and os.path.isdir(tns_admin):
        try:
            oracledb.init_oracle_client(config_dir=tns_admin)
            logger.info("Oracle client config_dir=%s", tns_admin)
        except oracledb.ProgrammingError as e:
            if "already been initialized" not in str(e).lower():
                raise
    else:
        logger.warning("TNS_ADMIN/WALLET_LOCATION not set or not a directory: %s", tns_admin)

    _pool = oracledb.create_pool(
        user=user,
        password=password,
        dsn=dsn,
        min=1,
        max=4,
    )
    return _pool


class DatabaseManager:
    """Simple Oracle DB manager using oracledb connection pool."""

    def __init__(self):
        self._pool = _get_pool()

    def execute_query(
        self,
        query: str,
        params: Optional[dict] = None,
        fetch_one: bool = False,
        fetch_all: bool = True,
        commit: bool = False,
    ) -> Any:
        """Execute a query. Returns one row, list of rows, or None."""
        conn = self._pool.acquire()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params or {})
            if commit:
                conn.commit()
            if fetch_one:
                return cursor.fetchone()
            if fetch_all:
                return cursor.fetchall()
            return None
        finally:
            if cursor:
                cursor.close()
            self._pool.release(conn)

    def execute_procedure(self, proc_call: str, params: Optional[dict] = None) -> bool:
        """Execute a PL/SQL block or procedure with auto-commit."""
        conn = self._pool.acquire()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(proc_call, params or {})
            conn.commit()
            return True
        except Exception as e:
            logger.error("execute_procedure failed: %s", e)
            raise
        finally:
            if cursor:
                cursor.close()
            self._pool.release(conn)

    @staticmethod
    def close_pool():
        global _pool
        if _pool:
            _pool.close()
            _pool = None
