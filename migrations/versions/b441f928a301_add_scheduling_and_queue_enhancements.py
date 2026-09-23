"""add_scheduling_and_queue_enhancements

Revision ID: b441f928a301
Revises: a332ee87e882
Create Date: 2026-09-23 14:50:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b441f928a301'
down_revision: Union[str, Sequence[str], None] = 'a332ee87e882'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Shop settings table
    op.create_table(
        'shop_settings',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('shop_name', sa.String(length=128), nullable=False),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=False),
        sa.Column('order_acceptance_enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # 2. Printing schedules table
    op.create_table(
        'printing_schedules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('open_time', sa.Time(), nullable=False),
        sa.Column('close_time', sa.Time(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_printing_schedules_day_of_week'), 'printing_schedules', ['day_of_week'], unique=False)

    # 3. Schedule exceptions table
    op.create_table(
        'schedule_exceptions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('exception_date', sa.Date(), nullable=False),
        sa.Column('exception_type', sa.String(length=32), nullable=False),
        sa.Column('open_time', sa.Time(), nullable=True),
        sa.Column('close_time', sa.Time(), nullable=True),
        sa.Column('reason', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_schedule_exceptions_exception_date'), 'schedule_exceptions', ['exception_date'], unique=False)

    # 4. Print service control table
    op.create_table(
        'print_service_control',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('manual_override', sa.String(length=32), nullable=False),
        sa.Column('effective_status', sa.String(length=32), nullable=False),
        sa.Column('reason', sa.String(length=256), nullable=True),
        sa.Column('updated_by', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # 5. Add columns to print_jobs
    with op.batch_alter_table('print_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('priority', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('is_held', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('held_reason', sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column('held_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('error_code', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('error_message', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('print_jobs', schema=None) as batch_op:
        batch_op.drop_column('error_message')
        batch_op.drop_column('error_code')
        batch_op.drop_column('held_at')
        batch_op.drop_column('held_reason')
        batch_op.drop_column('is_held')
        batch_op.drop_column('priority')

    op.drop_table('print_service_control')
    op.drop_index(op.f('ix_schedule_exceptions_exception_date'), table_name='schedule_exceptions')
    op.drop_table('schedule_exceptions')
    op.drop_index(op.f('ix_printing_schedules_day_of_week'), table_name='printing_schedules')
    op.drop_table('printing_schedules')
    op.drop_table('shop_settings')
