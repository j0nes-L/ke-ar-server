from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class SessionFile(BaseModel):
    filename: str
    file_type: str
    size_bytes: int


class SessionBase(BaseModel):
    session_id: str
    headset_type: Optional[str] = None
    start_time: Optional[str] = None


class SessionCreate(SessionBase):
    pass


class SessionResponse(SessionBase):
    id: int
    uploaded_at: str
    file_count: int
    total_size_bytes: int


class SessionDetailResponse(SessionResponse):
    files: List[SessionFile]


class SessionListResponse(BaseModel):
    total: int
    sessions: List[SessionResponse]


class UploadResponse(BaseModel):
    success: bool
    session_id: str
    message: str
    files_uploaded: int
    total_size_bytes: int
