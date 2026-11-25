from contextlib import asynccontextmanager
import os

from fastapi import Depends, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import User, router as auth_router
from app.database import Base, engine, get_db
from app.operations import (
    BoardingTicket,
    CommuterWallet,
    FleetIncident,
    WalletTransaction,
    router as ops_router,
    websocket_events,
    websocket_track,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="Transit Pulse API", lifespan=lifespan)

raw_origins = os.getenv("CORS_ALLOW_ORIGINS", "")
env_origins = [
    origin.strip() for origin in raw_origins.split(",") if origin.strip()
]

allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    *env_origins,
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(ops_router)


@app.get("/")
async def root():
    return {"status": "ok", "service": "Transit Pulse API"}


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.websocket("/ws/track/{trip_id}")
async def track_socket(
    websocket: WebSocket,
    trip_id: int,
    db: AsyncSession = Depends(get_db),
):
    await websocket_track(websocket, trip_id, db)


@app.websocket("/ws/events")
async def events_socket(websocket: WebSocket):
    await websocket_events(websocket)