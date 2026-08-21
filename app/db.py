"""Engine and session. The schema is created by dropping and recreating —
there are no migrations until the parallel run starts (BUILD.md)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)


def database_state() -> str:
    """One sentence about whether the database is there, for the health answer.

    Here rather than in the interface: the HTTP layer asks questions and never
    poses them itself, which is what keeps `sqlalchemy` out of that module
    entirely (SPEC §7).
    """
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return f"reachable at {DATABASE_URL.rsplit('@', 1)[-1]}"
    except Exception as exc:  # the page says so rather than failing to load
        return f"unreachable: {exc.__class__.__name__}"
