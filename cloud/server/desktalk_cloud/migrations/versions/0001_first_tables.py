"""The first tables: people, Pis, tokens, codes, events, totals, object states and reports.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

TIME = postgresql.DOUBLE_PRECISION          # seconds since 1970, kept exactly
BYTES_ORDER = 'C'                           # compare text as plain bytes, like Python does


def _device_fk() -> sa.Column:
    return sa.Column('device_id', sa.Text, sa.ForeignKey('devices.id', ondelete='CASCADE'), nullable=False)


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('google_sub', sa.Text, nullable=False, unique=True),
        sa.Column('email', sa.Text, nullable=False),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('created_at', TIME, nullable=False),
    )
    op.create_table(
        'admins',
        sa.Column('username', sa.Text, primary_key=True),
        sa.Column('password_hash', sa.Text, nullable=False),
    )
    op.create_table(
        'devices',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('user_id', sa.Text, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('time_zone', sa.Text, nullable=False),
        sa.Column('created_at', TIME, nullable=False),
        sa.Column('last_seen_at', TIME),
        sa.Column('caught_up_at', TIME),
        sa.Column('removed_at', TIME),
    )
    op.create_index('devices_user_id', 'devices', ['user_id'])
    op.create_table(
        'device_tokens',
        sa.Column('id', sa.Text, primary_key=True),
        _device_fk(),
        sa.Column('token_hash', sa.Text, nullable=False, unique=True),
        sa.Column('created_at', TIME, nullable=False),
        sa.Column('last_used_at', TIME),
        sa.Column('revoked_at', TIME),
    )
    op.create_index('device_tokens_device_id', 'device_tokens', ['device_id'])
    op.create_table(
        'pairing_codes',
        sa.Column('code_hash', sa.Text, primary_key=True),
        sa.Column('user_id', sa.Text, sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        # set for a "link again" code: the Pi it links again; empty for a code that makes a new Pi
        sa.Column('device_id', sa.Text, sa.ForeignKey('devices.id', ondelete='CASCADE')),
        sa.Column('time_zone', sa.Text, nullable=False),
        sa.Column('expires_at', TIME, nullable=False),
        sa.Column('used_at', TIME),
    )
    op.create_index('pairing_codes_device_id', 'pairing_codes', ['device_id'])
    op.create_index('pairing_codes_user_id', 'pairing_codes', ['user_id'])
    op.create_table(
        'events',
        _device_fk(),
        sa.Column('event_id', sa.Text(collation=BYTES_ORDER), nullable=False),
        sa.Column('ts', TIME, nullable=False),
        sa.Column('type', sa.Text, nullable=False),
        sa.Column('payload', postgresql.JSONB, nullable=False),
        sa.Column('received_at', TIME, nullable=False),
        sa.PrimaryKeyConstraint('device_id', 'event_id'),
    )
    op.create_index('events_device_ts', 'events', ['device_id', 'ts', 'event_id'])
    op.create_index('events_device_type_ts', 'events', ['device_id', 'type', 'ts'])
    op.create_index('events_ts', 'events', ['ts'])            # for deleting old events
    op.create_table(
        'daily_totals',
        _device_fk(),
        sa.Column('day', sa.Text, nullable=False),
        sa.Column('ts', TIME, nullable=False),                # the time of the event kept; only a newer one replaces it
        sa.Column('payload', postgresql.JSONB, nullable=False),
        sa.Column('updated_at', TIME, nullable=False),
        sa.PrimaryKeyConstraint('device_id', 'day'),
    )
    op.create_table(
        'device_state',
        _device_fk(),
        sa.Column('label', sa.Text(collation=BYTES_ORDER), nullable=False),
        sa.Column('state', sa.Text, nullable=False),
        sa.Column('since', TIME, nullable=False),
        sa.Column('updated_at', TIME, nullable=False),
        sa.PrimaryKeyConstraint('device_id', 'label'),
    )
    op.create_table(
        'reports',
        sa.Column('id', sa.Text, primary_key=True),
        _device_fk(),
        sa.Column('week_start', sa.Text, nullable=False),
        sa.Column('status', sa.Text, nullable=False),
        sa.Column('markdown', sa.Text),
        sa.Column('model', sa.Text),
        sa.Column('error', sa.Text),
        sa.Column('created_at', TIME, nullable=False),
        sa.Column('tries', sa.Integer, nullable=False, server_default='0'),
        sa.Column('next_try_at', TIME),
        sa.UniqueConstraint('device_id', 'week_start'),
        sa.CheckConstraint("status IN ('pending', 'done', 'no_data', 'failed')", name='reports_status'),
    )
    op.create_index('reports_status', 'reports', ['status'])
