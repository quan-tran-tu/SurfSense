"""Session-scoped folders: visibility, resolution, write protection, promotion.

A folder with ``owner_thread_id`` set is visible only inside that chat session.
The load-bearing invariants:

* another session's folders vanish from the path index (tree/ls/glob/grep all
  render from it), and their documents from ``readable_documents_filter``;
* ``virtual_path_to_doc`` with a ``thread_id`` cannot reach them, so the
  commit path's ``rm``/``rmdir`` fail closed;
* two sessions may own same-named roots whose documents coexist because the
  owning thread is mixed into ``unique_identifier_hash``;
* promotion clears the stamp on the whole subtree and rewrites those hashes to
  the space-wide form, refusing (409) when that would collide.
"""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.multi_agent_chat.main_agent.middleware.kb_persistence.middleware import (
    commit_staged_filesystem_state,
)
from app.agents.chat.runtime.path_resolver import (
    build_path_index,
    note_path_identifier,
    readable_documents_filter,
    virtual_path_to_doc,
)
from app.db import (
    Document,
    DocumentType,
    Folder,
    NewChatThread,
    SearchSpace,
    User,
)
from app.indexing_pipeline.document_hashing import (
    compute_identifier_hash,
    local_file_unique_id,
)
from app.services.folder_scope_service import promote_folder_to_space
from app.utils.document_converters import generate_unique_identifier_hash

DOC_PATH = "/documents/Research/Notes.xml"


def _note_hash(path: str, space_id: int, thread_id: int | None) -> str:
    return generate_unique_identifier_hash(
        DocumentType.NOTE, note_path_identifier(path, thread_id), space_id
    )


@pytest_asyncio.fixture
async def two_sessions(
    db_session: AsyncSession, db_user: User, db_search_space: SearchSpace
):
    """Two chat sessions, each owning a root folder named ``Research`` with one
    NOTE inside, carrying the session-scoped identity hash the write path mints."""
    threads = []
    for title in ("session A", "session B"):
        thread = NewChatThread(
            title=title, search_space_id=db_search_space.id, created_by_id=db_user.id
        )
        db_session.add(thread)
        threads.append(thread)
    await db_session.flush()

    folders, docs = [], []
    for thread in threads:
        folder = Folder(
            name="Research",
            position="a0",
            search_space_id=db_search_space.id,
            created_by_id=db_user.id,
            owner_thread_id=thread.id,
        )
        db_session.add(folder)
        await db_session.flush()
        doc = Document(
            title="Notes",
            document_type=DocumentType.NOTE,
            content=f"content of {thread.title}",
            content_hash=f"hash-{thread.id}",
            unique_identifier_hash=_note_hash(DOC_PATH, db_search_space.id, thread.id),
            document_metadata={"virtual_path": DOC_PATH},
            search_space_id=db_search_space.id,
            folder_id=folder.id,
            created_by_id=db_user.id,
        )
        db_session.add(doc)
        folders.append(folder)
        docs.append(doc)
    await db_session.flush()

    return {
        "space_id": db_search_space.id,
        "threads": threads,
        "folders": folders,
        "docs": docs,
    }


async def _commit(db_session: AsyncSession, space_id: int, thread_id: int, **staged):
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
        search_space_id=space_id,
        created_by_id=None,
        thread_id=thread_id,
        dispatch_events=False,
    )


# ---------------------------------------------------------------- visibility


@pytest.mark.asyncio
async def test_index_shows_only_own_session_folder(db_session, two_sessions):
    space_id = two_sessions["space_id"]
    thread_a, thread_b = two_sessions["threads"]
    folder_a, folder_b = two_sessions["folders"]
    doc_a, doc_b = two_sessions["docs"]

    index = await build_path_index(db_session, space_id, thread_id=thread_a.id)
    assert folder_a.id in index.folder_paths
    assert folder_b.id not in index.folder_paths
    assert index.hidden_folder_ids == {folder_b.id}
    # Same virtual path, but occupied by A's own document — not B's.
    assert index.occupants.get(DOC_PATH) == doc_a.id

    readable = (
        (
            await db_session.execute(
                select(Document.id).where(readable_documents_filter(index, space_id))
            )
        )
        .scalars()
        .all()
    )
    assert doc_a.id in readable
    assert doc_b.id not in readable


@pytest.mark.asyncio
async def test_unscoped_index_sees_everything(db_session, two_sessions):
    """No thread context (REST, exports, admin) keeps the historical wide view."""
    space_id = two_sessions["space_id"]
    folder_a, folder_b = two_sessions["folders"]

    index = await build_path_index(db_session, space_id)
    assert folder_a.id in index.folder_paths
    assert folder_b.id in index.folder_paths
    assert index.hidden_folder_ids == set()


@pytest.mark.asyncio
async def test_subfolder_of_foreign_session_is_hidden_too(db_session, two_sessions):
    """Hiding is by ancestor closure, so even a mis-stamped child fails closed."""
    space_id = two_sessions["space_id"]
    thread_a, _ = two_sessions["threads"]
    _, folder_b = two_sessions["folders"]

    child = Folder(
        name="Sub",
        position="a0",
        search_space_id=space_id,
        parent_id=folder_b.id,
        owner_thread_id=None,  # broken invariant on purpose
    )
    db_session.add(child)
    await db_session.flush()

    index = await build_path_index(db_session, space_id, thread_id=thread_a.id)
    assert child.id in index.hidden_folder_ids
    assert child.id not in index.folder_paths


# ---------------------------------------------------------------- resolution


@pytest.mark.asyncio
async def test_virtual_path_resolves_to_own_sessions_document(db_session, two_sessions):
    space_id = two_sessions["space_id"]
    thread_a, thread_b = two_sessions["threads"]
    doc_a, doc_b = two_sessions["docs"]

    resolved_a = await virtual_path_to_doc(
        db_session, search_space_id=space_id, virtual_path=DOC_PATH,
        thread_id=thread_a.id,
    )
    resolved_b = await virtual_path_to_doc(
        db_session, search_space_id=space_id, virtual_path=DOC_PATH,
        thread_id=thread_b.id,
    )
    assert resolved_a is not None and resolved_a.id == doc_a.id
    assert resolved_b is not None and resolved_b.id == doc_b.id


@pytest.mark.asyncio
async def test_id_suffix_lookup_cannot_cross_sessions(db_session, two_sessions):
    """The `` (<doc_id>).xml`` escape hatch must not reach another session's doc."""
    space_id = two_sessions["space_id"]
    thread_a, _ = two_sessions["threads"]
    _, doc_b = two_sessions["docs"]

    resolved = await virtual_path_to_doc(
        db_session,
        search_space_id=space_id,
        virtual_path=f"/documents/Research/Notes ({doc_b.id}).xml",
        thread_id=thread_a.id,
    )
    assert resolved is None or resolved.id != doc_b.id


# ---------------------------------------------------------- write protection


@pytest.mark.asyncio
async def test_rm_from_other_session_leaves_document_intact(db_session, two_sessions):
    space_id = two_sessions["space_id"]
    thread_a, thread_b = two_sessions["threads"]
    doc_a, doc_b = two_sessions["docs"]

    # Session B stages an rm of the shared path; it must resolve to B's own
    # document, never A's.
    await _commit(
        db_session,
        space_id,
        thread_b.id,
        pending_deletes=[{"path": DOC_PATH, "tool_call_id": "call-1"}],
    )

    assert await db_session.get(Document, doc_a.id) is not None
    assert await db_session.get(Document, doc_b.id) is None


@pytest.mark.asyncio
async def test_rmdir_from_other_session_leaves_folder_intact(db_session, two_sessions):
    space_id = two_sessions["space_id"]
    thread_a, thread_b = two_sessions["threads"]
    folder_a, folder_b = two_sessions["folders"]
    doc_b = two_sessions["docs"][1]

    # Empty B's folder first so ITS rmdir would succeed — proving the folder
    # that survives, survives because of scoping, not because it was non-empty.
    await db_session.delete(doc_b)
    await db_session.flush()

    await _commit(
        db_session,
        space_id,
        thread_b.id,
        pending_dir_deletes=[{"path": "/documents/Research", "tool_call_id": "c1"}],
    )

    assert await db_session.get(Folder, folder_a.id) is not None
    assert await db_session.get(Folder, folder_b.id) is None


# ------------------------------------------------------------------ promote


@pytest.mark.asyncio
async def test_promote_clears_stamp_and_rehashes(db_session, two_sessions):
    space_id = two_sessions["space_id"]
    thread_a, thread_b = two_sessions["threads"]
    folder_a, _ = two_sessions["folders"]
    doc_a, _ = two_sessions["docs"]

    result = await promote_folder_to_space(db_session, folder_a)
    assert result["folders_promoted"] == 1
    assert result["documents_rehashed"] == 1

    await db_session.refresh(folder_a)
    await db_session.refresh(doc_a)
    assert folder_a.owner_thread_id is None
    assert doc_a.unique_identifier_hash == _note_hash(DOC_PATH, space_id, None)

    # Session B now sees A's promoted folder alongside its own.
    index = await build_path_index(db_session, space_id, thread_id=thread_b.id)
    assert folder_a.id in index.folder_paths


@pytest.mark.asyncio
async def test_promote_conflicting_document_is_409(db_session, two_sessions):
    space_id = two_sessions["space_id"]
    folder_a, _ = two_sessions["folders"]

    blocker = Document(
        title="Notes",
        document_type=DocumentType.NOTE,
        content="pre-existing space-wide twin",
        content_hash="hash-blocker",
        unique_identifier_hash=_note_hash(DOC_PATH, space_id, None),
        search_space_id=space_id,
    )
    db_session.add(blocker)
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await promote_folder_to_space(db_session, folder_a)
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_promote_conflicting_sibling_folder_is_409(db_session, two_sessions):
    space_id = two_sessions["space_id"]
    folder_a, _ = two_sessions["folders"]

    db_session.add(
        Folder(
            name="Research",
            position="a1",
            search_space_id=space_id,
            owner_thread_id=None,
        )
    )
    await db_session.flush()

    with pytest.raises(HTTPException) as excinfo:
        await promote_folder_to_space(db_session, folder_a)
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_promote_space_wide_folder_is_400(db_session, two_sessions):
    folder_a, _ = two_sessions["folders"]
    await promote_folder_to_space(db_session, folder_a)
    with pytest.raises(HTTPException) as excinfo:
        await promote_folder_to_space(db_session, folder_a)
    assert excinfo.value.status_code == 400


# ---------------------------------------------------------------- upload ids


def test_local_file_unique_id_is_scoped():
    base = local_file_unique_id("Research", "sub/a.pdf", None)
    scoped = local_file_unique_id("Research", "sub/a.pdf", 7)
    assert base == "Research:sub/a.pdf"
    assert scoped == "thread:7:Research:sub/a.pdf"
    assert compute_identifier_hash(
        DocumentType.LOCAL_FOLDER_FILE.value, base, 1
    ) != compute_identifier_hash(DocumentType.LOCAL_FOLDER_FILE.value, scoped, 1)
