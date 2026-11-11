"""drop clauses.category; key clause_references by (clause_id, reference_id)

clauses.category was never filled by the AI prompt. A clause can cite a legal reference only
once, so the reference id (not the position) is part of the primary key; position stays as
the display order. Duplicate (clause, reference) pairs are removed first, keeping the first.

Revision ID: b7e41c9a2d30
Revises: 40980782e4a7
Create Date: 2026-11-10 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7e41c9a2d30'
down_revision: Union[str, Sequence[str], None] = '40980782e4a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(
        "DELETE FROM clause_references a USING clause_references b "
        "WHERE a.clause_id = b.clause_id AND a.reference_id = b.reference_id "
        "AND a.position > b.position")
    op.drop_constraint('pk_clause_references', 'clause_references', type_='primary')
    op.create_primary_key('pk_clause_references', 'clause_references', ['clause_id', 'reference_id'])
    op.drop_column('clauses', 'category')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('clauses', sa.Column('category', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.drop_constraint('pk_clause_references', 'clause_references', type_='primary')
    op.create_primary_key('pk_clause_references', 'clause_references', ['clause_id', 'position'])
