"""Folder service: depth validation, circular reference checks, and position generation."""

from collections.abc import Sequence

from fastapi import HTTPException
from fractional_indexing import generate_key_between
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db import Document, Folder

MAX_FOLDER_DEPTH = 8


async def get_folder_depth(session: AsyncSession, folder_id: int) -> int:
    """Return the depth of a folder (root-level = 1) using a recursive CTE."""
    result = await session.execute(
        text("""
            WITH RECURSIVE ancestors AS (
                SELECT id, parent_id, 1 AS depth
                FROM folders
                WHERE id = :folder_id
                UNION ALL
                SELECT f.id, f.parent_id, a.depth + 1
                FROM folders f
                JOIN ancestors a ON f.id = a.parent_id
            )
            SELECT MAX(depth) FROM ancestors;
        """),
        {"folder_id": folder_id},
    )
    return result.scalar() or 0


async def get_subtree_max_depth(session: AsyncSession, folder_id: int) -> int:
    """Return the maximum depth of any descendant below folder_id (0 if leaf)."""
    result = await session.execute(
        text("""
            WITH RECURSIVE descendants AS (
                SELECT id, 0 AS depth
                FROM folders
                WHERE parent_id = :folder_id
                UNION ALL
                SELECT f.id, d.depth + 1
                FROM folders f
                JOIN descendants d ON f.parent_id = d.id
            )
            SELECT COALESCE(MAX(depth), -1) FROM descendants;
        """),
        {"folder_id": folder_id},
    )
    val = result.scalar()
    return (val + 1) if val is not None and val >= 0 else 0


async def validate_folder_depth(
    session: AsyncSession,
    parent_id: int | None,
    subtree_depth: int = 0,
) -> None:
    """Raise 400 if placing a folder (with subtree) under parent_id would exceed MAX_FOLDER_DEPTH."""
    if parent_id is None:
        parent_depth = 0
    else:
        parent_depth = await get_folder_depth(session, parent_id)

    total = parent_depth + 1 + subtree_depth
    if total > MAX_FOLDER_DEPTH:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum folder nesting depth is {MAX_FOLDER_DEPTH}. "
            f"This operation would result in depth {total}.",
        )


async def check_no_circular_reference(
    session: AsyncSession,
    folder_id: int,
    new_parent_id: int | None,
) -> None:
    """Raise 400 if new_parent_id is folder_id itself or a descendant of folder_id."""
    if new_parent_id is None:
        return

    if new_parent_id == folder_id:
        raise HTTPException(
            status_code=400,
            detail="A folder cannot be moved into itself.",
        )

    result = await session.execute(
        text("""
            WITH RECURSIVE ancestors AS (
                SELECT id, parent_id
                FROM folders
                WHERE id = :new_parent_id
                UNION ALL
                SELECT f.id, f.parent_id
                FROM folders f
                JOIN ancestors a ON f.id = a.parent_id
            )
            SELECT 1 FROM ancestors WHERE id = :folder_id LIMIT 1;
        """),
        {"new_parent_id": new_parent_id, "folder_id": folder_id},
    )
    if result.scalar() is not None:
        raise HTTPException(
            status_code=400,
            detail="Cannot move a folder into one of its own descendants.",
        )


async def generate_folder_position(
    session: AsyncSession,
    search_space_id: int,
    parent_id: int | None,
    before_position: str | None = None,
    after_position: str | None = None,
) -> str:
    """Generate a fractional index key for ordering a folder among its siblings.

    - Default (no before/after): append after last sibling
    - Prepend: before_position=None, after_position=first sibling position
    - Insert between: both positions provided
    """
    if before_position is not None or after_position is not None:
        return generate_key_between(before_position, after_position)

    # Append after last sibling
    query = (
        select(Folder.position)
        .where(
            Folder.search_space_id == search_space_id,
            Folder.parent_id == parent_id
            if parent_id is not None
            else Folder.parent_id.is_(None),
        )
        .order_by(Folder.position.desc())
        .limit(1)
    )
    result = await session.execute(query)
    last_position = result.scalar()
    return generate_key_between(last_position, None)


async def ensure_folder_hierarchy_with_depth_validation(
    session: AsyncSession,
    search_space_id: int,
    path_segments: list[dict],
) -> Folder:
    """Create or return a nested folder chain, validating depth at each step.

    Each item in ``path_segments`` is a dict with:
      - ``name``  (str): folder display name
      - ``metadata`` (dict | None): optional ``folder_metadata`` JSONB payload

    Returns the deepest (leaf) Folder in the chain.
    """
    parent_id: int | None = None
    current_folder: Folder | None = None

    for segment in path_segments:
        name = segment["name"]
        metadata = segment.get("metadata")

        stmt = select(Folder).where(
            Folder.search_space_id == search_space_id,
            Folder.name == name,
            Folder.parent_id == parent_id
            if parent_id is not None
            else Folder.parent_id.is_(None),
        )
        result = await session.execute(stmt)
        folder = result.scalar_one_or_none()

        if folder is None:
            await validate_folder_depth(session, parent_id, subtree_depth=0)
            position = await generate_folder_position(
                session, search_space_id, parent_id
            )
            folder = Folder(
                name=name,
                search_space_id=search_space_id,
                parent_id=parent_id,
                position=position,
                folder_metadata=metadata,
            )
            session.add(folder)
            await session.flush()

        current_folder = folder
        parent_id = folder.id

    assert current_folder is not None, "path_segments must not be empty"
    return current_folder


