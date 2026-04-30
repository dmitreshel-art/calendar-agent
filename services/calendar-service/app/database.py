from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from .config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if settings.database_url.startswith('sqlite') else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def ensure_sqlite_schema_columns(database_url: str | None = None) -> None:
    url = database_url or settings.database_url
    if not url.startswith('sqlite'):
        return
    migration_engine = engine if database_url is None else create_engine(url, connect_args={"check_same_thread": False})
    with migration_engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(employees)").fetchall()}
        if 'role' not in existing:
            conn.exec_driver_sql("ALTER TABLE employees ADD COLUMN role VARCHAR(30) DEFAULT 'user' NOT NULL")


def init_db() -> None:
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_schema_columns()


@contextmanager
def session_scope():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
