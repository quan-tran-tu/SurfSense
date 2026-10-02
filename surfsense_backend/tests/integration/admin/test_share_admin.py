"""An admin lists shares and revokes them — and can't change their expiry.

The expiry is set once, by whoever mints the token. An admin revoke ends the
share and deletes every link to it, so the folder leaves each importer's
Imported list.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.auth.context import AuthContext
from app.db import Folder, FolderLink, SearchSpace, SharedFolder, User
from app.routes.admin_routes import list_folder_shares, revoke_share

pytestmark = pytest.mark.asyncio


async def _share(db_session) -> tuple[User, SharedFolder]:
    owner = User(
        id=uuid.uuid4(),
        email="owner@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    admin = User(
        id=uuid.uuid4(),
        email="admin@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    db_session.add_all([owner, admin])
    await db_session.flush()
    space = SearchSpace(name="cli:owner@example.com", user_id=owner.id)
    db_session.add(space)
    await db_session.flush()
    folder = Folder(name="Evidence", position="a0", search_space_id=space.id)
    db_session.add(folder)
    await db_session.flush()
    share = SharedFolder(
        token=uuid.uuid4().hex,
        source_folder_id=folder.id,
        source_search_space_id=space.id,
        created_by_id=owner.id,
    )
    db_session.add(share)
    await db_session.flush()
    return admin, share


async def test_listing_reports_state(db_session):
    admin, share = await _share(db_session)
    share.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.flush()
    rows = await list_folder_shares(db_session, AuthContext.session(admin))
    assert next(r for r in rows if r.id == share.id).state == "expired"


async def test_listing_names_the_shared_folder(db_session):
    admin, share = await _share(db_session)
    rows = await list_folder_shares(db_session, AuthContext.session(admin))
    row = next(r for r in rows if r.id == share.id)
    assert row.source_folder_name == "Evidence"


async def test_revoke_removes_every_import(db_session):
    admin, share = await _share(db_session)
    importer = User(
        id=uuid.uuid4(),
        email="importer@example.com",
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    db_session.add(importer)
    await db_session.flush()
    target = SearchSpace(name="cli:importer@example.com", user_id=importer.id)
    db_session.add(target)
    await db_session.flush()
    db_session.add(
        FolderLink(
            share_id=share.id,
            source_folder_id=share.source_folder_id,
            target_search_space_id=target.id,
            created_by_id=importer.id,
        )
    )
    await db_session.flush()

    r = await revoke_share(share.id, db_session, AuthContext.session(admin))
    assert r["links_removed"] == 1
    await db_session.refresh(share)
    assert share.revoked_at is not None
    left = await db_session.scalar(
        select(func.count(FolderLink.id)).where(FolderLink.share_id == share.id)
    )
    assert left == 0


async def test_unknown_share_is_404(db_session):
    admin, _ = await _share(db_session)
    with pytest.raises(HTTPException) as err:
        await revoke_share(987654, db_session, AuthContext.session(admin))
    assert err.value.status_code == 404
