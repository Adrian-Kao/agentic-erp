"""Provision local PostgreSQL roles after migration, using administrator credentials."""

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from erp_agent.config import Settings


def provision(settings: Settings) -> None:
    if not settings.erp_admin_database_url:
        raise ValueError("ERP_ADMIN_DATABASE_URL is required")
    admin, reader, writer = map(
        make_url,
        (
            settings.erp_admin_database_url,
            settings.read_url,
            settings.database_url,
        ),
    )
    urls = (admin, reader, writer)
    if any(url.get_backend_name() != "postgresql" for url in urls):
        raise ValueError("PostgreSQL URLs are required")
    if len({url.username for url in urls}) != 3 or any(not url.username for url in urls):
        raise ValueError("Specify three distinct database users")
    with psycopg.connect(
        admin.set(drivername="postgresql").render_as_string(hide_password=False)
    ) as conn:
        for url, tables, rights in (
            (
                reader,
                ("legal_entities", "ar_invoices", "ap_invoices", "payment_allocations"),
                "SELECT",
            ),
            (writer, ("exposure_runs", "exposure_items", "audit_events"), "SELECT, INSERT"),
        ):
            name = url.username
            if not name or not url.password:
                raise ValueError("Application and reader roles require passwords")
            role = sql.Identifier(name)
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone():
                conn.execute(
                    sql.SQL("CREATE ROLE {} NOINHERIT LOGIN PASSWORD {}").format(
                        role, sql.Literal(url.password)
                    )
                )
            # Prevent accidental use of an existing privileged account as an application role.
            privileged = conn.execute(
                "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (name,),
            ).fetchone()
            if privileged and privileged[0]:
                raise ValueError("Application roles must be unprivileged")
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
            conn.execute(
                sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                    sql.SQL(rights),
                    sql.SQL(", ").join(map(sql.Identifier, tables)),
                    role,
                )
            )


if __name__ == "__main__":
    provision(Settings())
