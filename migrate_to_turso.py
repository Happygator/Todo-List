import sqlite3
import os
import asyncio
import libsql_client
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
LOCAL_DB = "todo.db"

if not TURSO_URL or not TURSO_TOKEN:
    print("Error: TURSO_DATABASE_URL or TURSO_AUTH_TOKEN not found in .env")
    exit(1)

async def migrate():
    # Fix URL scheme for libsql-client if needed
    if TURSO_URL.startswith("libsql://"):
        url = TURSO_URL.replace("libsql://", "https://")
    else:
        url = TURSO_URL
        
    print(f"Connecting to Turso: {url}...")
    async with libsql_client.create_client(url, auth_token=TURSO_TOKEN) as remote_conn:
        
        print(f"Reading local database: {LOCAL_DB}...")
        local_conn = sqlite3.connect(LOCAL_DB)
        local_cursor = local_conn.cursor()

        # 1. Create Tables in Turso (if they don't exist)
        print("Creating tables in Turso...")
        await remote_conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                due_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigner_id INTEGER
            )
        """)
        
        await remote_conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER,
                key TEXT,
                value TEXT,
                PRIMARY KEY (user_id, key)
            )
        """)
        
        # 2. Migrate Tasks
        print("Migrating tasks...")
        
        local_cursor.execute("SELECT id, user_id, name, due_date, created_at, assigner_id FROM tasks")
        tasks = local_cursor.fetchall()
        
        count = 0
        for task in tasks:
            t_id, user, name, due, created, assigner = task
            # We try to insert with specific ID.
            try:
                await remote_conn.execute(
                    "INSERT INTO tasks (id, user_id, name, due_date, created_at, assigner_id) VALUES (?, ?, ?, ?, ?, ?)",
                    [t_id, user, name, due, created, assigner]
                )
                count += 1
            except Exception as e:
                print(f"Skipping task {t_id} (maybe exists?): {e}")
                
        print(f"Migrated {count} tasks.")

        # 3. Migrate Settings
        print("Migrating settings...")
        local_cursor.execute("SELECT user_id, key, value FROM settings")
        settings = local_cursor.fetchall()
        
        s_count = 0
        for setting in settings:
            u_id, key, val = setting
            try:
                await remote_conn.execute(
                    "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)",
                    [u_id, key, val]
                )
                s_count += 1
            except Exception as e:
                 print(f"Skipping setting {u_id}/{key}: {e}")

        print(f"Migrated {s_count} settings.")
        
        # No explicit commit implementation in http client usually, it's auto-commit or per-request
        print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
