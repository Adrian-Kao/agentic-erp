from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import make_url


def database_engine(url: str, *, read: bool = False) -> Engine:
    backend = make_url(url).get_backend_name()
    if backend == "mysql":
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=5,
            isolation_level="REPEATABLE READ" if read else "READ COMMITTED",
            connect_args={"connect_timeout": 5, "read_timeout": 20, "write_timeout": 20},
            hide_parameters=True,
        )

        @event.listens_for(engine, "connect")
        def configure_mysql(dbapi_connection: Any, _: Any) -> None:
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET SESSION time_zone = '+00:00'")
                cursor.execute("SET SESSION max_execution_time = 5000")
                if read:
                    cursor.execute("SET SESSION TRANSACTION READ ONLY")

        return engine
    if backend == "postgresql":
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_timeout=5,
            isolation_level="REPEATABLE READ" if read else "READ COMMITTED",
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
            hide_parameters=True,
        )
    engine = create_engine(url, isolation_level="SERIALIZABLE", hide_parameters=True)

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: Any, _: Any) -> None:
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
        if read:
            dbapi_connection.execute("PRAGMA query_only=ON")

    @event.listens_for(engine, "begin")
    def begin_sqlite(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine
