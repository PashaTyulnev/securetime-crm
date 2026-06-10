from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from crm.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)

# Enable WAL journal mode for SQLite so background threads can write
# while the web server reads simultaneously without "database is locked" errors.
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def get_session():
    return SessionLocal()
