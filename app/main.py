"""FastAPI application entry point."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import services
from app.routers import trends, system
from app.database import init_db
from app.config import CORS_ORIGINS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Keeps tracked trend keywords growing on their own; a no-op unless
    # TREND_SNAPSHOT_INTERVAL_HOURS > 0 and keywords are already tracked.
    task = asyncio.create_task(services.trend_snapshot_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="YouTube Trend Analysis", version="2.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Run at import time so the schema exists even when the app is used without
# going through ASGI startup (TestClient, scripts). init_db is idempotent.
# Replaces the deprecated @app.on_event("startup") hook.
init_db()
logger.info("YouTube Trend Analysis API started")


# Mount static files if present
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())


# Include routers
app.include_router(trends.router, prefix="")
app.include_router(system.router, prefix="")
