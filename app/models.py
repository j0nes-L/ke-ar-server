from pydantic import BaseModel
from typing import Optional, List, Any


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


class BinFileCheckResponse(BaseModel):
    session_id: str
    visual_json_exists: bool
    bin_file_exists: bool
    bin_filename: Optional[str] = None
    frame_count: int


class ExtractionStartResponse(BaseModel):
    session_id: str
    status: str
    message: str
    frame_count: int


class ExtractionProgressResponse(BaseModel):
    status: str
    total_frames: int
    current_frame: int
    color_extracted: int
    depth_extracted: int
    progress_percent: float
    errors: List[str]


class ImageStatusResponse(BaseModel):
    session_id: str
    color_available: bool
    depth_available: bool
    color_count: int
    depth_count: int


class ImageListResponse(BaseModel):
    session_id: str
    color_images: List[str]
    depth_images: List[str]
    color_count: int
    depth_count: int
    offset: int
    limit: int


class VisualFrameData(BaseModel):
    timestamp: Optional[str] = None
    timestampMs: Optional[float] = None
    pose: Optional[dict] = None
    distanceAtCenter: Optional[float] = None
    hasColor: bool = False
    hasDepth: bool = False


class TrackingFrameData(BaseModel):
    trackingFrameIndex: Optional[int] = None
    timestamp: Optional[str] = None
    timestampMs: Optional[float] = None
    timeDiffMs: Optional[float] = None
    leftHand: Optional[dict] = None
    rightHand: Optional[dict] = None
    leftEye: Optional[dict] = None
    rightEye: Optional[dict] = None


class FrameMetadataResponse(BaseModel):
    frame_index: int
    visual: Optional[VisualFrameData] = None
    tracking: Optional[TrackingFrameData] = None


class FrameSummary(BaseModel):
    frame_index: int
    timestamp: Optional[str] = None
    timestampMs: Optional[float] = None
    pose: Optional[dict] = None
    distanceAtCenter: Optional[float] = None
    hasColor: bool = False
    hasDepth: bool = False
    hasTracking: bool = False
    leftHandTracked: bool = False
    rightHandTracked: bool = False


class PaginatedFramesResponse(BaseModel):
    session_id: str
    total: int
    offset: int
    limit: int
    frames: List[FrameSummary]


class VisualInfo(BaseModel):
    imageWidth: int
    imageHeight: int
    depthWidth: int
    depthHeight: int
    colorFormat: str
    depthFormat: str
    compression: str
    intrinsics: Optional[dict] = None


class TrackingInfo(BaseModel):
    boneCount: int
    boneNames: List[str]
    handDataFormat: str
    eyeDataFormat: str
    screenVectorFormat: str
    coordinateSystem: str
    captureHandTracking: bool
    captureEyeTracking: bool


class SessionMetadataResponse(BaseModel):
    sessionId: str
    headsetType: str
    startTime: str
    cameraAccessSupported: bool
    depthSupported: bool
    raycastSupported: bool
    binaryFile: Optional[str] = None
    visualInfo: Optional[VisualInfo] = None
    trackingInfo: Optional[TrackingInfo] = None
    totalVisualFrames: int = 0
    totalTrackingFrames: int = 0


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptionCheckResponse(BaseModel):
    session_id: str
    audio_file_exists: bool
    audio_filename: Optional[str] = None
    transcript_exists: bool
    transcript_filename: Optional[str] = None


class TranscriptionStartResponse(BaseModel):
    session_id: str
    status: str
    message: str


class TranscriptionProgressResponse(BaseModel):
    status: str
    progress_percent: float
    current_step: str
    error: Optional[str] = None


class TranscriptionResultResponse(BaseModel):
    session_id: str
    language: str
    duration_seconds: float
    segments: List[TranscriptSegment]
    full_text: str
