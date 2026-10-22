"""add_printer_tracking_to_print_jobs

Revision ID: 74cb37691a9f
Revises: b441f928a301
Create Date: 2026-09-25 12:37:26.158285

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '74cb37691a9f'
down_revision: Union[str, Sequence[str], None] = 'b441f928a301'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('print_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('printer_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('printer_name', sa.String(length=128), nullable=True))

    with op.batch_alter_table('print_attempts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('printer_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('printer_name', sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('print_attempts', schema=None) as batch_op:
        batch_op.drop_column('printer_name')
        batch_op.drop_column('printer_id')

    with op.batch_alter_table('print_jobs', schema=None) as batch_op:
        batch_op.drop_column('printer_name')
        batch_op.drop_column('printer_id')
