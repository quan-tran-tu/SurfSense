"""An admin can set, move and clear a folder share's expiry."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.auth.context import AuthContext
from app.db import Folder, SearchSpace, SharedFolder, User
from app.routes.admin_routes import (
    AdminShareUpdate,
    list_folder_shares,
    update_share_expiry,
)

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


async def test_admin_sets_and_clears_expiry(db_session):
    admin, share = await _share(db_session)
    auth = AuthContext.session(admin)
    later = datetime.now(UTC) + timedelta(days=3)

    await update_share_expiry(share.id, AdminShareUpdate(expires_at=later), db_session, auth)
    await db_session.refresh(share)
    assert share.expires_at == later

    await update_share_expiry(share.id, AdminShareUpdate(expires_at=None), db_session, auth)
    await db_session.refresh(share)
    assert share.expires_at is None


async def test_listing_names_the_shared_folder(db_session):
    admin, share = await _share(db_session)
    rows = await list_folder_shares(db_session, AuthContext.session(admin))
    row = next(r for r in rows if r.id == share.id)
    assert row.source_folder_name == "Evidence"


async def test_unknown_share_is_404(db_session):
    admin, _ = await _share(db_session)
    with pytest.raises(HTTPException) as err:
        await update_share_expiry(
            987654, AdminShareUpdate(expires_at=None), db_session, AuthContext.session(admin)
        )
    assert err.value.status_code == 404
