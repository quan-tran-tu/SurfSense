"""Folders an admin grants to a user group are read by every member — read-only.

Pins the third grant shape at the one definition every read surface goes through
(``linked_folder_ids_subquery``) and at the two places it fans out to: the
agent's path index, where landing under ``/documents/_shared/`` is what makes the
write tools refuse it, and the citation fallback.

The invariant the whole feature rests on is
:func:`test_group_members_do_not_see_each_others_folders`: membership by itself
carries nothing. Only what an admin explicitly granted to the group travels.

``db_user`` / ``db_search_space`` play the first ordinary user throughout.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.runtime.path_resolver import build_path_index, is_shared_path
from app.db import (
    Document,
    DocumentType,
    Folder,
    FolderGroupGrant,
    NewChatThread,
    SearchSpace,
    User,
    UserGroup,
    UserGroupMembership,
)
from app.routes.search_spaces_routes import create_default_roles_and_membership
from app.services.folder_sharing_service import (
    get_group_granted_roots,
    get_linked_folder_ids,
    live_link_fingerprint,
    user_can_read_via_link,
)


async def _add_user(
    db_session: AsyncSession, email: str, *, admin: bool = False
) -> User:
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


async def _add_group(db_session: AsyncSession, name: str, *users: User) -> UserGroup:
    group = UserGroup(name=name)
    db_session.add(group)
    await db_session.flush()
    for user in users:
        db_session.add(UserGroupMembership(group_id=group.id, user_id=user.id))
    await db_session.flush()
    return group


async def _grant(db_session: AsyncSession, group: UserGroup, folder: Folder) -> None:
    db_session.add(FolderGroupGrant(group_id=group.id, folder_id=folder.id))
    await db_session.flush()


@pytest_asyncio.fixture
async def world(db_session: AsyncSession, db_user: User, db_search_space):
    """An admin with a granted ``General/Policies``, and two users in one group.

    ``db_user`` is the first member; ``member`` is the second. Each also has a
    personal folder of their own that nobody granted to anything.
    """
    admin = await _add_user(db_session, "admin@surfsense.net", admin=True)
    admin_space = await _add_space(db_session, admin)
    general = await _add_folder(db_session, admin_space, "General")
    policies = await _add_folder(db_session, admin_space, "Policies", parent=general)
    doc = Document(
        title="Handbook",
        document_type=DocumentType.NOTE,
        content="general content",
        content_hash="hash-group-grant-doc",
        search_space_id=admin_space.id,
        folder_id=policies.id,
        created_by_id=admin.id,
    )
    db_session.add(doc)

    member = await _add_user(db_session, "member@surfsense.net")
    member_space = await _add_space(db_session, member)

    mine = await _add_folder(db_session, db_search_space, "My Uploads")
    theirs = await _add_folder(db_session, member_space, "Their Uploads")

    group = await _add_group(db_session, "Analysts", db_user, member)
    await _grant(db_session, group, general)
    await db_session.flush()

    return {
        "admin": admin,
        "admin_space": admin_space,
        "general": general,
        "policies": policies,
        "doc": doc,
        "group": group,
        "member": member,
        "member_space": member_space,
        "mine": mine,
        "theirs": theirs,
    }


@pytest.mark.asyncio
async def test_members_read_the_granted_subtree(db_session, world, db_search_space):
    """Both members reach the granted folder and everything under it."""
    for space_id in (db_search_space.id, world["member_space"].id):
        ids = await get_linked_folder_ids(db_session, space_id)
        assert world["general"].id in ids
        assert world["policies"].id in ids


@pytest.mark.asyncio
async def test_group_members_do_not_see_each_others_folders(
    db_session, world, db_search_space
):
    """The invariant: membership carries the grant, never the other members' uploads."""
    mine = await get_linked_folder_ids(db_session, db_search_space.id)
    theirs = await get_linked_folder_ids(db_session, world["member_space"].id)
    assert world["theirs"].id not in mine
    assert world["mine"].id not in theirs


