from alembic import op

from erp_agent.database.dataset_tables import dataset_metadata

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dataset_metadata.create_all(op.get_bind(), checkfirst=False)


def downgrade() -> None:
    dataset_metadata.drop_all(op.get_bind(), checkfirst=False)
