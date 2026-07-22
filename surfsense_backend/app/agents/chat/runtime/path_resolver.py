"""Canonical virtual-path resolver for SurfSense knowledge-base documents.

This module is the single source of truth for mapping ``Document`` rows to
virtual paths under ``/documents/`` and back. It is used by:

* :class:`KnowledgeTreeMiddleware` (rendering the workspace tree)
* :class:`KBPostgresBackend` (``als_info`` / ``aread`` / move operations)
* :class:`KnowledgeBasePersistenceMiddleware` (resolving moves and creates)

Centralising the logic ensures that title-collision suffixes, folder paths,
and ``unique_identifier_hash`` lookups never drift between renders and
commits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Document, DocumentType, Folder
from app.services.folder_sharing_service import (
    SHARED_PREFIX,
    get_linked_folder_roots,
)
from app.utils.document_converters import generate_unique_identifier_hash

DOCUMENTS_ROOT = "/documents"
"""Root virtual folder for all KB documents."""


def current_thread_id() -> int | None:
    """The active chat thread id, from the live LangGraph config; ``None`` outside a run.

    Compiled graphs are cached per search space and serve many threads, so the
    thread must be resolved at call time from ``configurable.thread_id`` — never
    captured at construction (same pattern as
    ``KnowledgeBasePersistenceMiddleware._resolve_thread_id``). ``None`` means
    "no session context": callers treat that as unscoped, so non-chat surfaces
    (REST, exports, admin) keep seeing everything.

    Subagent runs carry an *extended* id (``subagent_invoke_config`` namespaces
    the checkpoint slot as ``"{parent_thread}::task:{tool_call_id}"``), and the
    knowledge_base subagent is where retrieval actually happens — so the parent
    id is parsed off the front rather than treating the string as garbage,
    which would silently widen every search back to the whole space.
    """
    try:
        from langgraph.config import get_config

        config = get_config()
    except Exception:
        return None
    if not isinstance(config, dict):
        return None
    value = (config.get("configurable") or {}).get("thread_id")
    if value is None:
        return None
    base = str(value).split("::", 1)[0]
    try:
        return int(base)
    except (TypeError, ValueError):
        return None


def note_path_identifier(virtual_path: str, owner_thread_id: int | None) -> str:
    """Identity string hashed into a NOTE's ``unique_identifier_hash``.

    Session-scoped folders make the same virtual path reachable from several
    threads (two chats can each upload a root named ``Research``), and the hash
    is globally unique — so the owning thread must be part of the identity or
    the second session's write collides with the first's document.
    """
    if owner_thread_id is None:
        return virtual_path
    return f"thread:{owner_thread_id}:{virtual_path}"

_INVALID_FILENAME_CHARS = re.compile(r"[\\/:*?\"<>|]+")
_WHITESPACE_RUN = re.compile(r"\s+")


def safe_filename(value: str, *, fallback: str = "untitled.xml") -> str:
    """Convert arbitrary text into a filesystem-safe ``.xml`` filename."""
    name = _INVALID_FILENAME_CHARS.sub("_", value).strip()
    name = _WHITESPACE_RUN.sub(" ", name)
    if not name:
        name = fallback
    if len(name) > 180:
        name = name[:180].rstrip()
    if not name.lower().endswith(".xml"):
        name = f"{name}.xml"
    return name


def safe_folder_segment(value: str, *, fallback: str = "folder") -> str:
    """Sanitize a single folder name into a path-safe segment."""
    name = _INVALID_FILENAME_CHARS.sub("_", value).strip()
    name = _WHITESPACE_RUN.sub(" ", name)
    if not name:
        return fallback
    if len(name) > 180:
        name = name[:180].rstrip()
    return name


def _suffix_with_doc_id(filename: str, doc_id: int | None) -> str:
    if doc_id is None:
        return filename
    if not filename.lower().endswith(".xml"):
        return f"{filename} ({doc_id}).xml"
    stem = filename[:-4]
    return f"{stem} ({doc_id}).xml"


_SUFFIX_PATTERN = re.compile(r"\s\((\d+)\)\.xml$", re.IGNORECASE)


def parse_doc_id_suffix(filename: str) -> tuple[str, int | None]:
    """Strip a trailing ``" (<doc_id>).xml"`` suffix; return ``(stem, doc_id)``.

    If no suffix is present, returns ``(stem_without_xml_extension, None)``.
    """
    match = _SUFFIX_PATTERN.search(filename)
    if match:
        doc_id = int(match.group(1))
        stem = filename[: match.start()]
        return stem, doc_id
    if filename.lower().endswith(".xml"):
        return filename[:-4], None
    return filename, None


@dataclass
class PathIndex:
    """In-memory occupancy snapshot used by :func:`doc_to_virtual_path`.

    Built once per call site so collision handling is deterministic and so
    we don't perform N folder lookups per render.
    """

    folder_paths: dict[int, str] = field(default_factory=dict)
    """``Folder.id`` -> absolute virtual folder path under ``/documents``."""

    occupants: dict[str, int] = field(default_factory=dict)
    """virtual path -> ``Document.id`` already occupying that path (this render)."""

    linked_folder_ids: set[int] = field(default_factory=set)
    """Folders readable only via a share link. **Read-only** — see WS3 write guard.

    These live under ``/documents/_shared/`` and belong to *another* search space;
    their rows must never be mutated through this space. Carried on the index so
    callers that already built one can widen their document filters, and so the
    write guard can test membership, without re-querying.
    """

    hidden_folder_ids: set[int] = field(default_factory=set)
    """Folders owned by a *different* chat session than the one this index serves.

    They are excluded from ``folder_paths`` entirely — invisible to the tree,
    ``ls``, glob, grep, and ``@``-mentions — and their documents are excluded by
    :func:`readable_documents_filter`. Empty when the index was built without a
    thread (non-chat surfaces stay unscoped)."""


SHARED_ROOT = f"{DOCUMENTS_ROOT}/{SHARED_PREFIX}"
"""Virtual mount for folders reachable through a share link. Read-only."""


def is_shared_path(path: str) -> bool:
    """True for any path inside the read-only linked-folder mount.

    Linked folders are reachable *only* under this prefix (see
    :func:`_add_linked_folder_paths`), so a prefix test is a complete cheap
    filter for "would this write touch another space's rows?". It is not the
    whole guard — the commit path also re-checks the resolved row's owning
    search space, because ownership, not the name used to reach a row, is what
    actually decides.
    """
    return path == SHARED_ROOT or path.startswith(SHARED_ROOT + "/")


def readable_documents_filter(index: PathIndex, search_space_id: int):
    """SQL predicate for "documents this context may read".

    Owned documents, plus those sitting in a folder reachable through a live
    share link, minus those in a folder owned by a different chat session.
    Every read surface that renders paths from a :class:`PathIndex` must use
    this instead of a bare ``search_space_id ==`` — otherwise linked folders
    appear in the tree but their documents do not (or another session's
    documents surface with no folder to hold them).
    """
    if not index.linked_folder_ids:
        readable = Document.search_space_id == search_space_id
    else:
        readable = or_(
            Document.search_space_id == search_space_id,
            Document.folder_id.in_(index.linked_folder_ids),
        )
    if not index.hidden_folder_ids:
        return readable
    # NOT IN is NULL-hostile: a folderless document must stay readable.
    return and_(
        readable,
        or_(
            Document.folder_id.is_(None),
            Document.folder_id.notin_(index.hidden_folder_ids),
        ),
    )


async def _build_folder_paths(
    session: AsyncSession,
    search_space_id: int,
    thread_id: int | None = None,
) -> tuple[dict[int, str], set[int], set[int]]:
    """Compute ``Folder.id`` -> absolute virtual path under ``/documents``.

    Returns ``(path map, linked folder ids, hidden folder ids)``. Owned folders
    are rooted at ``/documents``; folders reachable through a live share link
    are rooted at ``/documents/_shared/<root name>`` so they read as visibly
    foreign and so the write guard has a cheap path-prefix test.

    When ``thread_id`` is given, folders owned by a *different* chat session —
    or sitting anywhere under one, so a broken stamping invariant still fails
    closed — go into the hidden set and get no path at all.
    """
    result = await session.execute(
        select(Folder.id, Folder.name, Folder.parent_id, Folder.owner_thread_id).where(
            Folder.search_space_id == search_space_id
        )
    )
    rows = result.all()
    by_id = {
        row.id: {
            "name": row.name,
            "parent_id": row.parent_id,
            "owner_thread_id": row.owner_thread_id,
        }
        for row in rows
    }

    def _foreign(owner: int | None) -> bool:
        return thread_id is not None and owner is not None and owner != thread_id

    hidden: set[int] = set()
    for folder_id in by_id:
        cursor: int | None = folder_id
        visited: set[int] = set()
        while cursor is not None and cursor in by_id and cursor not in visited:
            visited.add(cursor)
            if _foreign(by_id[cursor]["owner_thread_id"]):
                hidden.add(folder_id)
                break
            cursor = by_id[cursor]["parent_id"]

    cache: dict[int, str] = {}

    def resolve(folder_id: int) -> str:
        if folder_id in cache:
            return cache[folder_id]
        parts: list[str] = []
        cursor: int | None = folder_id
        visited: set[int] = set()
        while cursor is not None and cursor in by_id and cursor not in visited:
            visited.add(cursor)
            entry = by_id[cursor]
            parts.append(safe_folder_segment(str(entry["name"])))
            cursor = entry["parent_id"]
        parts.reverse()
        path = f"{DOCUMENTS_ROOT}/" + "/".join(parts) if parts else DOCUMENTS_ROOT
        cache[folder_id] = path
        return path

    for folder_id in by_id:
        if folder_id not in hidden:
            resolve(folder_id)

    linked_ids = await _add_linked_folder_paths(session, search_space_id, cache)
    return cache, linked_ids, hidden


async def _add_linked_folder_paths(
    session: AsyncSession,
    search_space_id: int,
    cache: dict[int, str],
) -> set[int]:
    """Mount each live-linked folder subtree under ``/documents/_shared/``.

    Mutates ``cache`` in place; returns the ids mounted. A linked root's own
    children are walked from the sharer's tree, so ``Research/AI`` under a share
    of ``Research`` lands at ``/documents/_shared/Research/AI``.
    """
    roots = await get_linked_folder_roots(session, search_space_id)
    if not roots:
        return set()

    shared_root = f"{DOCUMENTS_ROOT}/{SHARED_PREFIX}"
    linked_ids: set[int] = set()

    # Two links can name folders with the same display name from different spaces;
    # disambiguate the second by id rather than letting one shadow the other.
    used_names: set[str] = set()
    frontier: list[tuple[int, str]] = []
    for root in roots:
        segment = safe_folder_segment(str(root.name))
        if segment in used_names:
            segment = f"{segment} ({root.id})"
        used_names.add(segment)
        path = f"{shared_root}/{segment}"
        cache[root.id] = path
        linked_ids.add(root.id)
        frontier.append((root.id, path))

    # Breadth-first over the sharer's descendants.
    while frontier:
        parent_ids = [fid for fid, _ in frontier]
        parent_paths = dict(frontier)
        child_rows = await session.execute(
            select(Folder.id, Folder.name, Folder.parent_id).where(
                Folder.parent_id.in_(parent_ids)
            )
        )
        frontier = []
        for row in child_rows.all():
            if row.id in linked_ids:  # cycle guard; folders are a tree, but cheap
                continue
            path = f"{parent_paths[row.parent_id]}/{safe_folder_segment(str(row.name))}"
            cache[row.id] = path
            linked_ids.add(row.id)
            frontier.append((row.id, path))

    return linked_ids


async def build_path_index(
    session: AsyncSession,
    search_space_id: int,
    *,
    populate_occupants: bool = True,
    thread_id: int | None = None,
) -> PathIndex:
    """Build a :class:`PathIndex` for a search space.

    ``populate_occupants`` controls whether the occupancy map is pre-seeded
    from existing ``Document`` rows. Most callers want this so that
    :func:`doc_to_virtual_path` can detect collisions across the whole space;
    the persistence middleware sets this to ``False`` when it is iterating to
    decide where to place fresh documents.

    The index spans owned documents *and* those reachable through a live share
    link; the latter are read-only and flagged by ``linked_folder_ids``.
    ``thread_id`` scopes the index to a chat session: folders owned by other
    sessions are dropped from the map and their documents from the occupancy
    seed. ``None`` builds an unscoped index (non-chat surfaces).
    """
    folder_paths, linked_folder_ids, hidden_folder_ids = await _build_folder_paths(
        session, search_space_id, thread_id
    )
    occupants: dict[str, int] = {}
    if populate_occupants:
        query = select(Document.id, Document.title, Document.folder_id).where(
            or_(
                Document.search_space_id == search_space_id,
                Document.folder_id.in_(linked_folder_ids)
                if linked_folder_ids
                else false(),
            )
        )
        if hidden_folder_ids:
            # Without this, another session's documents would seed the map with
            # their folder missing and appear as loose files at /documents.
            query = query.where(
                or_(
                    Document.folder_id.is_(None),
                    Document.folder_id.notin_(hidden_folder_ids),
                )
            )
        rows = await session.execute(query)
        for row in rows.all():
            base = folder_paths.get(row.folder_id, DOCUMENTS_ROOT)
            filename = safe_filename(str(row.title or "untitled"))
            path = f"{base}/{filename}"
            if path in occupants and occupants[path] != row.id:
                path = f"{base}/{_suffix_with_doc_id(filename, row.id)}"
            occupants[path] = row.id
    return PathIndex(
        folder_paths=folder_paths,
        occupants=occupants,
        linked_folder_ids=linked_folder_ids,
        hidden_folder_ids=hidden_folder_ids,
    )


def doc_to_virtual_path(
    *,
    doc_id: int | None,
    title: str,
    folder_id: int | None,
    index: PathIndex,
) -> str:
    """Return the canonical virtual path for a document.

    Mutates ``index.occupants`` so subsequent calls see this assignment and
    deterministically pick a different suffix for the next colliding doc.
    """
    base = index.folder_paths.get(folder_id, DOCUMENTS_ROOT)
    filename = safe_filename(str(title or "untitled"))
    path = f"{base}/{filename}"
    occupant = index.occupants.get(path)
    if occupant is not None and occupant != doc_id:
        path = f"{base}/{_suffix_with_doc_id(filename, doc_id)}"
    if doc_id is not None:
        index.occupants[path] = doc_id
    return path


async def folder_owner_thread_id(
    session: AsyncSession, folder_id: int | None
) -> int | None:
    """The session that owns ``folder_id`` (via its own stamp), or ``None``.

    Relies on the stamping invariant (children carry their subtree root's
    owner), which every folder-creation site maintains.
    """
    if folder_id is None:
        return None
    result = await session.execute(
        select(Folder.owner_thread_id).where(Folder.id == folder_id)
    )
    row = result.first()
    return row[0] if row is not None else None


async def _folder_visible_to_thread(
    session: AsyncSession, folder_id: int | None, thread_id: int | None
) -> bool:
    """False iff the folder belongs to a chat session other than ``thread_id``."""
    if folder_id is None or thread_id is None:
        return True
    owner = await folder_owner_thread_id(session, folder_id)
    return owner is None or owner == thread_id


async def virtual_path_to_doc(
    session: AsyncSession,
    *,
    search_space_id: int,
    virtual_path: str,
    thread_id: int | None = None,
) -> Document | None:
    """Resolve a virtual path back to a ``Document`` row.

    Resolution order:
    1. ``Document.unique_identifier_hash`` lookup (fast path for paths created
       by SurfSense itself — every NOTE write goes through this hash). With a
       ``thread_id``, the session-scoped hash is tried first: the same path can
       exist in several sessions, and the caller's own copy must win.
    2. If the basename carries a ``" (<doc_id>).xml"`` disambiguation suffix,
       try a direct id lookup constrained to the search space.
    3. Title-from-basename + folder-resolution lookup as a last resort.

    ``thread_id`` also constrains folder resolution, so another session's
    folders — and therefore their documents — cannot be reached at all.
    """
    if not virtual_path or not virtual_path.startswith(DOCUMENTS_ROOT):
        return None

    # Linked documents belong to the sharer's space, so every lookup below —
    # the unique_identifier_hash (which mixes in search_space_id), the id lookup,
    # the folder walk — is constrained to the wrong space and would miss them.
    # Resolve them through the index that minted the path in the first place.
    if virtual_path.startswith(f"{DOCUMENTS_ROOT}/{SHARED_PREFIX}/"):
        index = await build_path_index(session, search_space_id, thread_id=thread_id)
        doc_id = index.occupants.get(virtual_path)
        if doc_id is None:
            return None
        result = await session.execute(select(Document).where(Document.id == doc_id))
        document = result.scalar_one_or_none()
        # An id lookup unconstrained by search space is exactly the sort of thing
        # that must fail closed: re-verify the row really does sit in a folder
        # this space holds a live link to.
        if document is None or document.folder_id not in index.linked_folder_ids:
            return None
        return document

    hash_identifiers = [virtual_path]
    if thread_id is not None:
        hash_identifiers.insert(0, note_path_identifier(virtual_path, thread_id))
    for identifier in hash_identifiers:
        unique_hash = generate_unique_identifier_hash(
            DocumentType.NOTE,
            identifier,
            search_space_id,
        )
        result = await session.execute(
            select(Document).where(
                Document.search_space_id == search_space_id,
                Document.unique_identifier_hash == unique_hash,
            )
        )
        document = result.scalar_one_or_none()
        if document is not None:
            return document

    rel = virtual_path[len(DOCUMENTS_ROOT) :].lstrip("/")
    if not rel:
        return None
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return None
    basename = parts[-1]
    folder_parts = parts[:-1]

    stem, suffix_doc_id = parse_doc_id_suffix(basename)
    if suffix_doc_id is not None:
        result = await session.execute(
            select(Document).where(
                Document.search_space_id == search_space_id,
                Document.id == suffix_doc_id,
            )
        )
        document = result.scalar_one_or_none()
        if document is not None and await _folder_visible_to_thread(
            session, document.folder_id, thread_id
        ):
            return document

    folder_id = await _resolve_folder_id(
        session,
        search_space_id=search_space_id,
        folder_parts=folder_parts,
        thread_id=thread_id,
    )
    title_candidates: list[str] = []
    raw_title = stem
    title_candidates.append(raw_title)
    if raw_title.endswith(".xml"):
        title_candidates.append(raw_title[:-4])

    for candidate in dict.fromkeys(title_candidates):
        if not candidate:
            continue
        query = select(Document).where(
            Document.search_space_id == search_space_id,
            Document.title == candidate,
        )
        if folder_id is None:
            query = query.where(Document.folder_id.is_(None))
        else:
            query = query.where(Document.folder_id == folder_id)
        result = await session.execute(query)
        document = result.scalars().first()
        if document is not None:
            return document

    # Fallback: title-as-string lookup misses when the real DB title contains
    # characters that ``safe_filename`` lossily replaces (``:``, ``/``, ``*``,
    # etc.) — common for connector-imported docs (Google Calendar/Drive etc.).
    # The workspace tree shows the lossy filename, so the agent passes that
    # filename back here. Scan all documents in the resolved folder and match
    # by ``safe_filename(title)`` to recover the original document.
    folder_scan = select(Document).where(
        Document.search_space_id == search_space_id,
    )
    if folder_id is None:
        folder_scan = folder_scan.where(Document.folder_id.is_(None))
    else:
        folder_scan = folder_scan.where(Document.folder_id == folder_id)
    result = await session.execute(folder_scan)
    for candidate_doc in result.scalars().all():
        encoded = safe_filename(str(candidate_doc.title or "untitled"))
        if encoded == basename:
            return candidate_doc
    return None


async def _resolve_folder_id(
    session: AsyncSession,
    *,
    search_space_id: int,
    folder_parts: list[str],
    thread_id: int | None = None,
) -> int | None:
    """Look up the leaf folder id for a chain of folder names; return ``None`` if missing.

    With a ``thread_id``, other sessions' folders don't resolve, and when both a
    session-owned and a space-wide folder carry the same name at the same level,
    the session's own wins — the more specific scope shadows the general one.
    """
    if not folder_parts:
        return None
    parent_id: int | None = None
    for raw in folder_parts:
        name = safe_folder_segment(raw)
        query = select(Folder.id).where(
            Folder.search_space_id == search_space_id,
            Folder.name == name,
        )
        if thread_id is not None:
            query = query.where(
                or_(
                    Folder.owner_thread_id.is_(None),
                    Folder.owner_thread_id == thread_id,
                )
            ).order_by((Folder.owner_thread_id == thread_id).desc())
        if parent_id is None:
            query = query.where(Folder.parent_id.is_(None))
        else:
            query = query.where(Folder.parent_id == parent_id)
        result = await session.execute(query)
        row = result.first()
        if row is None:
            return None
        parent_id = row[0]
    return parent_id


def parse_documents_path(virtual_path: str) -> tuple[list[str], str]:
    """Parse a ``/documents/...`` path into ``(folder_parts, document_title)``.

    The title has any ``.xml`` extension and trailing ``" (<doc_id>)"``
    disambiguation suffix stripped.
    """
    if not virtual_path or not virtual_path.startswith(DOCUMENTS_ROOT):
        return [], ""
    rel = virtual_path[len(DOCUMENTS_ROOT) :].strip("/")
    if not rel:
        return [], ""
    parts = [p for p in rel.split("/") if p]
    if not parts:
        return [], ""
    folder_parts = parts[:-1]
    basename = parts[-1]
    stem, _ = parse_doc_id_suffix(basename)
    title = stem
    if title.endswith(".xml"):
        title = title[:-4]
    return folder_parts, title


__all__ = [
    "DOCUMENTS_ROOT",
    "PathIndex",
    "build_path_index",
    "current_thread_id",
    "doc_to_virtual_path",
    "folder_owner_thread_id",
    "note_path_identifier",
    "parse_doc_id_suffix",
    "SHARED_ROOT",
    "is_shared_path",
    "parse_documents_path",
    "readable_documents_filter",
    "safe_filename",
    "safe_folder_segment",
    "virtual_path_to_doc",
]