async def resolve_folder_path(
    session: AsyncSession,
    search_space_id: int,
    path: str,
) -> Folder:
    """Resolve a slash-separated folder path (``"Research/AI"``) to its Folder.

    Read-only counterpart to :func:`ensure_folder_hierarchy_with_depth_validation`,
    which walks the same name segments but *creates* missing ones — unusable when
    the caller is naming an existing folder rather than declaring one.

    Raises HTTPException(404) if any segment is missing.
    """
    segments = [seg for seg in path.strip("/").split("/") if seg]
    if not segments:
        raise HTTPException(status_code=400, detail="Folder path must not be empty")

    parent_id: int | None = None
    folder: Folder | None = None

    for name in segments:
        stmt = select(Folder).where(
            Folder.search_space_id == search_space_id,
            Folder.name == name,
            Folder.parent_id == parent_id
            if parent_id is not None
            else Folder.parent_id.is_(None),
        )
        result = await session.execute(stmt)
        folder = result.scalar_one_or_none()

        if folder is None:
            raise HTTPException(
                status_code=404, detail=f"Folder not found at path: {path}"
            )
        parent_id = folder.id

    assert folder is not None
    return folder


async def get_folder_subtree_ids(session: AsyncSession, folder_id: int) -> list[int]:
    """Return all folder IDs in the subtree rooted at folder_id (inclusive)."""
    result = await session.execute(
        text("""
            WITH RECURSIVE subtree AS (
                SELECT id FROM folders WHERE id = :folder_id
                UNION ALL
                SELECT f.id FROM folders f JOIN subtree s ON f.parent_id = s.id
            )
            SELECT id FROM subtree;
        """),
        {"folder_id": folder_id},
    )
    return list(result.scalars().all())


def folder_subtree_ids_subquery(folder_ids: Sequence[int]):
    """Selectable yielding every folder id inside the subtrees rooted at ``folder_ids``.

    The embeddable counterpart of :func:`get_folder_subtree_ids`: several roots
    instead of one, and a subquery instead of a round trip, so a search can say
    "documents inside these folders" in a single statement.

    Recursion is what makes this usable as a scope. A folder upload mirrors the
    picked directory tree — ``_resolve_folder_for_file`` creates a ``Folder`` row
    per subdirectory — so an exact ``folder_id IN (roots)`` predicate matches only
    the files sitting loose at each root, which for a real document set is close
    to nothing and reads as "not found" rather than as a bug.

    Deliberately not filtered by search space: a folder reachable by link lives in
    the sharer's space, and callers already AND in their own readability
    condition, so restricting here would only break the linked-folder case.
    """
    roots = (
        select(Folder.id.label("id"))
        .where(Folder.id.in_(list(folder_ids)))
        .cte("scoped_folders", recursive=True)
    )
    descendants = select(Folder.id).join(roots, Folder.parent_id == roots.c.id)
    return select(roots.union_all(descendants).c.id)


async def dispatch_folder_deletion(session: AsyncSession, folder_id: int) -> int:
    """Mark a folder subtree's documents ``deleting`` and queue their removal.

    The Celery job deletes the documents first, then the (now empty) folders — a
    plain ``DELETE`` on the folder row would only SET NULL each ``Document.folder_id``,
    orphaning the documents instead of removing them. Returns the number of
    documents queued.

    Authorization is the caller's responsibility: the user route gates on
    ``DOCUMENTS_DELETE`` in the folder's space; the admin route gates on
    ``is_superuser`` and skips the membership check entirely.
    """
    subtree_ids = await get_folder_subtree_ids(session, folder_id)

    doc_result = await session.execute(
        select(Document.id).where(
            Document.folder_id.in_(subtree_ids),
            Document.status["state"].as_string() != "deleting",
        )
    )
    document_ids = list(doc_result.scalars().all())

    if document_ids:
        await session.execute(
            Document.__table__.update()
            .where(Document.id.in_(document_ids))
            .values(status={"state": "deleting"})
        )
        await session.commit()

    try:
        from app.tasks.celery_tasks.document_tasks import (
            delete_folder_documents_task,
        )

        delete_folder_documents_task.delay(
            document_ids, folder_subtree_ids=list(subtree_ids)
        )
    except Exception as err:
        if document_ids:
            await session.execute(
                Document.__table__.update()
                .where(Document.id.in_(document_ids))
                .values(status={"state": "ready"})
            )
            await session.commit()
        raise HTTPException(
            status_code=503,
            detail="Could not queue folder deletion. Documents have been restored.",
        ) from err

    return len(document_ids)
