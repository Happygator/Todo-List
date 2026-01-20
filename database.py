import libsql_client
import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("TURSO_DATABASE_URL")
DB_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# Normalize URL to https
if DB_URL and DB_URL.startswith("libsql://"):
    DB_URL = DB_URL.replace("libsql://", "https://")

async def get_client():
    return libsql_client.create_client(DB_URL, auth_token=DB_TOKEN)

async def init_db():
    # Tables specific to this app
    async with await get_client() as client:
        await client.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                due_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigner_id TEXT,
                task_type TEXT DEFAULT 'normal'
            )
        """)
        
        await client.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id TEXT,
                key TEXT,
                value TEXT,
                PRIMARY KEY (user_id, key)
            )
        """)

async def migrate_add_task_type_column():
    """Adds task_type column if it doesn't exist."""
    async with await get_client() as client:
        try:
            # Check if column exists by selecting it from one row
            await client.execute("SELECT task_type FROM tasks LIMIT 1")
        except:
            # Likely doesn't exist (LibsqlError or similar)
            print("Migrating: Adding task_type column to tasks table...")
            await client.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'normal'")

async def migrate_to_multi_user():
    # No-op for Turso as we assume schema is correct or handled by migration script/init
    pass

async def fix_date_formats():
    # Can stick to simple update if needed, but avoiding full scan is better.
    # We'll skip this for now or implement if needed. 
    # Turso is remote, minimizing round trips is good.
    pass

async def add_task(user_id: int, name: str, due_date: str = None, assigner_id: int = None, task_type: str = 'normal') -> int:
    if assigner_id is None:
        assigner_id = user_id
        
    async with await get_client() as client:
        # libsql_client usually returns last_insert_rowid in some way? 
        # For HTTP, it might not directly return via cursor.lastrowid
        # We might need to do INSERT RETURNING id if supported (SQLite 3.35+)
        # Turso supports RETURNING.
        
        rs = await client.execute(
            "INSERT INTO tasks (user_id, name, due_date, assigner_id, task_type) VALUES (?, ?, ?, ?, ?) RETURNING id",
            [user_id, name, due_date, assigner_id, task_type]
        )
        return rs.rows[0][0]

async def delete_task(user_id: int, task_id: int) -> bool:
    async with await get_client() as client:
        rs = await client.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", [task_id, user_id])
        return rs.rows_affected > 0

async def delete_tasks(user_id: int, task_ids: list[int]) -> int:
    if not task_ids:
        return 0
    
    placeholders = ",".join(["?"] * len(task_ids))
    sql = f"DELETE FROM tasks WHERE user_id = ? AND id IN ({placeholders})"
    params = [user_id] + task_ids
    
    async with await get_client() as client:
        rs = await client.execute(sql, params)
        return rs.rows_affected

def row_to_dict(row, columns):
    return dict(zip(columns, row))

async def get_top_tasks(user_id: int, limit: int = 5):
    async with await get_client() as client:
        rs = await client.execute(
            """
            SELECT * FROM tasks 
            WHERE user_id = ? 
            ORDER BY 
                CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, 
                due_date ASC, 
                CASE WHEN assigner_id = user_id THEN 0 ELSE 1 END,
                assigner_id ASC
            LIMIT ?
            """, 
            [user_id, limit]
        )
        # Convert to list of dicts for compatibility
        columns = list(rs.columns)
        return [row_to_dict(row, columns) for row in rs.rows]

async def get_tasks_for_reminders(user_id: int):
    """Fetch all tasks with a due date to process for reminders logic (complex types)."""
    async with await get_client() as client:
        # specific query for efficiency: only those with due dates
        rs = await client.execute("SELECT * FROM tasks WHERE user_id = ? AND due_date IS NOT NULL", [user_id])
        columns = list(rs.columns)
        return [row_to_dict(row, columns) for row in rs.rows]
            
async def rollover_past_tasks(user_id: int, target_date: str):
    async with await get_client() as client:
        await client.execute(
            "UPDATE tasks SET due_date = ? WHERE user_id = ? AND due_date < ? AND due_date IS NOT NULL",
            [target_date, user_id, target_date]
        )

async def get_all_undone_tasks_sorted(user_id: int):
    async with await get_client() as client:
        rs = await client.execute(
            """
            SELECT * FROM tasks 
            WHERE user_id = ? 
            ORDER BY 
                CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, 
                due_date ASC, 
                CASE WHEN assigner_id = user_id THEN 0 ELSE 1 END,
                assigner_id ASC,
                id ASC
            """,
            [user_id]
        )
        columns = list(rs.columns)
        return [row_to_dict(row, columns) for row in rs.rows]

async def set_setting(user_id: int, key: str, value: str):
    async with await get_client() as client:
        await client.execute(
            "INSERT OR REPLACE INTO settings (user_id, key, value) VALUES (?, ?, ?)",
            [user_id, key, value]
        )

async def get_setting(user_id: int, key: str) -> str:
    async with await get_client() as client:
        rs = await client.execute("SELECT value FROM settings WHERE user_id = ? AND key = ?", [user_id, key])
        if rs.rows:
            return rs.rows[0][0]
        return None

async def get_users_with_settings():
    """Get all unique user_ids that have settings (e.g. timezone) configured."""
    async with await get_client() as client:
        rs = await client.execute("SELECT DISTINCT user_id FROM settings")
        if not rs.rows:
            return []
        
        # Check if returned as tuples or just values? Usually tuples in rows.
        # rs.rows[0] is (value,) or value?
        # Based on previous check, likely tuple/sequence.
        return [row[0] for row in rs.rows]

async def get_all_unique_users_from_tasks():
    """Get all unique user_ids that have tasks."""
    async with await get_client() as client:
        rs = await client.execute("SELECT DISTINCT user_id FROM tasks")
        if not rs.rows:
            return []
        return [int(row[0]) for row in rs.rows] # Ensure int for consistency, though stored as TEXT

async def ensure_user_initialized(user_id: int):
    """
    Check if user has settings. If not, initialize with defaults:
    Timezone: US/Pacific
    Reminder Time: 08:00
    """
    async with await get_client() as client:
        # Check timezone
        rs_tz = await client.execute("SELECT value FROM settings WHERE user_id = ? AND key = 'timezone'", [user_id])
        if not rs_tz.rows:
            await client.execute(
                "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)",
                [user_id, 'timezone', 'US/Pacific']
            )

        # Check reminder_time
        rs_time = await client.execute("SELECT value FROM settings WHERE user_id = ? AND key = 'reminder_time'", [user_id])
        if not rs_time.rows:
            await client.execute(
                "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)",
                [user_id, 'reminder_time', '08:00']
            )
