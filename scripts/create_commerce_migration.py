"""Run from backend/: PYTHONPATH=. python ../scripts/create_commerce_migration.py

Freeze ONLY these two new model tables into a normal Alembic revision. The real current
head, existing Record columns and naming convention come from the user's repository.
This creates a migration file; it does not connect to/migrate the database.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.autogenerate import render_python_code
from alembic.operations.ops import (
    CreateTableOp,
    CreateIndexOp,
    UpgradeOps,
    DropTableOp,
    DowngradeOps,
)
from sqlalchemy.dialects import postgresql

import app.db.models  # Register existing FK target tables, without opening .env contents.
from app.db.commerce import HouseholdBudget, BudgetCommitment


def main():
    config = Config("alembic.ini")
    config.set_main_option("revision_environment", "false")
    directory = ScriptDirectory.from_config(config)
    if len(directory.get_heads()) != 1:
        raise SystemExit(
            "Expected one Alembic head. Resolve migration branches before continuing."
        )
    for revision in directory.walk_revisions():
        if "add household budgets and commitments" in (revision.doc or ""):
            raise SystemExit(
                "A budget migration already exists. Inspect/use it rather than generating another."
            )
    tables = [HouseholdBudget.__table__, BudgetCommitment.__table__]
    changes = []
    for table in tables:
        changes.append(CreateTableOp.from_table(table))
        changes.extend(
            CreateIndexOp.from_index(index)
            for index in sorted(table.indexes, key=lambda i: i.name)
        )
    upgrade = render_python_code(
        UpgradeOps(changes), migration_context=None, user_module_prefix=None
    )
    downgrade = render_python_code(
        DowngradeOps([DropTableOp.from_table(t) for t in reversed(tables)])
    )
    revision = command.revision(config, message="add household budgets and commitments")
    path = Path(revision.path)
    original = path.read_text()
    header = original[: original.index("def upgrade")]
    if "from sqlalchemy.dialects import postgresql" not in header:
        header += "from sqlalchemy.dialects import postgresql\n\n"
    path.write_text(
        header
        + "def upgrade() -> None:\n"
        + upgrade
        + "\n\ndef downgrade() -> None:\n"
        + downgrade
        + "\n"
    )
    print(f"Created {path}. Review it, then run python -m alembic upgrade head.")


if __name__ == "__main__":
    main()
