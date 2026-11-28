from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError

from erp_agent.config import Settings
from erp_agent.database import repository
from erp_agent.database.engine import database_engine
from erp_agent.database.named_queries import QUERY_NAMES
from erp_agent.database.repository import QueryLimitExceeded, SQLAlchemyERPRepository
from erp_agent.database.tables import allocations, ar
from scripts.seed_demo import AS_OF, demo_id, seed


def test_migration_seed_and_read_scope(demo_settings: Settings) -> None:
    engine = database_engine(demo_settings.read_url)
    seed(engine)
    repo = SQLAlchemyERPRepository(engine)
    snapshot = repo.snapshot("demo-a", ("TW01",), AS_OF, ("USD",))
    assert {doc.original_amount for doc in snapshot.invoices} == {
        Decimal("1400000"),
        Decimal("800000"),
        Decimal("25000"),
        Decimal("5000"),
        Decimal("9000"),
    }
    assert {p.amount for p in snapshot.allocations} == {
        Decimal("200000"),
        Decimal("100000"),
        Decimal("5000"),
    }
    assert len(snapshot.invoices) == 5
    assert repo.snapshot("demo-b", ("TW01",), AS_OF).invoices[0].original_amount == Decimal("77777")
    assert not repo.list_entities("demo-b", ("TW02",))
    assert "SELECT * FROM ar_invoices" not in QUERY_NAMES


def test_snapshot_row_budget(demo_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "MAX_ROWS", 1)
    with pytest.raises(QueryLimitExceeded):
        SQLAlchemyERPRepository(database_engine(demo_settings.read_url)).snapshot(
            "demo-a",
            ("TW01",),
            AS_OF,
        )


@pytest.mark.parametrize(
    ("tenant", "kind", "currency"),
    [
        ("demo-b", "AR", "USD"),
        ("demo-a", "AP", "USD"),
        ("demo-a", "AR", "EUR"),
    ],
)
def test_payment_reference_scope(demo_settings: Settings, tenant, kind, currency) -> None:
    engine = database_engine(demo_settings.database_url)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            insert(allocations).values(
                id=uuid4(),
                tenant_id=tenant,
                document_type=kind,
                document_id=demo_id("usd-main"),
                currency=currency,
                amount=Decimal("100.0000"),
                effective_date=AS_OF,
                status="POSTED",
            )
        )


def test_exact_money_sqlite_round_trip(demo_settings: Settings) -> None:
    amount = Decimal("9999999999999999.9999")
    with database_engine(demo_settings.database_url).begin() as conn:
        conn.execute(
            update(ar).where(ar.c.id == demo_id("usd-main")).values(original_amount=amount)
        )
    snapshot = SQLAlchemyERPRepository(database_engine(demo_settings.read_url, read=True)).snapshot(
        "demo-a",
        ("TW01",),
        AS_OF,
        ("USD",),
    )
    assert (
        next(doc.original_amount for doc in snapshot.invoices if doc.id == demo_id("usd-main"))
        == amount
    )
