"""add_copies_to_print_configurations

Revision ID: c78b41f23a90
Revises: fad2dad27c07
Create Date: 2026-10-25 20:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c78b41f23a90'
down_revision: Union[str, Sequence[str], None] = 'fad2dad27c07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('print_configurations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('copies', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('print_configurations', schema=None) as batch_op:
        batch_op.drop_column('copies')
