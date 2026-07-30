import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "data" / "minutregnskab.db"))


def column_names(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def migrate():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "shifts" not in tables:
            return

        columns = column_names(db, "shifts")
        if "share_code" not in columns:
            db.execute("ALTER TABLE shifts ADD COLUMN share_code TEXT")
        if "share_expires_at" not in columns:
            db.execute("ALTER TABLE shifts ADD COLUMN share_expires_at TEXT")

        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_shifts_active_share_code "
            "ON shifts(share_code) WHERE share_code IS NOT NULL"
        )


if __name__ == "__main__":
    migrate()
