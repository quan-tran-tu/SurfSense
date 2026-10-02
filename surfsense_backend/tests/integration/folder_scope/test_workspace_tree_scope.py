"""The workspace listing follows the question's folder scope.

"How many files are in this folder?" with one folder picked must be counted in
that folder's subtree, not across the whole workspace.
"""

import pytest

from app.agents.chat.shared.workspace_tree import build_workspace_tree
from app.db import Document, DocumentType, Folder


async def _folder(db_session, space_id, name, parent=None):
    f = Folder(
        name=name,
        position="a0",
        search_space_id=space_id,
        parent_id=parent.id if parent else None,
    )
    db_session.add(f)
    await db_session.flush()
    return f


async def _doc(db_session, space_id, folder, title):
    db_session.add(
        Document(
            title=title,
            document_type=DocumentType.FILE,
            content=title,
            content_hash=f"h-{folder.id}-{title}",
            unique_identifier_hash=f"u-{folder.id}-{title}",
            search_space_id=space_id,
            folder_id=folder.id,
        )
    )
    await db_session.flush()


@pytest.mark.asyncio
async def test_listing_is_cut_to_the_picked_folder(db_session, db_search_space):
    space_id = db_search_space.id
    picked = await _folder(db_session, space_id, "Picked")
    sub = await _folder(db_session, space_id, "Sub", parent=picked)
    other = await _folder(db_session, space_id, "Other")
    await _doc(db_session, space_id, picked, "a")
    await _doc(db_session, space_id, sub, "b")
    for t in ("c", "d", "e"):
        await _doc(db_session, space_id, other, t)

    whole = await build_workspace_tree(db_session, search_space_id=space_id, thread_id=None)
    assert whole.document_count == 5
    assert whole.scope_paths == ()

    scoped = await build_workspace_tree(
        db_session, search_space_id=space_id, thread_id=None, folder_ids=[picked.id]
    )
    assert scoped.document_count == 2
    assert scoped.folder_count == 2  # Picked + Sub
    assert scoped.scope_paths == ("/documents/Picked",)
    assert "Other" not in scoped.text
