"""Application configuration -- the single source of truth for environment
variables in this backend.

Root cause of the "ANTHROPIC_API_KEY is missing" bug this module fixes:
`load_dotenv()` (no path argument) resolves relative to python-dotenv's own
frame-walking heuristic, which is easy to get wrong depending on how/where
the process is launched (`uvicorn app.main:app` from `backend/`, from the
repo root, from a systemd unit, from Docker's WORKDIR, ...). We now load
`.env` from an ABSOLUTE path derived from this file's own location, so it
is completely independent of the current working directory.

Every other module that needs a value which can change at runtime (in
practice: tests calling `reload_settings()` after monkeypatching the
environment) must import THIS MODULE, not the `settings` object by value --
i.e. `from app import config` then read `config.settings.<field>` at the
point of use. Importing `from app.config import settings` binds a local
name to whatever object `settings` pointed to at import time; a later
`reload_settings()` call reassigns the module-level `settings` name to a
*new* object, which that old local binding will never see. This was the
root architectural bug behind the original "ANTHROPIC_API_KEY is missing"
report: `app/agent/llm_client.py` used to do `from app.config import
ANTHROPIC_API_KEY`, copying the value once at import time, so it could
silently diverge from `app.config`'s own copy. See `AnthropicLLMClient` in
`app/agent/llm_client.py`, `app/main.py`, and `app/api/misc.py` for the
correct pattern.

No secret is ever hardcoded, logged, or included in any exception message,
API response, or `repr()`/`str()` of `Settings`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("parcelpilot.config")

BACKEND_DIR = Path(__file__).resolve().parent.parent  # .../backend
PROJECT_ROOT = BACKEND_DIR.parent  # repo root
DATA_DIR = BACKEND_DIR / "data"


def dotenv_candidates() -> list[Path]:
    """Absolute paths, in priority order, checked for a `.env` file.

    Both are derived from THIS file's own resolved location
    (`Path(__file__).resolve()`), not the process's current working
    directory -- that's what makes `.env` discovery independent of whether
    the app is launched as `uvicorn app.main:app` from `backend/`, from the
    repo root, from a Docker WORKDIR, or anywhere else.
    """
    return [PROJECT_ROOT / ".env", BACKEND_DIR / ".env"]


def load_env_files(candidates: list[Path] | None = None) -> None:
    """Load the first-found `.env` file(s) from `candidates` (project root
    preferred, then backend/ as a convenience override). Never overrides an
    already-exported real environment variable (e.g. one set by a container
    platform or CI secret), and never raises if a file is absent -- a
    missing `.env` is a normal, supported configuration (see
    `Settings.ai_configured` / graceful-degradation behavior below).
    """
    for path in candidates if candidates is not None else dotenv_candidates():
        if path.is_file():
            load_dotenv(dotenv_path=path, override=False)


load_env_files()


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of configuration read from the environment.

    Access via the module-level `settings` instance below. Use
    `reload_settings()` (tests only) to rebuild it after changing
    environment variables at runtime.
    """

    anthropic_api_key: str
    anthropic_model: str
    ai_provider: str
    ollama_base_url: str
    ollama_model: str
    ollama_timeout_s: float
    ollama_num_predict: int
    ollama_keep_alive: str
    db_path: Path
    max_tool_call_depth: int
    business_hours_start: int
    business_hours_end: int
    cors_origins: list[str]
    log_level: str

    @property
    def ai_configured(self) -> bool:
        """True if a (non-empty) Anthropic API key is configured. Kept
        specifically about the Anthropic key (unrelated to `ai_provider`)
        for backward compatibility with existing callers/tests. Safe to
        expose to clients (e.g. via /health) -- never derive a boolean from
        the key's value in a way that could leak length/prefix/etc."""
        return bool(self.anthropic_api_key)

    @property
    def active_ai_model(self) -> str:
        """The model id actually in use for the currently selected
        `ai_provider` -- what `/health` should display."""
        return self.ollama_model if self.ai_provider == "ollama" else self.anthropic_model

    def __repr__(self) -> str:  # never let a stray print()/log/debugger leak the key
        return (
            f"Settings(ai_configured={self.ai_configured}, ai_provider={self.ai_provider!r}, "
            f"anthropic_model={self.anthropic_model!r}, ollama_model={self.ollama_model!r}, "
            f"db_path={str(self.db_path)!r}, max_tool_call_depth={self.max_tool_call_depth}, "
            f"log_level={self.log_level!r})"
        )

    __str__ = __repr__


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def load_settings() -> Settings:
    """Build a Settings instance from the current process environment.

    `AI_PROVIDER` selects which LLM backend `app.api.chat` talks to: "ollama"
    (the default for this repo's `.env` -- a local, zero-cost model, no
    Anthropic key required) or "anthropic" (the original paid cloud path,
    still fully supported -- e.g. for CI/tests/production, which pin it
    explicitly). The code-level fallback here is deliberately "anthropic",
    not "ollama": it's what every pre-existing caller/test that never sets
    AI_PROVIDER at all was already written to assume, so an *absent* env var
    reproduces the original behavior exactly. This machine's `.env` sets
    AI_PROVIDER=ollama explicitly to opt into local mode.
    """
    ai_provider = os.getenv("AI_PROVIDER", "anthropic").strip().lower()
    if ai_provider not in ("anthropic", "ollama"):
        logger.warning("Unknown AI_PROVIDER=%r; falling back to 'anthropic'.", ai_provider)
        ai_provider = "anthropic"

    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
    if ai_provider == "ollama":
        host = ollama_base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if host not in _LOCAL_HOSTS:
            # Not a hard error -- some setups do run Ollama on a separate
            # trusted host -- but this must never be able to be
            # api.anthropic.com, so it's loud in the logs either way.
            logger.warning(
                "AI_PROVIDER=ollama but OLLAMA_BASE_URL=%r is not localhost. Verify this is intentional.",
                ollama_base_url,
            )

    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929").strip(),
        ai_provider=ai_provider,
        ollama_base_url=ollama_base_url,
        ollama_model=os.getenv("AI_MODEL", "qwen2.5:1.5b").strip(),
        ollama_timeout_s=float(os.getenv("OLLAMA_TIMEOUT_S", "180")),
        # Concise answers are the system prompt's own instruction, so capping
        # generation length trims the worst-case tail without changing
        # typical output; a runaway generation is the main thing this guards
        # against on slow hardware.
        ollama_num_predict=int(os.getenv("OLLAMA_NUM_PREDICT", "512")),
        # How long Ollama keeps the model loaded in RAM after a request.
        # Longer avoids paying the ~15s+ reload cost on every chat message;
        # only worth shortening if RAM is needed for other apps between uses.
        ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "30m").strip(),
        db_path=Path(os.getenv("PARCELPILOT_DB_PATH", str(BACKEND_DIR / "parcelpilot.db"))),
        max_tool_call_depth=int(os.getenv("PARCELPILOT_MAX_TOOL_DEPTH", "8")),
        business_hours_start=int(os.getenv("PARCELPILOT_BUSINESS_HOURS_START", "9")),
        business_hours_end=int(os.getenv("PARCELPILOT_BUSINESS_HOURS_END", "18")),
        cors_origins=_split_origins(os.getenv("PARCELPILOT_CORS_ORIGINS", "*")),
        log_level=os.getenv("PARCELPILOT_LOG_LEVEL", "INFO"),
    )


