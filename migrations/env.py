from alembic import context

from erp_agent.config import Settings
from erp_agent.database.dataset_tables import dataset_metadata
from erp_agent.database.engine import database_engine
from erp_agent.database.tables import metadata

settings = Settings()
url = settings.erp_admin_database_url or settings.database_url
target = (metadata, dataset_metadata)
if context.is_offline_mode():
    context.configure(url=url, target_metadata=target, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with database_engine(url).connect() as connection:
        context.configure(connection=connection, target_metadata=target)
        with context.begin_transaction():
            context.run_migrations()
