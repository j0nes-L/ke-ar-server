from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.auth import verify_api_key
from app.routes import health, password, sessions, images, transcription
from app.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(
    title="KE-AR API",
    version="1.0.0",
    root_path="/api/ke-ar",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://jonasludorf.dev",
        "https://j0nes-l.github.io",
        "http://localhost:4321",
        "http://localhost:4322",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["Health"])

app.include_router(
    password.router,
    tags=["Password"],
    dependencies=[Depends(verify_api_key)]
)

app.include_router(
    sessions.router,
    tags=["Sessions"],
    dependencies=[Depends(verify_api_key)]
)

app.include_router(
    images.router,
    tags=["Images"],
    dependencies=[Depends(verify_api_key)]
)

app.include_router(
    transcription.router,
    tags=["Transcription"],
    dependencies=[Depends(verify_api_key)]
)
