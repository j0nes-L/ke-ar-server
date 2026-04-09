import json
import struct
import shutil
import uuid as _uuid
import aiosqlite
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from app.models import (
    SessionResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionFile,
    UploadResponse,
)
from app.database import DATABASE_PATH

router = APIRouter(prefix="/sessions")

FILES_DIR = Path("/app/files")
CHUNK_DIR = FILES_DIR / ".chunks"
TMP_DIR = FILES_DIR / ".tmp"
ALLOWED_EXTENSIONS = {".json", ".wav", ".bin"}
STREAM_CHUNK_SIZE = 1024 * 1024

NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def validate_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def sanitize_filename(filename: str) -> str:
    return Path(filename).name


def is_safe_session_id(s: str) -> bool:
    if not s or len(s) < 8 or len(s) > 250:
        return False
    return all(0x20 <= ord(c) <= 0x7e and c not in '/\\:*?"<>|' for c in s)


def extract_session_id_from_json(
    content: bytes, filename: str
) -> tuple[str | None, str | None, str | None]:
    try:
        if content.startswith(b"\xef\xbb\xbf"):
            content = content[3:]
        text = content.decode("utf-8-sig")
        data = json.loads(text)

        possible_fields = ["sessionId", "session_id", "SessionId", "SESSION_ID", "id"]
        session_id = None
        for field in possible_fields:
            session_id = data.get(field)
            if session_id:
                break
        if not session_id and isinstance(data.get("session"), dict):
            session_id = data["session"].get("id") or data["session"].get("sessionId")

        headset_type = data.get("headsetType")
        start_time = data.get("startTime")

        if session_id and isinstance(session_id, str):
            return session_id, headset_type, start_time
    except Exception:
        pass
    return None, None, None


def extract_session_id_from_bin(file_path: Path) -> str | None:
    try:
        with open(file_path, "rb") as f:
            header = f.read(260)
        if len(header) < 4:
            return None
        length = struct.unpack("<i", header[:4])[0]
        if length <= 0 or length > 250 or length + 4 > len(header):
            return None
        session_id = header[4 : 4 + length].decode("utf-8")
        if len(session_id) >= 8 and all(0x20 <= ord(c) <= 0x7E for c in session_id):
            return session_id
    except Exception:
        pass
    return None


def extract_session_id_from_wav(file_path: Path) -> str | None:
    try:
        file_size = file_path.stat().st_size
        with open(file_path, "rb") as f:
            header = f.read(min(file_size, 4096))
        if len(header) < 44:
            return None
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None

        offset = 12
        while offset + 8 <= len(header):
            chunk_id = header[offset : offset + 4]
            chunk_size = struct.unpack("<I", header[offset + 4 : offset + 8])[0]
            if chunk_size > file_size:
                break
            if chunk_id == b"seid":
                available = len(header) - (offset + 8)
                id_len = min(chunk_size, available)
                if id_len <= 0:
                    return None
                session_id = header[offset + 8 : offset + 8 + id_len].decode(
                    "utf-8", errors="replace"
                )
                session_id = session_id.rstrip("\x00")
                return session_id or None
            advance = 8 + chunk_size + (chunk_size % 2)
            if advance == 0:
                break
            offset += advance
    except Exception:
        pass
    return None


