import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings
from app.db.base import Base
from app.db import models  # Registers model metadata.
from app.db import commerce, planner, corrections, household_settings  # Register budget/planner metadata.
from app.db.session import make_db_engine


config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

OWNED_TABLES = {
    "household_settings",
    "account_profiles",
    "household_needs",
    "households",
    "household_members",
    "products",
    "purchase_drafts",
    "purchases",
    "purchase_items",
    "household_budgets",
    "budget_commitments",
    "product_corrections",
    "weekly_plans",
    "planner_checkpoints",
    "planner_checkpoint_writes",
}


def include_name(name, type_, parent_names):
    if type_ == "table":
        return name in OWNED_TABLES
    return True


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return (
            name in OWNED_TABLES
            and obj.schema in (None, "public")
        )
    return True


def run_migrations_offline():
    raise RuntimeError(
        "Offline mode is not configured. "
        "Use the migration database connection."
    )


def run_migrations_online():
    settings = get_settings()
    engine = make_db_engine(settings.migration_database_url)

    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                include_name=include_name,
                include_object=include_object,
                compare_type=True,
                version_table="bonwise_alembic_version",
            )

            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
