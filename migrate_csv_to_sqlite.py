import csv
import os
import secrets
import sqlite3
from datetime import datetime

DB_PATH = "hunarhub.db"
CSV_PATH = "data.csv"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artisans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            craft_type TEXT,
            location TEXT,
            language_pref TEXT DEFAULT 'en',
            session_token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artisan_id INTEGER NOT NULL REFERENCES artisans(id),
            image TEXT,
            name TEXT NOT NULL,
            category TEXT,
            location TEXT,
            description TEXT,
            price TEXT,
            date_added TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artisan_id INTEGER NOT NULL REFERENCES artisans(id),
            role TEXT NOT NULL,
            message TEXT,
            image_path TEXT,
            structured_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artisan_id INTEGER NOT NULL REFERENCES artisans(id),
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL
        );
        """
    )


def get_legacy_artisan_id(conn):
    row = conn.execute(
        "SELECT id FROM artisans WHERE name = ? ORDER BY id LIMIT 1",
        ("Legacy Import",),
    ).fetchone()
    if row:
        return row["id"]

    cursor = conn.execute(
        """
        INSERT INTO artisans (name, craft_type, location, language_pref, session_token, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("Legacy Import", "Imported products", "", "en", secrets.token_hex(16), now_text()),
    )
    return cursor.lastrowid


def migrate():
    if not os.path.exists(CSV_PATH):
        print("No data.csv found; nothing to migrate.")
        return

    with get_connection() as conn:
        init_db(conn)
        artisan_id = get_legacy_artisan_id(conn)

        with open(CSV_PATH, newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            inserted = 0
            for row in reader:
                name = row.get("Name") or row.get("name")
                if not name:
                    continue

                conn.execute(
                    """
                    INSERT INTO products
                        (artisan_id, image, name, category, location, description, price, date_added)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artisan_id,
                        row.get("Image", ""),
                        name,
                        row.get("Category", ""),
                        row.get("Location", ""),
                        row.get("Description", ""),
                        row.get("Price", ""),
                        row.get("Date_Added") or now_text(),
                    ),
                )
                inserted += 1

    print(f"Migrated {inserted} product(s) into {DB_PATH}.")


if __name__ == "__main__":
    migrate()
