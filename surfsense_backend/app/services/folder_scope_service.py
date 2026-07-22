"""Session-scoped folders: the promote-to-space-wide operation.

A folder with ``owner_thread_id`` set is visible only inside that chat session.
Promotion clears the stamp on the whole subtree — nothing moves, nothing is
re-embedded — but the identity hashes of the documents inside must be
recomputed: session-scoped documents mix the owning thread into
``unique_identifier_hash`` (see :func:`local_file_unique_id` and
:func:`note_path_identifier`), and after promotion they must carry the
space-wide form or a later re-upload / agent write at the same path would
create duplicates instead of updating them.

Promotion can collide: a space-wide document with the same identity may already
exist (the user uploaded the same folder space-wide earlier, or another session
promoted first). That is surfaced as a 409 listing the conflicts — silently
overwriting either side would destroy data.
"""

import logging

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.runtime.path_resolver import note_path_identifier
from app.db import Document, DocumentType, Folder
from app.indexing_pipeline.document_hashing import (
    compute_identifier_hash,
    local_file_unique_id,
)
from app.services.folder_service import get_folder_subtree_ids

logger = logging.getLogger(__name__)


def _space_wide_hash(document: Document, search_space_id: int) -> str | None:
    """The identity hash this document must carry once space-wide.

    Returns ``None`` when the document's identity never mixed in the session
    (connector documents, or rows missing the metadata to rebuild it) — those
    keep their hash untouched.
    """
    metadata = document.document_metadata or {}
    if document.document_type == DocumentType.LOCAL_FOLDER_FILE:
        folder_name = metadata.get("folder_name")
        file_path = metadata.get("file_path")
        if not folder_name or not file_path:
            return None
        return compute_identifier_hash(
            DocumentType.LOCAL_FOLDER_FILE.value,
            local_file_unique_id(str(folder_name), str(file_path), None),
            search_space_id,
        )
    if document.document_type == DocumentType.NOTE:
        virtual_path = metadata.get("virtual_path")
        if not virtual_path:
            return None
        from app.utils.document_converters import generate_unique_identifier_hash

        return generate_unique_identifier_hash(
            DocumentType.NOTE,
            note_path_identifier(str(virtual_path), None),
            search_space_id,
        )
    return None


async def promote_folder_to_space(
    session: AsyncSession, folder: Folder
) -> dict[str, int]:
    """Clear the session stamp on ``folder``'s subtree and rehash its documents.

    Raises 400 for a folder that is not the top of a session subtree, 409 when
    a space-wide folder or document with the same identity already exists.
    Returns ``{"folders_promoted": n, "documents_rehashed": m}``.
    """
    if folder.owner_thread_id is None:
        raise HTTPException(
            status_code=400, detail="Folder is already space-wide"
        )

    if folder.parent_id is not None:
        parent = await session.get(Folder, folder.parent_id)
        if parent is not None and parent.owner_thread_id is not None:
            raise HTTPException(
                status_code=400,
                detail="Promote the top-level session folder, not a subfolder",
            )

    # The unique index (space, parent, name, owner) would reject the promoted
    # row on commit; pre-check so the caller gets a legible 409 instead.
    sibling_conflict = await session.execute(
        select(Folder.id).where(
            Folder.search_space_id == folder.search_space_id,
            Folder.name == folder.name,
            Folder.parent_id.is_(None)
            if folder.parent_id is None
            else Folder.parent_id == folder.parent_id,
            Folder.owner_thread_id.is_(None),
            Folder.id != folder.id,
        )
    )
    if sibling_conflict.first() is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A space-wide folder named '{folder.name}' already exists at "
                "this location. Rename one of them first."
            ),
        )

    subtree_ids = await get_folder_subtree_ids(session, folder.id)

    documents = (
        (
            await session.execute(
                select(Document).where(Document.folder_id.in_(subtree_ids))
            )
        )
        .scalars()
        .all()
    )

    rehash: dict[int, str] = {}
    for document in documents:
        new_hash = _space_wide_hash(document, folder.search_space_id)
        if new_hash is not None and new_hash != document.unique_identifier_hash:
            rehash[document.id] = new_hash

    if rehash:
        conflict_rows = (
            await session.execute(
                select(Document.id, Document.title).where(
                    Document.unique_identifier_hash.in_(list(rehash.values())),
                    Document.id.notin_(list(rehash.keys())),
                )
            )
        ).all()
        if conflict_rows:
            titles = ", ".join(str(row.title) for row in conflict_rows[:5])
            raise HTTPException(
                status_code=409,
                detail=(
                    "Promotion would collide with existing space-wide "
                    f"documents ({len(conflict_rows)}): {titles}. Remove or "
                    "rename them first."
                ),
            )

    promoted = (
        await session.execute(
            update(Folder)
            .where(
                Folder.id.in_(subtree_ids),
                Folder.owner_thread_id.is_not(None),
            )
            .values(owner_thread_id=None)
        )
    ).rowcount

    for document in documents:
        new_hash = rehash.get(document.id)
        if new_hash is not None:
            document.unique_identifier_hash = new_hash

    await session.commit()
    logger.info(
        "folder_scope: promoted folder #%s (space=%s): folders=%s rehashed=%s",
        folder.id,
        folder.search_space_id,
        promoted,
        len(rehash),
    )
    return {"folders_promoted": int(promoted or 0), "documents_rehashed": len(rehash)}
