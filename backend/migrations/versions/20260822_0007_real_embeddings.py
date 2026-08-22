"""Resize knowledge vectors for the local BGE embedding provider.

Revision ID: 20260822_0007
Revises: 20260815_0006
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision: str = "20260822_0007"
down_revision: str | None = "20260815_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.alter_column(
        "knowledge_chunks", "embedding", new_column_name="embedding_legacy"
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column(
            "embedding", pgvector.sqlalchemy.VECTOR(dim=512), nullable=True
        ),
    )
    op.drop_column("knowledge_chunks", "embedding_legacy")
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.alter_column(
        "knowledge_chunks", "embedding", new_column_name="embedding_real"
    )
    op.add_column(
        "knowledge_chunks",
        sa.Column("embedding", pgvector.sqlalchemy.VECTOR(dim=64), nullable=True),
    )
    op.execute(
        "UPDATE knowledge_chunks SET embedding = "
        "('[' || array_to_string(array_fill(0.0::real, ARRAY[64]), ',') || ']')::vector"
    )
    op.alter_column("knowledge_chunks", "embedding", nullable=False)
    op.drop_column("knowledge_chunks", "embedding_real")
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
