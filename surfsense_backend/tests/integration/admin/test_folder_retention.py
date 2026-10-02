"""Folders past their retention period are deleted — each kind on its own clock.

session: scoped to one chat session; group: granted to a user group; admin:
space-wide, uploaded by an admin; space: every other space-wide folder. Only
root folders count, the clock is the upload time, and an unset period keeps that
kind forever. An admin's settings (app_settings) replace the env defaults.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio


from app.config import config
from app.db import Folder, FolderGroupGrant, NewChatThread, SearchSpace, User, UserGroup
from app.services.app_settings_service import (
    clear_retention_override,
    set_retention_override,
)
from app.tasks.celery_tasks import folder_retention_task
from app.tasks.celery_tasks.folder_retention_task import expire_folders, folder_kind

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def deleted(monkeypatch):
    queued: list[int] = []

    async def _fake_dispatch(session, folder_id):
        queued.append(folder_id)
        return 0

    monkeypatch.setattr(folder_retention_task, "dispatch_folder_deletion", _fake_dispatch)
    return queued


def _retention(monkeypatch, session=None, admin=None, space=None, group=None):
    monkeypatch.setattr(config, "SESSION_FOLDER_RETENTION_DAYS", session)
    monkeypatch.setattr(config, "GROUP_FOLDER_RETENTION_DAYS", group)
    monkeypatch.setattr(config, "ADMIN_FOLDER_RETENTION_DAYS", admin)
    monkeypatch.setattr(config, "SPACE_FOLDER_RETENTION_DAYS", space)


@pytest_asyncio.fixture
async def world(db_session):
    users = {}
    for key, admin in (("user", False), ("admin", True)):
        u = User(
            id=uuid.uuid4(),
            email=f"{key}@example.com",
            hashed_password="x",
            is_active=True,
            is_superuser=admin,
            is_verified=True,
        )
        db_session.add(u)
        users[key] = u
    await db_session.flush()
    spaces = {}
    for key, u in users.items():
        spaces[key] = SearchSpace(name=f"cli:{u.email}", user_id=u.id)
        db_session.add(spaces[key])
    await db_session.flush()
    thread = NewChatThread(
        title="s", search_space_id=spaces["user"].id, created_by_id=users["user"].id
    )
    db_session.add(thread)
    await db_session.flush()

    async def folder(name, owner, age_days, *, session=False, parent=None):
        f = Folder(
            name=name,
            position="a0",
            search_space_id=spaces[owner].id,
            created_by_id=users[owner].id,
            parent_id=parent.id if parent else None,
            owner_thread_id=thread.id if session else None,
            created_at=NOW - timedelta(days=age_days),
        )
        db_session.add(f)
        await db_session.flush()
        return f

    folder.users = users
    return folder


def test_kinds():
    assert folder_kind(12, False) == "session"
    assert folder_kind(12, True) == "session"
    assert folder_kind(12, False, True) == "session"
    assert folder_kind(None, True, True) == "group"
    assert folder_kind(None, True) == "admin"
    assert folder_kind(None, False) == "space"


@pytest.mark.asyncio
async def test_each_kind_expires_on_its_own_clock(db_session, world, deleted, monkeypatch):
    _retention(monkeypatch, session=7, admin=5, space=30)
    old_session = await world("old-session", "user", 10, session=True)
    new_session = await world("new-session", "user", 2, session=True)
    old_admin = await world("old-admin", "admin", 10)
    user_space = await world("user-space", "user", 10)
    ancient_space = await world("ancient-space", "user", 40)

    assert await expire_folders(db_session, now=NOW) == 3
    assert sorted(deleted) == sorted([old_session.id, old_admin.id, ancient_space.id])
    assert new_session.id not in deleted
    assert user_space.id not in deleted


@pytest.mark.asyncio
async def test_unset_period_keeps_that_kind_forever(db_session, world, deleted, monkeypatch):
    _retention(monkeypatch, session=1)
    await world("old-space", "user", 400)
    old_session = await world("old-session", "user", 3, session=True)

    await expire_folders(db_session, now=NOW)
    assert deleted == [old_session.id]


@pytest.mark.asyncio
async def test_subfolders_go_with_their_root_not_on_their_own(
    db_session, world, deleted, monkeypatch
):
    _retention(monkeypatch, space=5)
    root = await world("fresh-root", "user", 1)
    await world("old-child", "user", 50, parent=root)

    await expire_folders(db_session, now=NOW)
    assert deleted == []


@pytest.mark.asyncio
async def test_nothing_configured_touches_nothing(db_session, world, deleted, monkeypatch):
    _retention(monkeypatch)
    await world("ancient", "user", 1000)

    assert await expire_folders(db_session, now=NOW) == 0
    assert deleted == []


@pytest.mark.asyncio
async def test_group_granted_folder_uses_the_group_period(
    db_session, world, deleted, monkeypatch
):
    _retention(monkeypatch, admin=100, group=5)
    granted = await world("granted", "admin", 10)
    kept = await world("not-granted", "admin", 10)
    group = UserGroup(name="Analysts")
    db_session.add(group)
    await db_session.flush()
    db_session.add(FolderGroupGrant(group_id=group.id, folder_id=granted.id))
    await db_session.flush()

    await expire_folders(db_session, now=NOW)
    assert deleted == [granted.id]
    assert kept.id not in deleted


@pytest.mark.asyncio
async def test_admin_settings_override_the_env(db_session, world, deleted, monkeypatch):
    _retention(monkeypatch, space=1000)
    old = await world("old-space", "user", 10)

    await set_retention_override(db_session, {"space": 5}, None)
    await expire_folders(db_session, now=NOW)
    assert deleted == [old.id]

    deleted.clear()
    await clear_retention_override(db_session)
    await expire_folders(db_session, now=NOW)
    assert deleted == []
