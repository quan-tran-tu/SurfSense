"""A system admin reads every non-admin user's folders — only those, and read-only.

The grant is folded into ``linked_folder_ids_subquery``, so it is pinned here at
that one definition and at the two places it fans out to: the agent's path index
(where landing under ``/documents/_shared/`` is what makes the write tools refuse
it) and the citation fallback.

``db_user`` / ``db_search_space`` play the ordinary user throughout.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.runtime.path_resolver import build_path_index, is_shared_path
from app.db import Document, DocumentType, Folder, NewChatThread, SearchSpace, User
from app.routes.search_spaces_routes import create_default_roles_and_membership
from app.services.folder_sharing_service import (
    get_linked_folder_ids,
    user_can_read_via_link,
)


async def _add_user(db_session: AsyncSession, email: str, *, admin: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="hashed",
        is_active=True,
        is_superuser=admin,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _add_space(db_session: AsyncSession, user: User) -> SearchSpace:
    space = SearchSpace(name=f"cli:{user.email}", user_id=user.id)
    db_session.add(space)
    await db_session.flush()
    await create_default_roles_and_membership(db_session, space.id, user.id)
    await db_session.flush()
    return space


async def _add_folder(
    db_session: AsyncSession,
    space: SearchSpace,
    name: str,
    *,
    parent: Folder | None = None,
    owner_thread_id: int | None = None,
) -> Folder:
    folder = Folder(
        name=name,
        position="a0",
        search_space_id=space.id,
        parent_id=parent.id if parent else None,
        owner_thread_id=owner_thread_id,
    )
    db_session.add(folder)
    await db_session.flush()
    return folder


@pytest_asyncio.fixture
async def admin_space(db_session: AsyncSession) -> SearchSpace:
    admin = await _add_user(db_session, "admin@surfsense.net", admin=True)
    return await _add_space(db_session, admin)


@pytest_asyncio.fixture
async def user_tree(db_session: AsyncSession, db_user: User, db_search_space):
    """The ordinary user's Research/AI tree with one document in AI."""
    root = await _add_folder(db_session, db_search_space, "Research")
    child = await _add_folder(db_session, db_search_space, "AI", parent=root)
    doc = Document(
        title="Notes",
        document_type=DocumentType.NOTE,
        content="user content",
        content_hash="hash-admin-grant-doc",
        search_space_id=db_search_space.id,
        folder_id=child.id,
        created_by_id=db_user.id,
    )
    db_session.add(doc)
    await db_session.flush()
    return {"root": root, "child": child, "doc": doc}


@pytest.mark.asyncio
async def test_admin_reads_user_folder_subtree(db_session, admin_space, user_tree):
    ids = await get_linked_folder_ids(db_session, admin_space.id)
    assert user_tree["root"].id in ids
    assert user_tree["child"].id in ids


@pytest.mark.asyncio
async def test_user_does_not_read_admin_folders(
    db_session, admin_space, db_search_space, user_tree
):
    """The grant runs one way: a higher role reads a lower one, never back."""
    admin_folder = await _add_folder(db_session, admin_space, "Admin Only")
    ids = await get_linked_folder_ids(db_session, db_search_space.id)
    assert admin_folder.id not in ids


@pytest.mark.asyncio
async def test_admin_does_not_read_another_admins_folders(db_session, admin_space):
    other = await _add_user(db_session, "other-admin@surfsense.net", admin=True)
    other_space = await _add_space(db_session, other)
    folder = await _add_folder(db_session, other_space, "Theirs")
    assert folder.id not in await get_linked_folder_ids(db_session, admin_space.id)


@pytest.mark.asyncio
async def test_session_scoped_user_folder_stays_hidden(
    db_session, db_user, db_search_space, admin_space
):
    """A folder private to one of the user's chats is not wider for an admin."""
    thread = NewChatThread(
        title="private", search_space_id=db_search_space.id, created_by_id=db_user.id
    )
    db_session.add(thread)
    await db_session.flush()
    scoped = await _add_folder(
        db_session, db_search_space, "Scratch", owner_thread_id=thread.id
    )
    assert scoped.id not in await get_linked_folder_ids(db_session, admin_space.id)


@pytest.mark.asyncio
async def test_demoting_the_admin_ends_the_grant(db_session, admin_space, user_tree):
    admin = await db_session.get(User, admin_space.user_id)
    admin.is_superuser = False
    await db_session.flush()
    assert user_tree["root"].id not in await get_linked_folder_ids(
        db_session, admin_space.id
    )


@pytest.mark.asyncio
async def test_user_folders_mount_read_only_under_owner_email(
    db_session, admin_space, user_tree
):
    index = await build_path_index(db_session, admin_space.id)
    root_path = index.folder_paths[user_tree["root"].id]
    assert root_path == "/documents/_shared/test@surfsense.net/Research"
    assert index.folder_paths[user_tree["child"].id] == f"{root_path}/AI"
    # The write tools refuse anything under this prefix.
    assert is_shared_path(root_path)
    assert user_tree["root"].id in index.linked_folder_ids


@pytest.mark.asyncio
async def test_admin_can_open_a_user_documents_citation(
    db_session, admin_space, user_tree
):
    admin = await db_session.get(User, admin_space.user_id)
    assert await user_can_read_via_link(db_session, admin.id, user_tree["doc"])
