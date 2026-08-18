"""Engine and session. The schema is created by dropping and recreating —
there are no migrations until the parallel run starts (BUILD.md)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
