"""Resolve ``@document`` references into pointer references for the model.

Turning mentions into a *retrieval scope* is a separate job, and no longer done
here: ``SearchScope`` carries the pinned document and folder ids straight into
the search predicate (see ``shared/retrieval/hybrid_search.py``), so folders are
matched by subtree instead of being pre-expanded into document ids.
"""

from __future__ import annotations

from .resolver import resolve_document_references

__all__ = ["resolve_document_references"]
