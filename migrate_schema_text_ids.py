import asyncio
import os
import libsql_client
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if TURSO_URL and TURSO_URL.startswith("libsql://"):
    TURSO_URL = TURSO_URL.replace("libsql://", "https://")

async def migrate_schema():
    print(f"Connecting to {TURSO_URL}...")
    async with libsql_client.create_client(TURSO_URL, auth_token=TURSO_TOKEN) as client:
        
        # 1. Migrate TASKS table
        print("Migrating 'tasks' table...")
        # Create new table with TEXT ids
        await client.execute("DROP TABLE IF EXISTS tasks_new")
        await client.execute("""
            CREATE TABLE tasks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                due_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigner_id TEXT
            )
        """)
        
        # Copy data, casting integers to strings
        # Note: SQLite CAST(col AS TEXT) works reliably
        await client.execute("""
            INSERT INTO tasks_new (id, user_id, name, due_date, created_at, assigner_id)
            SELECT id, CAST(user_id AS TEXT), name, due_date, created_at, CAST(assigner_id AS TEXT)
            FROM tasks
        """)
        
        # Swap tables
        await client.execute("DROP TABLE tasks")
        await client.execute("ALTER TABLE tasks_new RENAME TO tasks")
        print("Tasks table migrated.")

        # 2. Migrate SETTINGS table
        print("Migrating 'settings' table...")
        await client.execute("DROP TABLE IF EXISTS settings_new")
        await client.execute("""
            CREATE TABLE settings_new (
                user_id TEXT,
                key TEXT,
                value TEXT,
                PRIMARY KEY (user_id, key)
            )
        """)
        
        await client.execute("""
            INSERT INTO settings_new (user_id, key, value)
            SELECT CAST(user_id AS TEXT), key, value
            FROM settings
        """)
        
        await client.execute("DROP TABLE settings")
        await client.execute("ALTER TABLE settings_new RENAME TO settings")
        print("Settings table migrated.")
        
    print("Schema migration complete! IDs are now stored as TEXT.")

if __name__ == "__main__":
    asyncio.run(migrate_schema())
