"""Central configuration for Signal Inbox.

SPEC.md section 8 names the Anthropic API for classification/extraction; by the
owner's later decision those calls route through OpenRouter instead (same
models, different pipe), keyed per task below so any one task can be swapped
without touching code.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = Path(os.environ.get("SIGNAL_DB_PATH", ROOT / "signal.db"))
DEMO = os.environ.get("SIGNAL_DEMO", "0") == "1"

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Task -> OpenRouter model slug. Tier 1 (bulk, structured) on Haiku,
# Tier 2 (user-facing judgment/writing) on Opus.
MODELS = {
    "classify": os.environ.get("MODEL_CLASSIFY", "anthropic/claude-haiku-4.5"),
    "extract": os.environ.get("MODEL_EXTRACT", "anthropic/claude-opus-5"),
    "draft": os.environ.get("MODEL_DRAFT", "anthropic/claude-opus-5"),
    "digest": os.environ.get("MODEL_DIGEST", "anthropic/claude-haiku-4.5"),
}

PROMPTS_DIR = ROOT / "prompts"

# Phase 2 scopes (SPEC 8): readonly for sync, modify strictly for labels
# under the Signal/ prefix, send for replies as the user. Adding scopes
# invalidates an old token.json - delete it and rerun the backfill consent.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

LABEL_PREFIX = "Signal/"
UNDO_SECONDS = 12

CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"

BACKFILL_DAYS = 30
POLL_SECONDS = 60

EMAIL_TYPES = [
    "receipt", "tracking", "alert", "statement", "verification",
    "notice", "offer", "digest", "personal", "other",
]

TIERS = ["person", "service", "feed", "promo", "bot", "notes", "split"]

# Lanes observed in the account (SPEC 3.1). Sources attach as Feed senders
# are classified; digest building is Phase 3.
DEFAULT_LANES = ["Baseball", "Jobs", "Homes", "Markets", "Notebook", "Reads", "Watch & go"]
# Reads and Notebook show no counts at all (SPEC 3.1).
NO_COUNT_LANES = {"Reads", "Notebook"}


def load_prompt(name: str) -> str:
    """Prompts live in their own files so the owner can edit them (PROMPT.md).
    Read at call time so edits apply without a restart."""
    return (PROMPTS_DIR / f"{name}.md").read_text()