settings = load_settings()


def reload_settings() -> Settings:
    """Rebuild and replace the module-level `settings` object in place.

    Intended for tests that change environment variables at runtime (e.g.
    via `monkeypatch.setenv`) and need every consumer -- all of which read
    `config.settings.<field>` through this module's namespace, not a copied
    local -- to immediately observe the new value. Production code never
    needs to call this; configuration is fixed for the life of the process.
    """
    global settings
    settings = load_settings()
    logger.info(
        "AI provider=%s active_model=%s (anthropic_configured=%s)",
        settings.ai_provider, settings.active_ai_model, settings.ai_configured,
    )
    return settings


logger.info(
        "AI provider=%s active_model=%s (anthropic_configured=%s)",
        settings.ai_provider, settings.active_ai_model, settings.ai_configured,
    )

# --- Backward-compatible flat constants -------------------------------
# These mirror `settings` for modules that only ever need the value fixed
# at process startup (never reloaded mid-test), which keeps their imports
# simple. ANTHROPIC_API_KEY / ANTHROPIC_MODEL are deliberately NOT mirrored
# here -- see the module docstring -- consumers that need those must read
# `settings.anthropic_api_key` / `settings.anthropic_model` dynamically.
DB_PATH = settings.db_path
MAX_TOOL_CALL_DEPTH = settings.max_tool_call_depth

# Demo business-hours calendar. The supplied source pack does NOT define an
# authoritative business-hours calendar, so this is an explicit, labeled,
# configurable assumption used only to *estimate* business-hour SLA due
# times. It must never be presented as authoritative. See docs/ARCHITECTURE.md.
BUSINESS_HOURS_START = settings.business_hours_start
BUSINESS_HOURS_END = settings.business_hours_end
BUSINESS_DAYS = {0, 1, 2, 3, 4}  # Mon-Fri, Python weekday() convention
DATASET_TZ = "Asia/Kolkata"

CORS_ORIGINS = settings.cors_origins
LOG_LEVEL = settings.log_level
