"""findings ids use AUTOINCREMENT

Revision ID: findings_autoincrement
Revises: init
Create Date: 2026-10-28 12:00:00.000000

Without AUTOINCREMENT SQLite hands out the ids of deleted findings again, so old
action-log rows (which keep ``finding_id``) would attach to unrelated findings.
SQLite can only add AUTOINCREMENT by rebuilding the table. Dropping the old table
while foreign keys are enforced cascades into its children, so their rows are kept
aside and restored around the rebuild. The sequence starts past every id an
existing action-log row refers to.
"""
from alembic import op


revision = 'findings_autoincrement'
down_revision = 'init'
branch_labels = None
depends_on = None

_CHILDREN = ('finding_copies', 'finding_server_ignores', 'rule_states')


def _rebuild(autoincrement: bool) -> None:
    for t in _CHILDREN:
        op.execute(f'DROP TABLE IF EXISTS _keep_{t}')
        op.execute(f'CREATE TEMP TABLE _keep_{t} AS SELECT * FROM {t}')
    with op.batch_alter_table(
        'findings', recreate='always', table_kwargs={'sqlite_autoincrement': autoincrement}
    ):
        pass
    for t in _CHILDREN:
        op.execute(f'DELETE FROM {t}')  # whether or not the rebuild cascaded
        op.execute(f'INSERT INTO {t} SELECT * FROM _keep_{t}')
        op.execute(f'DROP TABLE _keep_{t}')


def upgrade() -> None:
    _rebuild(True)
    op.execute("DELETE FROM sqlite_sequence WHERE name = 'findings'")
    op.execute(
        "INSERT INTO sqlite_sequence (name, seq) SELECT 'findings', MAX("
        "COALESCE((SELECT MAX(id) FROM findings), 0), "
        "COALESCE((SELECT MAX(finding_id) FROM action_log), 0))"
    )


def downgrade() -> None:
    _rebuild(False)
