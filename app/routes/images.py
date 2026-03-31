from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
import json

from ..image_extractor import (
    extract_images,
    get_extraction_progress,
    check_images_available,
    check_bin_file_exists,
    get_frame_metadata,
    get_frames_metadata_paginated,
    get_session_metadata
)
from ..models import (
    BinFileCheckResponse,
    ExtractionStartResponse,
    ExtractionProgressResponse,
    ImageStatusResponse,
    ImageListResponse,
    FrameMetadataResponse,
    PaginatedFramesResponse,
    SessionMetadataResponse
)

router = APIRouter(prefix="/images", tags=["images"])

FILES_DIR = Path("/app/files")


@router.get("/{session_id}/metadata", response_model=SessionMetadataResponse)
async def get_metadata(session_id: str):
    result = get_session_metadata(session_id, FILES_DIR)
    if not result:
        raise HTTPException(status_code=404, detail="Session not found or visual_data.json missing")
    return SessionMetadataResponse(**result)


@router.get("/{session_id}/check-bin", response_model=BinFileCheckResponse)
async def check_bin_exists(session_id: str):
    result = check_bin_file_exists(session_id, FILES_DIR)
    return BinFileCheckResponse(**result)


@router.post("/{session_id}/extract", response_model=ExtractionStartResponse)
async def start_extraction(session_id: str, background: bool = False):
    bin_check = check_bin_file_exists(session_id, FILES_DIR)
    
    if not bin_check["visual_json_exists"]:
        raise HTTPException(status_code=404, detail="visual_data.json not found for session")
    
    if not bin_check["bin_file_exists"]:
        raise HTTPException(status_code=404, detail=f"Binary file {bin_check['bin_filename']} not found")
    
    if background:
        import asyncio
        async def run_extraction():
            async for _ in extract_images(session_id, FILES_DIR):
                pass
        asyncio.create_task(run_extraction())
        return ExtractionStartResponse(
            session_id=session_id,
            status="started",
            message="Extraction started in background. Use /progress endpoint to track.",
            frame_count=bin_check["frame_count"]
        )
    else:
        final_progress = None
        async for progress in extract_images(session_id, FILES_DIR):
            final_progress = progress
        
        return ExtractionStartResponse(
            session_id=session_id,
            status=final_progress["status"] if final_progress else "unknown",
            message="Extraction completed",
            frame_count=bin_check["frame_count"]
        )


@router.get("/{session_id}/extract/stream")
async def stream_extraction(session_id: str):
    bin_check = check_bin_file_exists(session_id, FILES_DIR)
    
    if not bin_check["visual_json_exists"]:
        raise HTTPException(status_code=404, detail="visual_data.json not found for session")
    
    if not bin_check["bin_file_exists"]:
        raise HTTPException(status_code=404, detail=f"Binary file {bin_check['bin_filename']} not found")
    
    async def event_generator():
        async for progress in extract_images(session_id, FILES_DIR):
            yield f"data: {json.dumps(progress)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/{session_id}/progress", response_model=ExtractionProgressResponse)
async def get_progress(session_id: str):
    progress = get_extraction_progress(session_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="No extraction in progress for this session")
    return ExtractionProgressResponse(**progress)


@router.get("/{session_id}/status", response_model=ImageStatusResponse)
async def get_image_status(session_id: str):
    result = check_images_available(session_id, FILES_DIR)
    return ImageStatusResponse(
        session_id=result["session_id"],
        color_available=result["color_available"],
        depth_available=result["depth_available"],
        color_count=result["color_count"],
        depth_count=result["depth_count"]
    )


@router.get("/{session_id}/list", response_model=ImageListResponse)
async def list_images(session_id: str, limit: int = 0, offset: int = 0):
    result = check_images_available(session_id, FILES_DIR, limit=limit, offset=offset)
    return ImageListResponse(
        session_id=result["session_id"],
        color_images=result["color_images"],
        depth_images=result["depth_images"],
        color_count=result["color_count"],
        depth_count=result["depth_count"],
        offset=result["offset"],
        limit=result["limit"]
    )


@router.get("/{session_id}/color/{filename}")
async def get_color_image(session_id: str, filename: str):
    image_path = FILES_DIR / session_id / "color_images" / filename
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Color image not found")
    return FileResponse(str(image_path), media_type="image/png")


@router.get("/{session_id}/depth/{filename}")
async def get_depth_image(session_id: str, filename: str):
    image_path = FILES_DIR / session_id / "depth_images" / filename
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Depth image not found")
    return FileResponse(str(image_path), media_type="image/png")


@router.get("/{session_id}/frames/{frame_index}/metadata", response_model=FrameMetadataResponse)
async def get_single_frame_metadata(session_id: str, frame_index: int):
    result = get_frame_metadata(session_id, frame_index, FILES_DIR)
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return FrameMetadataResponse(**result)


@router.get("/{session_id}/frames", response_model=PaginatedFramesResponse)
async def get_frames_paginated(session_id: str, limit: int = 20, offset: int = 0):
    result = get_frames_metadata_paginated(session_id, FILES_DIR, limit=limit, offset=offset)
    return PaginatedFramesResponse(**result)
