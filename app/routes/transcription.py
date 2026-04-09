import json
import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..audio_transcription import (
    check_audio_file_exists,
    transcribe_audio,
    get_transcription_progress,
    get_transcript
)
from ..models import (
    TranscriptionCheckResponse,
    TranscriptionStartResponse,
    TranscriptionProgressResponse,
    TranscriptionResultResponse
)

router = APIRouter(prefix="/transcription", tags=["transcription"])

FILES_DIR = Path("/app/files")


@router.get("/{session_id}/check", response_model=TranscriptionCheckResponse)
async def check_audio(session_id: str):
    result = check_audio_file_exists(session_id, FILES_DIR)
    return TranscriptionCheckResponse(**result)


@router.post("/{session_id}/transcribe", response_model=TranscriptionStartResponse)
async def start_transcription(
    session_id: str, 
    background: bool = False,
    model: str = "base"
):
    valid_models = ["tiny", "base", "small", "medium", "large"]
    if model not in valid_models:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid model. Choose from: {', '.join(valid_models)}"
        )
    
    check = check_audio_file_exists(session_id, FILES_DIR)
    if not check["audio_file_exists"]:
        raise HTTPException(status_code=404, detail="No audio file found for this session")
    
    if background:
        async def run_transcription():
            async for _ in transcribe_audio(session_id, FILES_DIR, model):
                pass
        asyncio.create_task(run_transcription())
        
        return TranscriptionStartResponse(
            session_id=session_id,
            status="started",
            message="Transcription started in background. Use /progress endpoint to track."
        )
    else:
        final_progress = None
        async for progress in transcribe_audio(session_id, FILES_DIR, model):
            final_progress = progress
        
        if final_progress and final_progress.get("status") == "error":
            raise HTTPException(
                status_code=500,
                detail=final_progress.get("error", "Transcription failed")
            )
        
        return TranscriptionStartResponse(
            session_id=session_id,
            status=final_progress["status"] if final_progress else "unknown",
            message="Transcription completed successfully"
        )


@router.get("/{session_id}/transcribe/stream")
async def stream_transcription(session_id: str, model: str = "base"):
    valid_models = ["tiny", "base", "small", "medium", "large"]
    if model not in valid_models:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid model. Choose from: {', '.join(valid_models)}"
        )
    
    check = check_audio_file_exists(session_id, FILES_DIR)
    if not check["audio_file_exists"]:
        raise HTTPException(status_code=404, detail="No audio file found for this session")
    
    async def event_generator():
        async for progress in transcribe_audio(session_id, FILES_DIR, model):
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


@router.get("/{session_id}/progress", response_model=TranscriptionProgressResponse)
async def get_progress(session_id: str):
    progress = get_transcription_progress(session_id)
    if progress is None:
        raise HTTPException(
            status_code=404, 
            detail="No transcription in progress for this session"
        )
    return TranscriptionProgressResponse(**{
        k: v for k, v in progress.items() if k != "result"
    })


@router.get("/{session_id}/result", response_model=TranscriptionResultResponse)
async def get_transcription_result(session_id: str):
    transcript = get_transcript(session_id, FILES_DIR)
    if transcript is None:
        raise HTTPException(
            status_code=404,
            detail="No transcript found for this session. Run transcription first."
        )
    return TranscriptionResultResponse(**transcript)
