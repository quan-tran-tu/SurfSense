"""An admin can move and clear a live share's expiry — and nothing else.

Revoked and expired are final: an ended share can't be changed (409), and an
expiry can't be set in the past (422) — ending a share now is a revoke.
"""

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


async def test_past_expiry_is_refused(db_session):
    admin, share = await _share(db_session)
    with pytest.raises(HTTPException) as err:
        await update_share_expiry(
            share.id,
            AdminShareUpdate(expires_at=datetime.now(UTC) - timedelta(minutes=1)),
            db_session,
            AuthContext.session(admin),
        )
    assert err.value.status_code == 422


async def test_expired_share_cannot_be_revived(db_session):
    admin, share = await _share(db_session)
    share.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.flush()
    with pytest.raises(HTTPException) as err:
        await update_share_expiry(
            share.id, AdminShareUpdate(expires_at=None), db_session, AuthContext.session(admin)
        )
    assert err.value.status_code == 409


async def test_revoked_share_cannot_be_changed(db_session):
    admin, share = await _share(db_session)
    share.revoked_at = datetime.now(UTC)
    await db_session.flush()
    with pytest.raises(HTTPException) as err:
        await update_share_expiry(
            share.id,
            AdminShareUpdate(expires_at=datetime.now(UTC) + timedelta(days=1)),
            db_session,
            AuthContext.session(admin),
        )
    assert err.value.status_code == 409


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


async def test_unknown_share_is_404(db_session):
    admin, _ = await _share(db_session)
    with pytest.raises(HTTPException) as err:
        await update_share_expiry(
            987654, AdminShareUpdate(expires_at=None), db_session, AuthContext.session(admin)
        )
    assert err.value.status_code == 404
