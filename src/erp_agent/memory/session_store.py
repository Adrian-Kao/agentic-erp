import sqlite3
from contextlib import closing
from pathlib import Path
from time import time
from uuid import UUID

from erp_agent.security.authorization import Principal


class SessionStore:
    """Retains only the last run reference, scoped to the authenticated actor."""

    def __init__(self, path: str, ttl_seconds: int = 86_400) -> None:
        self.path = Path(path)
        self.ttl = ttl_seconds

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=3)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (tenant TEXT, actor_type TEXT, actor TEXT, "
            "session_id TEXT, run_id TEXT, expires INTEGER, "
            "PRIMARY KEY (tenant, actor_type, actor, session_id))"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_sessions_expires ON sessions (expires)")
        return conn

    @staticmethod
    def _scope(principal: Principal, session_id: str) -> tuple[str, str, str, str]:
        return principal.tenant_id, principal.actor_type, principal.actor_id, session_id

    def get(self, principal: Principal, session_id: str) -> UUID | None:
        now = int(time())
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            row = conn.execute(
                "SELECT run_id FROM sessions WHERE tenant=? AND actor_type=? AND actor=? AND session_id=?",
                self._scope(principal, session_id),
            ).fetchone()
            return UUID(row[0]) if row else None

    def remember(self, principal: Principal, session_id: str, run_id: UUID) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant, actor_type, actor, session_id) DO UPDATE "
                "SET run_id=excluded.run_id, expires=excluded.expires",
                (*self._scope(principal, session_id), str(run_id), int(time()) + self.ttl),
            )
