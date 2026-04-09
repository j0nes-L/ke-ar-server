import json
import asyncio
from pathlib import Path
from typing import AsyncGenerator, Optional
from concurrent.futures import ThreadPoolExecutor

import modal

transcription_progress: dict[str, dict] = {}

_executor = ThreadPoolExecutor(max_workers=2)


def check_audio_file_exists(session_id: str, files_dir: Path) -> dict:
    session_dir = files_dir / session_id
    
    result = {
        "session_id": session_id,
        "audio_file_exists": False,
        "audio_filename": None,
        "transcript_exists": False,
        "transcript_filename": None
    }
    
    if not session_dir.exists():
        return result
    
    audio_files = list(session_dir.glob("*.wav"))
    if audio_files:
        result["audio_file_exists"] = True
        result["audio_filename"] = audio_files[0].name
  
    transcript_path = session_dir / "transcript.json"
    if transcript_path.exists():
        result["transcript_exists"] = True
        result["transcript_filename"] = "transcript.json"
    
    return result


def get_transcription_progress(session_id: str) -> Optional[dict]:
    return transcription_progress.get(session_id)


def _run_modal_transcription(audio_path: str, model_name: str = "base") -> dict:
    transcribe_fn = modal.Function.from_name("ke-ar", "transcribe_audio_modal")
    
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()
    
    result = transcribe_fn.remote(audio_bytes, model_name)
    
    return result


async def transcribe_audio(
    session_id: str, 
    files_dir: Path,
    model_name: str = "base"
) -> AsyncGenerator[dict, None]:
    session_dir = files_dir / session_id
    
    transcription_progress[session_id] = {
        "status": "starting",
        "progress_percent": 0.0,
        "current_step": "Initializing...",
        "error": None
    }
    yield transcription_progress[session_id].copy()
    
    audio_files = list(session_dir.glob("*.wav"))
    if not audio_files:
        transcription_progress[session_id] = {
            "status": "error",
            "progress_percent": 0.0,
            "current_step": "No audio file found",
            "error": "No .wav file found in session directory"
        }
        yield transcription_progress[session_id].copy()
        return
    
    audio_path = audio_files[0]
    
    try:
        transcription_progress[session_id] = {
            "status": "processing",
            "progress_percent": 10.0,
            "current_step": f"Connecting to Modal cloud ({model_name} model)...",
            "error": None
        }
        yield transcription_progress[session_id].copy()
        
        transcription_progress[session_id] = {
            "status": "processing",
            "progress_percent": 20.0,
            "current_step": "Transcribing audio in cloud...",
            "error": None
        }
        yield transcription_progress[session_id].copy()

        loop = asyncio.get_event_loop()
        modal_task = loop.run_in_executor(
            _executor,
            _run_modal_transcription,
            str(audio_path),
            model_name,
        )

        pct = 20.0
        while True:
            done, _ = await asyncio.wait({asyncio.ensure_future(modal_task)}, timeout=10)
            if done:
                break
            pct = min(pct + 3.0, 75.0)
            transcription_progress[session_id] = {
                "status": "processing",
                "progress_percent": round(pct, 1),
                "current_step": "Transcribing audio in cloud...",
                "error": None,
            }
            yield transcription_progress[session_id].copy()

        result = await modal_task

        transcription_progress[session_id] = {
            "status": "processing",
            "progress_percent": 80.0,
            "current_step": "Processing transcription results...",
            "error": None
        }
        yield transcription_progress[session_id].copy()
        
        segments = result.get("segments", [])

        transcript_data = {
            "session_id": session_id,
            "language": result.get("language", "unknown"),
            "duration_seconds": result.get("duration_seconds", 0.0),
            "segments": segments,
            "full_text": result.get("full_text", "")
        }

        transcript_path = session_dir / "transcript.json"
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(transcript_data, f, ensure_ascii=False, indent=2)
        
        transcription_progress[session_id] = {
            "status": "completed",
            "progress_percent": 100.0,
            "current_step": "Transcription complete",
            "error": None,
            "result": transcript_data
        }
        yield transcription_progress[session_id].copy()
        
    except Exception as e:
        transcription_progress[session_id] = {
            "status": "error",
            "progress_percent": 0.0,
            "current_step": "Transcription failed",
            "error": str(e)
        }
        yield transcription_progress[session_id].copy()
    
    finally:
        await asyncio.sleep(60)
        if session_id in transcription_progress:
            del transcription_progress[session_id]


def get_transcript(session_id: str, files_dir: Path) -> Optional[dict]:
    transcript_path = files_dir / session_id / "transcript.json"
    
    if not transcript_path.exists():
        return None
    
    with open(transcript_path, "r", encoding="utf-8") as f:
        return json.load(f)
