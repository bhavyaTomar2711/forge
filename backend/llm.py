"""LLM client + helpers.

Centralizes Groq model config so every agent uses the same call site.
Phase 1: planner + coding use the same text model. Vision model wired in
phase 4 for image attachments (per FORGE_BRAIN.md Image/Media Input Handling).
"""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from groq import Groq

# Load .env from backend/ so the API key is available regardless of cwd.
# Safe to call multiple times.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))


# Models per FORGE_BRAIN.md (vision-capable for phase 4). Pin current Groq
# model ids; user can override via env if these deprecate.
TEXT_MODEL = os.getenv("FORGE_TEXT_MODEL", "llama-3.3-70b-versatile")
VISION_MODEL = os.getenv("FORGE_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")


@lru_cache(maxsize=1)
def get_client() -> Groq:
    """Return a cached Groq client. Reads GROQ_API_KEY from env at first call."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "your_groq_api_key_here":
        raise RuntimeError(
            "GROQ_API_KEY not set. Put it in backend/.env (see .env.example)."
        )
    return Groq(api_key=api_key)


def chat_text(system: str, user: str, *, model: str | None = None, max_tokens: int = 1024) -> str:
    """Single-turn text completion. Returns the assistant message text."""
    client = get_client()
    resp = client.chat.completions.create(
        model=model or TEXT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()
