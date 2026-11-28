import re

from sqlalchemy.engine import make_url

from erp_agent.config import Settings
from erp_agent.database import dataset_tables as ds
from erp_agent.database.engine import database_engine
from erp_agent.database.tables import allocations, ap, ar, audit, entities, items, runs


def provision_mysql(settings: Settings) -> None:
    if not settings.erp_admin_database_url:
        raise ValueError("ERP_ADMIN_DATABASE_URL is required to provision MySQL")
    admin, reader, writer, importer = map(
        make_url,
        (
            settings.erp_admin_database_url,
            settings.read_url,
            settings.database_url,
            settings.import_url,
        ),
    )
    all_roles = (admin, reader, writer, importer)
    if len({role.username for role in all_roles}) != 4 or any(
        role.get_backend_name() != "mysql" or role.database != admin.database for role in all_roles
    ):
        raise ValueError("Four distinct MySQL accounts in the same database are required")
    if not admin.database or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", admin.database):
        raise ValueError("Invalid MySQL database name")
    source = (entities, ar, ap, allocations)
    public_metadata = (
        ds.batches,
        ds.versions,
        ds.current_source,
        ds.active_scenarios,
        ds.scenario_documents,
        ds.source_links,
    )
    reports = (runs, items, audit, ds.run_source_links)
    imported = (*ds.raw_tables.values(), ds.counterparties, ds.journals, ds.mappings, ds.quarantine)
    engine = database_engine(settings.erp_admin_database_url)
    try:
        with engine.begin() as conn:
            quote = conn.dialect.identifier_preparer.quote
            for role, groups in (
                (reader, (("SELECT", (*source, *public_metadata)),)),
                (writer, (("SELECT, INSERT", reports),)),
                (
                    importer,
                    (
                        ("SELECT, INSERT", (*source, *public_metadata, *imported)),
                        ("UPDATE", (ds.batches, ds.current_source, ds.active_scenarios)),
                    ),
                ),
            ):
                if (
                    not role.username
                    or not role.password
                    or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", role.username)
                ):
                    raise ValueError("MySQL role needs a valid configured username and password")
                conn.exec_driver_sql(
                    "CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s",
                    (role.username, "%", role.password),
                )
                for privileges, tables in groups:
                    for table in tables:
                        resource = f"{quote(admin.database)}.{quote(table.name)}"
                        conn.exec_driver_sql(
                            f"GRANT {privileges} ON {resource} TO %s@%s", (role.username, "%")
                        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    provision_mysql(Settings())
    print("MySQL reader, importer and report writer provisioned")