async def stream_upload_to_disk(upload: UploadFile, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await upload.read(STREAM_CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    return total


def assemble_chunks(chunk_dir: Path, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(dest, "wb") as out:
        for part in sorted(chunk_dir.glob("chunk_*")):
            with open(part, "rb") as inp:
                while True:
                    data = inp.read(STREAM_CHUNK_SIZE)
                    if not data:
                        break
                    out.write(data)
                    total += len(data)
    return total


async def register_file_in_db(
    session_id: str,
    filename: str,
    file_size: int,
    headset_type: str | None = None,
    start_time: str | None = None,
):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        file_type = Path(filename).suffix.lower()
        await db.execute(
            """
            INSERT INTO session_files (session_id, filename, file_type, size_bytes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id, filename) DO UPDATE SET
                file_type = excluded.file_type,
                size_bytes = excluded.size_bytes
            """,
            (session_id, filename, file_type, file_size),
        )

        cursor = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) "
            "FROM session_files WHERE session_id = ?",
            (session_id,),
        )
        count, total_size = await cursor.fetchone()

        now = datetime.utcnow().isoformat()

        cursor = await db.execute(
            "SELECT headset_type, start_time FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        existing = await cursor.fetchone()

        if existing:
            final_ht = headset_type or existing[0]
            final_st = start_time or existing[1]
            await db.execute(
                """
                UPDATE sessions
                   SET headset_type = ?, start_time = ?, uploaded_at = ?,
                       file_count = ?, total_size_bytes = ?
                 WHERE session_id = ?
                """,
                (final_ht, final_st, now, count, total_size, session_id),
            )
        else:
            await db.execute(
                """
                INSERT INTO sessions
                    (session_id, headset_type, start_time, uploaded_at, file_count, total_size_bytes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, headset_type, start_time, now, count, total_size),
            )

        await db.commit()


@router.post("/upload", response_model=UploadResponse)
async def upload_session(
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = Form(None),
    chunk_index: Optional[int] = Form(None),
    total_chunks: Optional[int] = Form(None),
    original_filename: Optional[str] = Form(None),
):
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    is_chunked = (
        chunk_index is not None
        and total_chunks is not None
        and original_filename is not None
    )

    if is_chunked:
        return await _handle_chunked_upload(
            files[0], session_id, chunk_index, total_chunks, original_filename
        )

    return await _handle_normal_upload(files, session_id)


async def _handle_normal_upload(
    files: List[UploadFile],
    form_session_id: str | None,
) -> UploadResponse:
    for f in files:
        safe_name = sanitize_filename(f.filename)
        if not validate_file(safe_name):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type: {f.filename}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
            )

    tmp_id = _uuid.uuid4().hex
    tmp_dir = TMP_DIR / tmp_id
    tmp_dir.mkdir(parents=True, exist_ok=True)

    saved: list[tuple[str, int, Path]] = []
    try:
        for f in files:
            fname = sanitize_filename(f.filename)
            dest = tmp_dir / fname
            size = await stream_upload_to_disk(f, dest)
            saved.append((fname, size, dest))

        sid = form_session_id
        headset_type: str | None = None
        start_time: str | None = None

        if not sid:
            for fname, _, path in saved:
                if fname.lower().endswith(".json"):
                    content = path.read_bytes()
                    _sid, ht, st = extract_session_id_from_json(content, fname)
                    if _sid:
                        sid, headset_type, start_time = _sid, ht, st
                        break

            if not sid:
                for fname, _, path in saved:
                    if fname.lower().endswith(".bin"):
                        _sid = extract_session_id_from_bin(path)
                        if _sid:
                            sid = _sid
                            break

            if not sid:
                for fname, _, path in saved:
                    if fname.lower().endswith(".wav"):
                        _sid = extract_session_id_from_wav(path)
                        if _sid:
                            sid = _sid
                            break
        else:
            for fname, _, path in saved:
                if fname.lower() == "visual_data.json":
                    try:
                        data = json.loads(path.read_bytes().decode("utf-8"))
                        headset_type = data.get("headsetType")
                        start_time = data.get("startTime")
                    except Exception:
                        pass
                    break

        if not sid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not determine session ID. "
                    "Provide a session_id form field or include a .json, .bin, or .wav "
                    "file with an embedded session ID."
                ),
            )

        if not is_safe_session_id(sid):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid session ID format: {sid!r}",
            )

        session_dir = FILES_DIR / sid
        session_dir.mkdir(parents=True, exist_ok=True)

        total_size = 0
        for fname, size, tmp_path in saved:
            final_path = session_dir / fname
            shutil.move(str(tmp_path), str(final_path))
            total_size += size

        for fname, size, _ in saved:
            await register_file_in_db(sid, fname, size, headset_type, start_time)

        return UploadResponse(
            success=True,
            session_id=sid,
            message="Session uploaded successfully",
            files_uploaded=len(saved),
            total_size_bytes=total_size,
        )
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def _handle_chunked_upload(
    file: UploadFile,
    form_session_id: str | None,
    chunk_index: int,
    total_chunks: int,
    original_filename: str,
) -> UploadResponse:
    original_filename = sanitize_filename(original_filename)
    if not validate_file(original_filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type: {original_filename}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    chunk_key = (
        form_session_id
        if form_session_id and is_safe_session_id(form_session_id)
        else "pending"
    )
    chunk_dir = CHUNK_DIR / chunk_key / original_filename
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunk_path = chunk_dir / f"chunk_{chunk_index:06d}"
    chunk_size = await stream_upload_to_disk(file, chunk_path)

    existing_chunks = list(chunk_dir.glob("chunk_*"))
    if len(existing_chunks) < total_chunks:
        return UploadResponse(
            success=True,
            session_id=form_session_id or "pending",
            message=f"Chunk {chunk_index + 1}/{total_chunks} received",
            files_uploaded=0,
            total_size_bytes=chunk_size,
        )

    sid = form_session_id

    if not sid:
        first_chunk = chunk_dir / "chunk_000000"
        if first_chunk.exists():
            if original_filename.lower().endswith(".bin"):
                sid = extract_session_id_from_bin(first_chunk)
            elif original_filename.lower().endswith(".wav"):
                sid = extract_session_id_from_wav(first_chunk)

    if not sid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All chunks received but could not determine session ID. Provide session_id form field.",
        )

    if not is_safe_session_id(sid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid session ID format: {sid!r}",
        )

    session_dir = FILES_DIR / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    final_path = session_dir / original_filename

    total_size = assemble_chunks(chunk_dir, final_path)

    shutil.rmtree(CHUNK_DIR / chunk_key, ignore_errors=True)

    await register_file_in_db(sid, original_filename, total_size)

    return UploadResponse(
        success=True,
        session_id=sid,
        message=f"File {original_filename} uploaded ({total_chunks} chunks assembled)",
        files_uploaded=1,
        total_size_bytes=total_size,
    )


@router.get("")
async def list_sessions(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("SELECT COUNT(*) as count FROM sessions")
        row = await cursor.fetchone()
        total = row["count"]

        cursor = await db.execute(
            """
            SELECT s.*,
                   COALESCE(f.cnt, 0) AS real_file_count,
                   COALESCE(f.sz, 0)  AS real_total_size
              FROM sessions s
              LEFT JOIN (
                  SELECT session_id,
                         COUNT(*)              AS cnt,
                         COALESCE(SUM(size_bytes), 0) AS sz
                    FROM session_files
                   GROUP BY session_id
              ) f ON f.session_id = s.session_id
             ORDER BY s.uploaded_at DESC
             LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()

        sessions = [
            SessionResponse(
                id=row["id"],
                session_id=row["session_id"],
                headset_type=row["headset_type"],
                start_time=row["start_time"],
                uploaded_at=row["uploaded_at"],
                file_count=row["real_file_count"],
                total_size_bytes=row["real_total_size"],
            )
            for row in rows
        ]

        body = SessionListResponse(total=total, sessions=sessions)
        return JSONResponse(content=body.model_dump(), headers=NO_CACHE_HEADERS)


@router.get("/{session_id}")
async def get_session(session_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        session = await cursor.fetchone()

        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found",
            )

        cursor = await db.execute(
            "SELECT * FROM session_files WHERE session_id = ?",
            (session_id,),
        )
        file_rows = await cursor.fetchall()

        files = [
            SessionFile(
                filename=row["filename"],
                file_type=row["file_type"],
                size_bytes=row["size_bytes"],
            )
            for row in file_rows
        ]

        body = SessionDetailResponse(
            id=session["id"],
            session_id=session["session_id"],
            headset_type=session["headset_type"],
            start_time=session["start_time"],
            uploaded_at=session["uploaded_at"],
            file_count=len(files),
            total_size_bytes=sum(f.size_bytes for f in files),
            files=files,
        )
        return JSONResponse(content=body.model_dump(), headers=NO_CACHE_HEADERS)


@router.get("/{session_id}/files/{filename}")
async def get_session_file(session_id: str, filename: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        if not await cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found",
            )

    file_path = FILES_DIR / session_id / filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {filename} not found in session {session_id}",
        )

    file_size = file_path.stat().st_size
    suffix = Path(filename).suffix.lower()
    media_types = {
        ".json": "application/json",
        ".wav": "audio/wav",
        ".bin": "application/octet-stream",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    def _iter_file():
        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type=media_type,
        headers={
            "Content-Length": str(file_size),
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        if not await cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id} not found",
            )

        await db.execute("DELETE FROM session_files WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await db.commit()

    session_dir = FILES_DIR / session_id
    if session_dir.exists():
        shutil.rmtree(session_dir)

    return {"success": True, "message": f"Session {session_id} deleted"}
