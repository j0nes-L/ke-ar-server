import modal

app = modal.App("ke-ar")
whisper_models_volume = modal.Volume.from_name("whisper-models-vol", create_if_missing=True)
MODELS_DIR = "/models"

whisper_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg")
    .pip_install("openai-whisper>=20231117", "numpy>=1.24.0", "torch>=2.0.0", "torchaudio>=2.0.0")
)


@app.function(image=whisper_image, volumes={MODELS_DIR: whisper_models_volume}, gpu="T4", timeout=600, retries=2)
def transcribe_audio_modal(audio_bytes: bytes, model_name: str = "base") -> dict:
    import tempfile, os, whisper
    os.environ["XDG_CACHE_HOME"] = MODELS_DIR
    
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
        tmp_file.write(audio_bytes)
        tmp_path = tmp_file.name
    
    try:
        model = whisper.load_model(model_name, download_root=MODELS_DIR)
        result = model.transcribe(tmp_path, language=None, verbose=False, word_timestamps=False)
        
        segments = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()} 
                    for s in result.get("segments", [])]
        whisper_models_volume.commit()
        
        return {
            "language": result.get("language", "unknown"),
            "segments": segments,
            "full_text": result.get("text", "").strip(),
            "duration_seconds": segments[-1]["end"] if segments else 0.0
        }
    finally:
        os.unlink(tmp_path)


@app.function(image=whisper_image, volumes={MODELS_DIR: whisper_models_volume}, timeout=300)
def preload_model(model_name: str = "base") -> dict:
    import os, whisper
    os.environ["XDG_CACHE_HOME"] = MODELS_DIR
    whisper.load_model(model_name, download_root=MODELS_DIR)
    whisper_models_volume.commit()
    return {"status": "success", "model": model_name, "message": f"Model {model_name} cached successfully"}


@app.function(image=whisper_image, volumes={MODELS_DIR: whisper_models_volume})
def list_cached_models() -> list:
    import os
    models_path = os.path.join(MODELS_DIR, "whisper")
    return os.listdir(models_path) if os.path.exists(models_path) else []


@app.function(image=whisper_image, volumes={MODELS_DIR: whisper_models_volume})
def clear_model_cache() -> dict:
    import os, shutil
    models_path = os.path.join(MODELS_DIR, "whisper")
    if os.path.exists(models_path):
        shutil.rmtree(models_path)
        whisper_models_volume.commit()
        return {"status": "success", "message": "Model cache cleared"}
    return {"status": "success", "message": "No cache to clear"}


@app.local_entrypoint()
def main(audio_file: str = None, model: str = "base", action: str = "transcribe"):
    if action == "preload":
        print(f"Preloading model: {model}")
        print(preload_model.remote(model))
    elif action == "list":
        print("Cached models:")
        for m in list_cached_models.remote():
            print(f"  - {m}")
    elif action == "clear":
        print("Clearing model cache...")
        print(clear_model_cache.remote())
    elif action == "transcribe" and audio_file:
        print(f"Transcribing {audio_file} with model {model}")
        with open(audio_file, "rb") as f:
            result = transcribe_audio_modal.remote(f.read(), model)
        print(f"Language: {result['language']}, Duration: {result['duration_seconds']}s")
        print(f"Text: {result['full_text'][:200]}...")
    
    else:
        print("Please specify --audio-file or use --action preload/list/clear")
