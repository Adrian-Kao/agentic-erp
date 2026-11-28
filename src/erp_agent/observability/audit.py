from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, insert

from erp_agent.database.tables import audit
from erp_agent.security.authorization import Principal


def write_audit(
    conn: Connection,
    principal: Principal,
    operation: str,
    result: str,
    request_id: str,
    *,
    run_id: UUID | None = None,
    source_rows: int | None = None,
    issue_count: int | None = None,
) -> None:
    conn.execute(
        insert(audit).values(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            actor_type=principal.actor_type,
            operation=operation,
            result=result,
            run_id=run_id,
            metadata_json={
                "request_id": request_id,
                "source_rows": source_rows,
                "issue_count": issue_count,
            },
            created_at=datetime.now(UTC),
        )
    )
