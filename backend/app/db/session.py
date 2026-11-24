from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.performance import instrument_engine


def make_db_engine(secret_url, *, pool_size=None):
    url = make_url(secret_url.get_secret_value())
    url = url.set(drivername="postgresql+psycopg")

    if pool_size is None:
        pool_size = getattr(get_settings(), "database_pool_size", 0)
    if type(pool_size) is not int or not 0 <= pool_size <= 5:
        raise ValueError("Database pool size must be an integer from 0 through 5.")
    pool_options = (
        {"poolclass": NullPool} if pool_size == 0 else
        {"pool_size": pool_size, "max_overflow": 0, "pool_timeout": 5,
         "pool_recycle": 300, "pool_pre_ping": True}
    )
    return instrument_engine(create_engine(
        url,
        **pool_options,
        connect_args={
            "prepare_threshold": None,
            "connect_timeout": 10,
            "sslmode": "require",
        },
        hide_parameters=True,
    ))


engine = make_db_engine(get_settings().database_url)

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


def get_db():
    with SessionLocal() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise