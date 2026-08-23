from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.agent.llm_client import check_ollama_reachable
from app.api import actions, chat, misc
from app.config import CORS_ORIGINS, DATA_DIR, LOG_LEVEL
from app.db import get_singleton_connection, init_schema
from app.ingestion.loader import run_ingestion
from app.retrieval.retriever import build_index

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("parcelpilot")

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = get_singleton_connection()
    init_schema(conn)
    try:
        summary = run_ingestion(conn, DATA_DIR)
        build_index(conn)
        logger.info("startup ingestion complete: %s", summary)
    except Exception:
        logger.exception("startup ingestion FAILED -- the app will run but chat/data endpoints may error")
    yield


app = FastAPI(title="ParcelPilot AI Support Copilot", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(actions.router)
app.include_router(misc.router)


@app.get("/health")
def health():
    # ai_configured is a boolean only -- never the key, its length, or any
    # prefix/suffix of it. The chat agent being unconfigured is not itself
    # an unhealthy state: ingestion, calculations, and Issue Radar all work
    # without it, so this never affects the response status code.
    # `ai_reachable` is a lightweight connectivity probe (Ollama: list
    # locally installed models; Anthropic: mirrors ai_configured -- a real
    # reachability check would cost money on every /health poll), never a
    # full inference call.
    if config.settings.ai_provider == "ollama":
        ai_reachable = check_ollama_reachable(config.settings.ollama_base_url, config.settings.ollama_model)
    else:
        ai_reachable = config.settings.ai_configured
    ai_fields = {
        "ai_configured": config.settings.ai_configured,
        "ai_provider": config.settings.ai_provider,
        "ai_model": config.settings.active_ai_model,
        "ai_reachable": ai_reachable,
    }
    try:
        conn = get_singleton_connection()
        counts = {
            t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in ("accounts", "orders", "tickets", "document_chunks")
        }
        return {"status": "ok", **ai_fields, "counts": counts}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", **ai_fields, "detail": str(e)},
        )


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
