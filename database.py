import aiosqlite
import datetime
import os

DB_NAME = "todo.db"

# Hardcoded default user ID for migration
DEFAULT_USER_ID = 342869056203784202

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Check if we need to migrate 'tasks'
        # Simple check: does 'user_id' column exist?
        cursor = await db.execute("PRAGMA table_info(tasks)")
        columns = [row[1] for row in await cursor.fetchall()]
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                due_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        if 'user_id' not in columns and 'tasks' in columns: # Wait, if tasks exists but no user_id
             # This means it's the old schema. We need to handle this manually or via migration script.
             # But init_db CREATE TABLE IF NOT EXISTS won't alter it.
             pass

        # Check 'settings' table
        cursor = await db.execute("PRAGMA table_info(settings)")
        settings_columns = [row[1] for row in await cursor.fetchall()]

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER,
                key TEXT,
                value TEXT,
                PRIMARY KEY (user_id, key)
            )
        """)
        
        await db.commit()

async def migrate_to_multi_user():
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Migrate TASKS
        cursor = await db.execute("PRAGMA table_info(tasks)")
        columns = [row[1] for row in await cursor.fetchall()]
        
        if 'user_id' not in columns:
            print("Migrating tasks table to multi-user...")
            await db.execute("ALTER TABLE tasks ADD COLUMN user_id INTEGER")
            await db.execute("UPDATE tasks SET user_id = ?", (DEFAULT_USER_ID,))
            await db.commit()
            print("Tasks table migrated.")
            
        # 2. Migrate SETTINGS
        cursor = await db.execute("PRAGMA table_info(settings)")
        columns = [row[1] for row in await cursor.fetchall()]
        
        if 'user_id' not in columns:
            print("Migrating settings table to multi-user...")
            await db.execute("ALTER TABLE settings RENAME TO settings_old")
            await db.execute("""
                CREATE TABLE settings (
                    user_id INTEGER,
                    key TEXT,
                    value TEXT,
                    PRIMARY KEY (user_id, key)
                )
            """)
            # Copy old settings to default user
            await db.execute(
                "INSERT INTO settings (user_id, key, value) SELECT ?, key, value FROM settings_old", 
                (DEFAULT_USER_ID,)
            )
            await db.execute("DROP TABLE settings_old")
            await db.commit()
            print("Settings table migrated.")

async def fix_date_formats():
    """Normalize all dates to ISO 8601 (YYYY-MM-DD) to ensure correct sorting."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, due_date FROM tasks WHERE due_date IS NOT NULL") as cursor:
            tasks = await cursor.fetchall()
            
        for task in tasks:
            original = task['due_date']
            try:
                # Try parsing as YYYY-MM-DD
                dt = datetime.datetime.strptime(original, "%Y-%m-%d")
                iso = dt.date().isoformat()
                if iso != original:
                    await db.execute("UPDATE tasks SET due_date = ? WHERE id = ?", (iso, task['id']))
            except ValueError:
                pass
        await db.commit()

async def add_task(user_id: int, name: str, due_date: str = None) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO tasks (user_id, name, due_date) VALUES (?, ?, ?)",
            (user_id, name, due_date)
        )
        await db.commit()
        return cursor.lastrowid

async def delete_task(user_id: int, task_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        # Enforce user_id check so users can't delete each other's tasks
        cursor = await db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
        await db.commit()
        return cursor.rowcount > 0

async def get_top_tasks(user_id: int, limit: int = 5):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        # Order by due_date ASC, with NULLs last
        async with db.execute(
            "SELECT * FROM tasks WHERE user_id = ? ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date ASC LIMIT ?", 
            (user_id, limit)
        ) as cursor:
            return await cursor.fetchall()

async def get_tasks_for_reminders(user_id: int, target_date: str):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE user_id = ? AND due_date = ?", (user_id, target_date)) as cursor:
            return await cursor.fetchall()
            
async def rollover_past_tasks(user_id: int, target_date: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE tasks SET due_date = ? WHERE user_id = ? AND due_date < ? AND due_date IS NOT NULL",
            (target_date, user_id, target_date)
        )
        await db.commit()

async def get_all_undone_tasks_sorted(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tasks WHERE user_id = ? ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date ASC, id ASC",
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()

async def set_setting(user_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, value)
        )
        await db.commit()

async def get_setting(user_id: int, key: str) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE user_id = ? AND key = ?", (user_id, key)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_users_with_settings():
    """Get all unique user_ids that have settings (e.g. timezone) configured."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT DISTINCT user_id FROM settings") as cursor:
            return [row['user_id'] for row in await cursor.fetchall()]
