import json
import re
import aiosqlite
from fastapi import APIRouter, HTTPException, UploadFile, File, status
from fastapi.responses import FileResponse
from pathlib import Path
from datetime import datetime
from typing import List

from app.models import (
    SessionResponse, 
    SessionDetailResponse, 
    SessionListResponse,
    SessionFile,
    UploadResponse
)
from app.database import DATABASE_PATH

router = APIRouter(prefix="/sessions")

FILES_DIR = Path("/app/files")
ALLOWED_EXTENSIONS = {".json", ".wav", ".bin"}
UUID_PATTERN = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


def extract_session_id(content: bytes, filename: str) -> str | None:
    """Extract sessionId from JSON file content."""
    if not filename.endswith(".json"):
        return None
    
    try:
        if content.startswith(b'\xef\xbb\xbf'):
            content = content[3:]
        
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("utf-8")
        
        data = json.loads(text)
        
        possible_fields = ["sessionId", "session_id", "SessionId", "SESSION_ID", "id"]
        session_id = None
        
        for field in possible_fields:
            session_id = data.get(field)
            if session_id:
                break

        if not session_id and isinstance(data.get("session"), dict):
            session_id = data["session"].get("id") or data["session"].get("sessionId")
        
        if session_id and isinstance(session_id, str) and UUID_PATTERN.match(session_id):
            return session_id
        
        print(f"[DEBUG] {filename}: sessionId field value = {repr(session_id)}")
        
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[DEBUG] {filename}: Parse error: {e}")
    
    return None


def validate_file(filename: str) -> bool:
    """Check if file has allowed extension."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


@router.post("/upload", response_model=UploadResponse)
async def upload_session(files: List[UploadFile] = File(...)):
    """
    Upload a complete session (multiple files).
    All JSON files must contain the same valid sessionId.
    Allowed file types: .json, .wav, .bin
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided"
        )
    
    for file in files:
        if not validate_file(file.filename):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type: {file.filename}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            )

    file_contents = {}
    session_id = None
    headset_type = None
    start_time = None
    
    for file in files:
        content = await file.read()
        file_contents[file.filename] = content
        
        if file.filename.endswith(".json"):
            extracted_id = extract_session_id(content, file.filename)
            
            if extracted_id is None:
                try:
                    text = content.decode("utf-8-sig") if content.startswith(b'\xef\xbb\xbf') else content.decode("utf-8")
                    data = json.loads(text)
                    found_keys = list(data.keys())[:10]
     
                    possible_fields = ["sessionId", "session_id", "SessionId", "SESSION_ID", "id"]
                    found_values = {k: str(data.get(k))[:50] for k in possible_fields if k in data}
                    
                    sid = data.get("sessionId")
                    uuid_valid = bool(sid and isinstance(sid, str) and UUID_PATTERN.match(sid))
                    
                    detail = (
                        f"Could not extract valid sessionId (UUID) from {file.filename}. "
                        f"Found keys: {found_keys}. "
                        f"ID field values: {found_values if found_values else 'none found'}. "
                        f"sessionId exists: {sid is not None}, UUID valid: {uuid_valid}, "
                        f"First 100 bytes: {content[:100]!r}"
                    )
                except Exception as e:
                    detail = f"Could not extract valid sessionId (UUID) from {file.filename}: {str(e)}"
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=detail
                )
            
            if session_id is None:
                session_id = extracted_id
            elif session_id != extracted_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Mismatched sessionId in {file.filename}. Expected {session_id}, got {extracted_id}"
                )
                
            if file.filename == "visual_data.json":
                try:
                    data = json.loads(content.decode("utf-8"))
                    headset_type = data.get("headsetType")
                    start_time = data.get("startTime")
                except:
                    pass

    if session_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No JSON file with valid sessionId found. Upload rejected."
        )

    session_dir = FILES_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Save files
    total_size = 0
    saved_files = []
    
    for filename, content in file_contents.items():
        file_path = session_dir / filename
        file_path.write_bytes(content)
        size = len(content)
        total_size += size
        saved_files.append({
            "filename": filename,
            "file_type": Path(filename).suffix.lower(),
            "size_bytes": size
        })
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT INTO sessions (session_id, headset_type, start_time, uploaded_at, file_count, total_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                headset_type = excluded.headset_type,
                start_time = excluded.start_time,
                uploaded_at = excluded.uploaded_at,
                file_count = excluded.file_count,
                total_size_bytes = excluded.total_size_bytes
        """, (
            session_id,
            headset_type,
            start_time,
            datetime.utcnow().isoformat(),
            len(saved_files),
            total_size
        ))
        
        for f in saved_files:
            await db.execute("""
                INSERT INTO session_files (session_id, filename, file_type, size_bytes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, filename) DO UPDATE SET
                    file_type = excluded.file_type,
                    size_bytes = excluded.size_bytes
            """, (session_id, f["filename"], f["file_type"], f["size_bytes"]))
        
        await db.commit()
    
    return UploadResponse(
        success=True,
        session_id=session_id,
        message=f"Session uploaded successfully",
        files_uploaded=len(saved_files),
        total_size_bytes=total_size
    )


@router.get("", response_model=SessionListResponse)
async def list_sessions(limit: int = 50, offset: int = 0):
    """List all sessions with pagination."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("SELECT COUNT(*) as count FROM sessions")
        row = await cursor.fetchone()
        total = row["count"]
        
        # Get sessions
        cursor = await db.execute("""
            SELECT * FROM sessions 
            ORDER BY uploaded_at DESC 
            LIMIT ? OFFSET ?
        """, (limit, offset))
        rows = await cursor.fetchall()
        
        sessions = [
            SessionResponse(
                id=row["id"],
                session_id=row["session_id"],
                headset_type=row["headset_type"],
                start_time=row["start_time"],
                uploaded_at=row["uploaded_at"],
                file_count=row["file_count"],
                total_size_bytes=row["total_size_bytes"]
            )
            for row in rows
        ]
        
        return SessionListResponse(total=total, sessions=sessions)


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str):
    """Get session details including file list."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Get session
        cursor = await db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", 
            (session_id,)
        )
        session = await cursor.fetchone()
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found"
            )
        
        # Get files
        cursor = await db.execute(
            "SELECT * FROM session_files WHERE session_id = ?",
            (session_id,)
        )
        file_rows = await cursor.fetchall()
        
        files = [
            SessionFile(
                filename=row["filename"],
                file_type=row["file_type"],
                size_bytes=row["size_bytes"]
            )
            for row in file_rows
        ]
        
        return SessionDetailResponse(
            id=session["id"],
            session_id=session["session_id"],
            headset_type=session["headset_type"],
            start_time=session["start_time"],
            uploaded_at=session["uploaded_at"],
            file_count=session["file_count"],
            total_size_bytes=session["total_size_bytes"],
            files=files
        )


@router.get("/{session_id}/files/{filename}")
async def get_session_file(session_id: str, filename: str):
    """Download a specific file from a session."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found"
            )
    
    file_path = FILES_DIR / session_id / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {filename} not found in session {session_id}"
        )
    
    suffix = Path(filename).suffix.lower()
    media_types = {
        ".json": "application/json",
        ".wav": "audio/wav",
        ".bin": "application/octet-stream"
    }
    media_type = media_types.get(suffix, "application/octet-stream")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_type
    )


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and all its files."""
    import shutil
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session_id,)
        )
        if not await cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found"
            )

        await db.execute("DELETE FROM session_files WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await db.commit()
    
    session_dir = FILES_DIR / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)
    
    return {"success": True, "message": f"Session {session_id} deleted"}
