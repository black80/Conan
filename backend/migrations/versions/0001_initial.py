"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("customer_id", sa.String(), nullable=False),
        sa.Column("card_token", sa.String(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("merchant", sa.String(), nullable=False),
        sa.Column("merchant_category", sa.String(), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("device_id", sa.String(), nullable=False),
        sa.Column("card_present", sa.Boolean(), nullable=False),
        sa.Column("declined", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_transactions_customer_id", "transactions", ["customer_id"])
    op.create_index("ix_transactions_card_token", "transactions", ["card_token"])
    op.create_index("ix_transactions_device_id", "transactions", ["device_id"])

    op.create_table(
        "fraud_rules",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_fraud_rules_code", "fraud_rules", ["code"])

    op.create_table(
        "transaction_rule_hits",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("transaction_id", UUID, nullable=False),
        sa.Column("rule_code", sa.String(), nullable=False),
        sa.Column("rule_name", sa.String(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("detail", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
    )
    op.create_index(
        "ix_transaction_rule_hits_transaction_id",
        "transaction_rule_hits",
        ["transaction_id"],
    )

    op.create_table(
        "investigators",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_investigators_email", "investigators", ["email"])

    op.create_table(
        "investigation_cases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("transaction_id", UUID, nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("resolution", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("assigned_to", UUID, nullable=True),
        sa.Column("final_decision", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.ForeignKeyConstraint(["assigned_to"], ["investigators.id"]),
        sa.UniqueConstraint("transaction_id"),
    )
    op.create_index(
        "ix_investigation_cases_transaction_id",
        "investigation_cases",
        ["transaction_id"],
    )
    op.create_index(
        "ix_investigation_cases_status", "investigation_cases", ["status"]
    )

    op.create_table(
        "ai_recommendations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("case_id", UUID, nullable=False),
        sa.Column("recommendation", sa.String(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("reasoning", postgresql.JSONB(), nullable=False),
        sa.Column("next_action", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["investigation_cases.id"]),
    )
    op.create_index(
        "ix_ai_recommendations_case_id", "ai_recommendations", ["case_id"]
    )


def downgrade() -> None:
    op.drop_table("ai_recommendations")
    op.drop_table("investigation_cases")
    op.drop_table("investigators")
    op.drop_table("transaction_rule_hits")
    op.drop_table("fraud_rules")
    op.drop_table("transactions")
