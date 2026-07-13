"""Linked folders are readable and *not* writable.

The load-bearing test is :func:`test_rmdir_on_linked_folder_leaves_sharer_intact`.
``Folder.parent_id`` is ON DELETE CASCADE and ``Document.folder_id`` is ON DELETE
SET NULL, so an rmdir that reached a linked folder would delete the sharer's
folder rows and silently orphan the sharer's documents from their own tree —
while the importer's own space looked completely unchanged. Nothing else in the
feature fails that quietly.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.multi_agent_chat.main_agent.middleware.kb_persistence.middleware import (
    commit_staged_filesystem_state,
)
from app.agents.chat.runtime.path_resolver import (
    build_path_index,
    virtual_path_to_doc,
)
from app.db import (
    Document,
    DocumentType,
    Folder,
    FolderLink,
    SearchSpace,
    SharedFolder,
    User,
)
from app.services.folder_sharing_service import (
    generate_share_token,
    get_linked_folder_ids,
    user_can_read_via_link,
)

SHARED_FOLDER_PATH = "/documents/_shared/Research"
SHARED_DOC_PATH = "/documents/_shared/Research/Secret Notes.xml"


@pytest_asyncio.fixture
async def sharer_space(db_session: AsyncSession, db_user: User) -> SearchSpace:
    space = SearchSpace(name="Sharer Space", user_id=db_user.id)
    db_session.add(space)
    await db_session.flush()
    return space


@pytest_asyncio.fixture
async def shared_setup(
    db_session: AsyncSession,
    db_user: User,
    db_search_space: SearchSpace,
    sharer_space: SearchSpace,
):
    """Sharer owns Research/ + one document; importer holds a live link to it.

    ``db_search_space`` plays the importer.
    """
    folder = Folder(
        name="Research",
        position="a0",
        search_space_id=sharer_space.id,
        created_by_id=db_user.id,
    )
    db_session.add(folder)
    await db_session.flush()

    doc = Document(
        title="Secret Notes",
        document_type=DocumentType.NOTE,
        content="sharer content",
        content_hash="hash-shared-doc",
        search_space_id=sharer_space.id,
        folder_id=folder.id,
        created_by_id=db_user.id,
    )
    db_session.add(doc)
    await db_session.flush()

    share = SharedFolder(
        token=generate_share_token(),
        source_folder_id=folder.id,
        source_search_space_id=sharer_space.id,
        created_by_id=db_user.id,
    )
    db_session.add(share)
    await db_session.flush()

    link = FolderLink(
        share_id=share.id,
        source_folder_id=folder.id,
        target_search_space_id=db_search_space.id,
        created_by_id=db_user.id,
    )
    db_session.add(link)
    await db_session.flush()

    return {
        "folder": folder,
        "doc": doc,
        "share": share,
        "link": link,
        "importer_id": db_search_space.id,
        "sharer_id": sharer_space.id,
    }


async def _commit(db_session: AsyncSession, importer_id: int, **staged):
    return await commit_staged_filesystem_state(
        {
            "files": {},
            "staged_dirs": [],
            "pending_moves": [],
            "pending_deletes": [],
            "pending_dir_deletes": [],
            "dirty_paths": [],
            "doc_id_by_path": {},
            **staged,
        },
        search_space_id=importer_id,
        created_by_id=None,
        thread_id=None,
        dispatch_events=False,
    )


@pytest.mark.asyncio
async def test_linked_folder_is_readable(db_session, shared_setup):
    """Baseline: the link works at all — without this the guard tests are vacuous."""
    importer_id = shared_setup["importer_id"]

    linked_ids = await get_linked_folder_ids(db_session, importer_id)
    assert shared_setup["folder"].id in linked_ids

    index = await build_path_index(db_session, importer_id)
    assert index.folder_paths[shared_setup["folder"].id] == SHARED_FOLDER_PATH
    assert index.occupants.get(SHARED_DOC_PATH) == shared_setup["doc"].id

    doc = await virtual_path_to_doc(
        db_session, search_space_id=importer_id, virtual_path=SHARED_DOC_PATH
    )
    assert doc is not None
    assert doc.id == shared_setup["doc"].id


@pytest.mark.asyncio
async def test_rmdir_on_linked_folder_leaves_sharer_intact(db_session, shared_setup):
    """The test the plan says must exist before this feature ships."""
    folder_id = shared_setup["folder"].id
    doc_id = shared_setup["doc"].id

    await _commit(
        db_session,
        shared_setup["importer_id"],
        pending_dir_deletes=[{"path": SHARED_FOLDER_PATH, "tool_call_id": "call-1"}],
    )

    folder = await db_session.get(Folder, folder_id)
    assert folder is not None, "sharer's folder row was deleted"
    assert folder.search_space_id == shared_setup["sharer_id"]

    doc = await db_session.get(Document, doc_id)
    await db_session.refresh(doc)
    assert doc is not None, "sharer's document row was deleted"
    assert doc.folder_id == folder_id, "sharer's document was orphaned from its folder"


@pytest.mark.asyncio
async def test_rm_on_linked_document_leaves_sharer_intact(db_session, shared_setup):
    doc_id = shared_setup["doc"].id

    await _commit(
        db_session,
        shared_setup["importer_id"],
        pending_deletes=[{"path": SHARED_DOC_PATH, "tool_call_id": "call-1"}],
    )

    doc = await db_session.get(Document, doc_id)
    assert doc is not None, "sharer's document row was deleted"


@pytest.mark.asyncio
async def test_move_out_of_linked_folder_does_not_reparent_sharer_doc(
    db_session, shared_setup
):
    doc_id = shared_setup["doc"].id
    folder_id = shared_setup["folder"].id

    await _commit(
        db_session,
        shared_setup["importer_id"],
        pending_moves=[
            {
                "source": SHARED_DOC_PATH,
                "dest": "/documents/Stolen.xml",
                "tool_call_id": "call-1",
            }
        ],
    )

    doc = await db_session.get(Document, doc_id)
    await db_session.refresh(doc)
    assert doc.folder_id == folder_id, "sharer's document was reparented"
    assert doc.search_space_id == shared_setup["sharer_id"], (
        "sharer's document changed hands"
    )
    assert doc.title == "Secret Notes"


@pytest.mark.asyncio
async def test_revoked_share_stops_resolving(db_session, shared_setup):
    """Revocation must take effect on the next read, not on a sweep."""
    from datetime import UTC, datetime

    importer_id = shared_setup["importer_id"]
    share = shared_setup["share"]

    share.revoked_at = datetime.now(UTC)
    await db_session.flush()

    linked_ids = await get_linked_folder_ids(db_session, importer_id)
    assert linked_ids == []

    index = await build_path_index(db_session, importer_id)
    assert SHARED_FOLDER_PATH not in index.folder_paths.values()
    assert index.occupants.get(SHARED_DOC_PATH) is None


@pytest.mark.asyncio
async def test_link_grants_descendants_not_siblings(db_session, db_user, shared_setup):
    """A link on Research covers Research/AI, but not a sibling folder."""
    importer_id = shared_setup["importer_id"]
    sharer_id = shared_setup["sharer_id"]

    child = Folder(
        name="AI",
        position="a0",
        search_space_id=sharer_id,
        parent_id=shared_setup["folder"].id,
        created_by_id=db_user.id,
    )
    sibling = Folder(
        name="Private",
        position="a1",
        search_space_id=sharer_id,
        created_by_id=db_user.id,
    )
    db_session.add_all([child, sibling])
    await db_session.flush()

    linked_ids = await get_linked_folder_ids(db_session, importer_id)
    assert child.id in linked_ids
    assert sibling.id not in linked_ids

    index = await build_path_index(db_session, importer_id)
    assert index.folder_paths[child.id] == f"{SHARED_FOLDER_PATH}/AI"


@pytest.mark.asyncio
async def test_sharer_can_still_delete_own_folder(db_session, shared_setup):
    """The guard must not lock the *sharer* out of their own content."""
    folder_id = shared_setup["folder"].id
    doc_id = shared_setup["doc"].id

    # Sharer's own view: the folder is at /documents/Research, not under _shared.
    index = await build_path_index(db_session, shared_setup["sharer_id"])
    assert index.folder_paths[folder_id] == "/documents/Research"
    assert not index.linked_folder_ids

    await _commit(
        db_session,
        shared_setup["sharer_id"],
        pending_deletes=[
            {"path": "/documents/Research/Secret Notes.xml", "tool_call_id": "call-1"}
        ],
    )

    doc = await db_session.get(Document, doc_id)
    assert doc is None, "sharer was blocked from deleting their own document"


@pytest.mark.asyncio
async def test_citation_on_linked_document_resolves(db_session, db_user, shared_setup):
    """A linked document gets retrieved and cited; the citation must not 403 on click.

    ``user_can_read_via_link`` is what GET /documents/by-chunk/{id} falls back to
    when the ordinary membership check on the *sharer's* space rejects the
    importer.
    """
    assert await user_can_read_via_link(db_session, db_user.id, shared_setup["doc"])


@pytest.mark.asyncio
async def test_citation_denied_without_link(db_session, db_user, shared_setup):
    """The fallback must not become a blanket read-anything bypass.

    A document in an *unshared sibling* folder of the same sharer: reachable by
    the same query shape, but covered by no link.
    """
    private = Folder(
        name="Private",
        position="a9",
        search_space_id=shared_setup["sharer_id"],
        created_by_id=db_user.id,
    )
    db_session.add(private)
    await db_session.flush()

    unshared = Document(
        title="Not Shared",
        document_type=DocumentType.NOTE,
        content="private",
        content_hash="hash-unshared",
        search_space_id=shared_setup["sharer_id"],
        folder_id=private.id,
    )
    db_session.add(unshared)
    await db_session.flush()

    assert not await user_can_read_via_link(db_session, db_user.id, unshared)


@pytest.mark.asyncio
async def test_documents_query_spans_owned_and_linked(db_session, shared_setup):
    """readable_documents_filter is what every read surface relies on."""
    from app.agents.chat.runtime.path_resolver import readable_documents_filter

    importer_id = shared_setup["importer_id"]

    own_doc = Document(
        title="My Note",
        document_type=DocumentType.NOTE,
        content="importer content",
        content_hash="hash-own-doc",
        search_space_id=importer_id,
    )
    db_session.add(own_doc)
    await db_session.flush()

    index = await build_path_index(db_session, importer_id)
    rows = await db_session.execute(
        select(Document.id).where(readable_documents_filter(index, importer_id))
    )
    visible = set(rows.scalars().all())

    assert own_doc.id in visible
    assert shared_setup["doc"].id in visible, "linked document not readable"
