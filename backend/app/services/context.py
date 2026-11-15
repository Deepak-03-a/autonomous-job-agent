"""Everything the agent pipeline needs, passed in rather than imported, so the
pipeline can be tested without the web app and run against either database."""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AgentContext:
    postgres: Any | None            # PostgresApplicationStore, or None in SQLite dev mode
    get_db: Callable[[], Any]       # SQLite connection factory (dev mode)
    matcher: Any
    advisor: Any
    embedder: Any | None            # needed to retrieve jobs from PostgreSQL
    planner_llm: Any | None = None  # OllamaClient with a short timeout, or None
    narrator_llm: Any | None = None
    cache: dict = field(default_factory=dict)   # per-run results shared between tools
