from collections.abc import Generator
from datetime import datetime

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(
    settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3}
)
SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def database_utc_now(session: Session) -> datetime:
    """Return PostgreSQL's current UTC timestamp for persisted expiry decisions."""
    value = session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("PostgreSQL did not return its current timestamp")
    return value


def get_db_session() -> Generator[Session, None, None]:
    session = SessionFactory()
    try:
        yield session
        if session.in_transaction():
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