@pytest.mark.asyncio
async def test_a_non_member_reads_nothing(db_session, world):
    outsider = await _add_user(db_session, "outsider@surfsense.net")
    outsider_space = await _add_space(db_session, outsider)
    assert await get_linked_folder_ids(db_session, outsider_space.id) == []


@pytest.mark.asyncio
async def test_removing_the_member_ends_access(db_session, world, db_search_space):
    """Membership is read per query, so access ends on the next question."""
    from sqlalchemy import delete

    await db_session.execute(
        delete(UserGroupMembership).where(
            UserGroupMembership.group_id == world["group"].id,
            UserGroupMembership.user_id == world["member"].id,
        )
    )
    await db_session.flush()
    assert await get_linked_folder_ids(db_session, world["member_space"].id) == []
    # The other member is untouched.
    assert world["general"].id in await get_linked_folder_ids(
        db_session, db_search_space.id
    )


@pytest.mark.asyncio
async def test_revoking_the_grant_ends_access(db_session, world, db_search_space):
    from sqlalchemy import delete

    await db_session.execute(
        delete(FolderGroupGrant).where(FolderGroupGrant.group_id == world["group"].id)
    )
    await db_session.flush()
    assert await get_linked_folder_ids(db_session, db_search_space.id) == []


@pytest.mark.asyncio
async def test_the_granting_admin_still_owns_its_own_folder(db_session, world):
    """A folder the granter owns must not come back as foreign to the granter.

    ``linked_folder_ids_subquery`` is what the write guard reads as "belongs to
    another space, refuse the write", so an admin who joins the group it granted
    ``General`` to must still be able to edit ``General``.
    """
    db_session.add(
        UserGroupMembership(group_id=world["group"].id, user_id=world["admin"].id)
    )
    await db_session.flush()
    ids = await get_linked_folder_ids(db_session, world["admin_space"].id)
    assert world["general"].id not in ids
    assert world["policies"].id not in ids


@pytest.mark.asyncio
async def test_session_scoped_grant_stays_hidden(db_session, world, db_search_space):
    """A folder private to one chat is not widened by a grant made against it."""
    thread = NewChatThread(
        title="private",
        search_space_id=world["member_space"].id,
        created_by_id=world["member"].id,
    )
    db_session.add(thread)
    await db_session.flush()
    scoped = await _add_folder(
        db_session, world["member_space"], "Scratch", owner_thread_id=thread.id
    )
    await _grant(db_session, world["group"], scoped)
    assert scoped.id not in await get_linked_folder_ids(db_session, db_search_space.id)


@pytest.mark.asyncio
async def test_granted_folders_mount_read_only_under_the_group_name(
    db_session, world, db_search_space
):
    index = await build_path_index(db_session, db_search_space.id)
    root_path = index.folder_paths[world["general"].id]
    assert root_path == "/documents/_shared/Analysts/General"
    assert index.folder_paths[world["policies"].id] == f"{root_path}/Policies"
    # The write tools refuse anything under this prefix.
    assert is_shared_path(root_path)
    assert world["general"].id in index.linked_folder_ids


@pytest.mark.asyncio
async def test_a_member_can_open_a_granted_documents_citation(
    db_session, world, db_user
):
    assert await user_can_read_via_link(db_session, db_user.id, world["doc"])


@pytest.mark.asyncio
async def test_group_roots_carry_their_group_name(db_session, world, db_search_space):
    roots = await get_group_granted_roots(db_session, db_search_space.id)
    assert [(f.id, name) for f, name in roots] == [(world["general"].id, "Analysts")]


@pytest.mark.asyncio
async def test_the_fingerprint_moves_when_a_grant_appears(
    db_session, world, db_search_space
):
    """The workspace-tree cache key has to notice a grant; nothing bumps
    ``tree_version`` when an admin makes one."""
    before = await live_link_fingerprint(db_session, db_search_space.id)
    await _grant(db_session, world["group"], world["theirs"])
    after = await live_link_fingerprint(db_session, db_search_space.id)
    assert before != after
