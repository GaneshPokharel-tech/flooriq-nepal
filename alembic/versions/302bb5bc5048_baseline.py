"""baseline

Revision ID: 302bb5bc5048
Revises:
Create Date: 2026-02-23

This is a NO-OP baseline revision.

Our initial database schema is created via:
  supabase/schema.sql

We keep Alembic so future schema changes can be tracked as migrations.
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "302bb5bc5048"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally empty (baseline only)
    return


def downgrade() -> None:
    # Intentionally empty (baseline only)
    return
