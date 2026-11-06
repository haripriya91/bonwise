"""Add clothing category.

Revision ID: 1bc1cf27e701
Revises: 6ca87490b165
Create Date: 2026-11-05 22:28:13.494827
"""

from typing import Sequence, Union

from alembic import op


# Revision identifiers, used by Alembic.
revision: str = "1bc1cf27e701"
down_revision: Union[str, Sequence[str], None] = "6ca87490b165"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_CATEGORIES = (
    "category IN ('groceries','household','personal_care',"
    "'transport','dining_out','other')"
)

NEW_CATEGORIES = (
    "category IN ('groceries','clothing','household',"
    "'personal_care','transport','dining_out','other')"
)


def replace_category_checks(expression: str) -> None:
    for table in ("products", "purchase_items"):
        constraint_name = op.f(f"ck_{table}_valid_category")

        op.drop_constraint(
            constraint_name,
            table,
            type_="check",
            schema="public",
        )

        op.create_check_constraint(
            constraint_name,
            table,
            expression,
            schema="public",
        )


def upgrade() -> None:
    """Allow clothing in product and purchase-item categories."""
    replace_category_checks(NEW_CATEGORIES)


def downgrade() -> None:
    """Restore the previous categories without modifying records."""
    # This fails safely if existing rows use clothing.
    replace_category_checks(OLD_CATEGORIES)