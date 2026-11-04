from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


def make_db_engine(secret_url):
    url = make_url(secret_url.get_secret_value())
    url = url.set(drivername="postgresql+psycopg")

    return create_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "prepare_threshold": None,
            "connect_timeout": 10,
            "sslmode": "require",
        },
        hide_parameters=True,
    )


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