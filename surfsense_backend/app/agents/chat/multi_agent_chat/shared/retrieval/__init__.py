"""Knowledge-base retrieval: hybrid search rendered as citable evidence.

Public surface is the service (``search_knowledge_base_context``) and its input
value object (``SearchScope``); the rest are building blocks.
"""

from __future__ import annotations

from .models import ChunkHit, DocumentHit, SearchScope
from .service import (
    DEFAULT_TOP_K,
    build_context,
    search_knowledge_base_context,
    search_knowledge_base_hits,
)

__all__ = [
    "DEFAULT_TOP_K",
    "ChunkHit",
    "DocumentHit",
    "SearchScope",
    "build_context",
    "search_knowledge_base_context",
    "search_knowledge_base_hits",
]
