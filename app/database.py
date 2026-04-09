import aiosqlite
from pathlib import Path

DATABASE_PATH = Path("/app/data/sessions.db")

async def init_db():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                headset_type TEXT,
                start_time TEXT,
                uploaded_at TEXT NOT NULL,
                file_count INTEGER DEFAULT 0,
                total_size_bytes INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id),
                UNIQUE(session_id, filename)
            )
        """)
        await db.commit()

async def get_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def delete_bin_file_entry(session_id: str, bin_filename: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT size_bytes FROM session_files WHERE session_id = ? AND filename = ?",
            (session_id, bin_filename)
        )
        row = await cursor.fetchone()
        
        if row:
            file_size = row[0]
            await db.execute(
                "DELETE FROM session_files WHERE session_id = ? AND filename = ?",
                (session_id, bin_filename)
            )
            await db.execute(
                """UPDATE sessions 
                   SET file_count = file_count - 1, 
                       total_size_bytes = total_size_bytes - ? 
                   WHERE session_id = ?""",
                (file_size, session_id)
            )
            await db.commit()
            return True
    return False
