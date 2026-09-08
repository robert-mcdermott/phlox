"""Checked baseline for pre-Alembic Phlox databases, through Wave 3."""
from alembic import op

from app.migrations.baseline import adopt

revision = '0001_wave3'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    adopt(op.get_bind())


def downgrade():
    raise RuntimeError('Baseline downgrade is unsupported; restore a verified backup with its matching release')
