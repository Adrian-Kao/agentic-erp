from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Connection, Engine, Table, insert, select

from erp_agent.config import Settings
from erp_agent.database.engine import database_engine
from erp_agent.database.tables import allocations, ap, ar, entities

AS_OF = date(2026, 11, 27)


def demo_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"erp-demo-v1/{key}")


def _insert_once(conn: Connection, table: Table, **values) -> None:
    if conn.scalar(select(table.c.id).where(table.c.id == values["id"])) is None:
        conn.execute(insert(table).values(**values))


def seed(engine: Engine, *, finance: bool = False) -> None:
    finance = finance or engine.dialect.name == "mysql"

    def tenant_code(value: str) -> str:
        return value.replace("-", "_").upper() if finance else value

    timestamp = datetime(2026, 10, 1, tzinfo=UTC)
    company_rows = [("demo-a", "TW01"), ("demo-a", "TW02"), ("demo-b", "TW01")]
    invoices = [
        (ar, "demo-a", "TW01", "usd-main", "USD", "2027-01-15", "1400000", "OPEN"),
        (ap, "demo-a", "TW01", "usd-bill", "USD", "2027-01-20", "800000", "OPEN"),
        (ar, "demo-a", "TW01", "late", "USD", "2026-11-10", "25000", "OPEN"),
        (ar, "demo-a", "TW01", "jpy", "JPY", "2026-12-10", "123456.1250", "OPEN"),
        (ap, "demo-a", "TW01", "eur", "EUR", "2027-02-10", "50000", "OPEN"),
        (ar, "demo-a", "TW01", "unknown-due", "EUR", None, "20000", "OPEN"),
        (ar, "demo-a", "TW01", "closed", "USD", "2026-12-05", "5000", "SETTLED"),
        (ap, "demo-a", "TW01", "cancel", "USD", "2026-12-05", "9000", "CANCELLED"),
        (ar, "demo-a", "TW01", "local", "TWD", "2026-12-01", "1234", "OPEN"),
        (ap, "demo-a", "TW02", "other-entity", "USD", "2027-01-20", "90000", "OPEN"),
        (ar, "demo-b", "TW01", "other-tenant", "USD", "2027-01-20", "77777", "OPEN"),
    ]
    document_ids: dict[str, UUID] = {}
    with engine.begin() as conn:
        for tenant, code in company_rows:
            tenant = tenant_code(tenant)
            _insert_once(
                conn,
                entities,
                id=demo_id(f"{tenant}/{code}"),
                tenant_id=tenant,
                entity_code=code,
                name="Aster Electronics Co., Ltd. (fictional)"
                if finance and tenant == "DEMO_A" and code == "TW01"
                else f"Demo {tenant} {code}",
                functional_currency="TWD",
                is_active=True,
            )
        for table, tenant, code, ref, currency, due, amount, status in invoices:
            target: Table = table
            tenant = tenant_code(tenant)
            document_no = f"DEMO-{ref}"
            entity_id = demo_id(f"{tenant}/{code}")
            existing = conn.scalar(
                select(target.c.id).where(
                    target.c.tenant_id == tenant,
                    target.c.entity_id == entity_id,
                    target.c.document_no == document_no,
                )
            )
            doc_id = existing or demo_id(f"finance-fixture/{ref}" if finance else ref)
            document_ids[ref] = doc_id
            _insert_once(
                conn,
                target,
                id=doc_id,
                tenant_id=tenant,
                entity_id=entity_id,
                document_no=document_no,
                currency=currency,
                issue_date=timestamp.date(),
                due_date=date.fromisoformat(due) if due else None,
                original_amount=Decimal(amount),
                status=status,
                created_at=timestamp,
                updated_at=timestamp,
            )
        for ref, status, effective, amount in [
            ("usd-main", "POSTED", "2026-11-27", "200000"),
            ("usd-main", "VOID", "2026-11-12", "100000"),
            ("usd-main", "POSTED", "2026-12-10", "10000"),
            ("closed", "POSTED", "2026-11-20", "5000"),
        ]:
            doc_id = document_ids[ref]
            key = f"{ref}/{effective}/{status}"
            _insert_once(
                conn,
                allocations,
                id=demo_id(f"finance-fixture/{key}" if finance and doc_id != demo_id(ref) else key),
                tenant_id=tenant_code("demo-a"),
                document_type="AR",
                document_id=doc_id,
                currency="USD",
                amount=Decimal(amount),
                effective_date=date.fromisoformat(effective),
                status=status,
            )


if __name__ == "__main__":
    settings = Settings()
    if settings.app_env != "development":
        raise SystemExit("Demo seed requires APP_ENV=development")
    engine = database_engine(
        settings.erp_import_database_url or settings.erp_admin_database_url or settings.database_url
    )
    seed(engine)
    if engine.dialect.name == "mysql":
        from erp_agent.ingestion.scenario_builder import register_fixture

        register_fixture(engine)
    print("ERP demo seed ready")
