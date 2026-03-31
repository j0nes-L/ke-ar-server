from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.auth import verify_api_key
from app.routes import health, password

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("KE-AR API starting...")
    yield
    print("KE-AR API shutting down...")

app = FastAPI(
    title="KE-AR API",
    version="1.0.0",
    root_path="/api/ke-ar",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
