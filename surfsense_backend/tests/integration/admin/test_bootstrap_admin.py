"""The first admin comes from the deployment, never from whoever registers first.

``app.bootstrap_admin`` runs on every deploy (the migrate step). It must be a
no-op once an admin exists, create the configured account when there is none,
and promote an existing account only for the holder of its password — the
promote-on-login ``ADMIN_EMAILS`` it replaced handed admin to whoever
registered the configured address first.
"""

import uuid

import pytest
from fastapi_users.password import PasswordHelper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bootstrap_admin import bootstrap
from app.db import User
from app.users import UserManager

pytestmark = pytest.mark.asyncio

EMAIL = "boss@example.com"
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _no_default_space(monkeypatch):
    # on_after_register seeds a search space through its own connection, which
    # would wait on the test's uncommitted user row. Not what is under test.
    async def _noop(self, user, request=None):
        return None

    monkeypatch.setattr(UserManager, "on_after_register", _noop)


async def _user(
    db_session: AsyncSession,
    email: str,
    *,
    password: str = "whatever-else",
    admin: bool = False,
    active: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=PasswordHelper().hash(password),
        is_active=active,
        is_superuser=admin,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _by_email(db_session: AsyncSession, email: str) -> User | None:
    return await db_session.scalar(select(User).where(User.email == email))


async def test_creates_the_configured_admin_when_there_is_none(db_session):
    assert await bootstrap(db_session, EMAIL, PASSWORD) == 0

    user = await _by_email(db_session, EMAIL)
    assert user is not None
    assert user.is_superuser and user.is_active and user.is_verified
    valid, _ = PasswordHelper().verify_and_update(PASSWORD, user.hashed_password)
    assert valid


async def test_does_nothing_once_an_active_admin_exists(db_session):
    await _user(db_session, "existing-admin@example.com", admin=True)

    assert await bootstrap(db_session, EMAIL, PASSWORD) == 0
    assert await _by_email(db_session, EMAIL) is None


async def test_an_inactive_admin_does_not_count(db_session):
    await _user(db_session, "gone@example.com", admin=True, active=False)

    assert await bootstrap(db_session, EMAIL, PASSWORD) == 0
    assert (await _by_email(db_session, EMAIL)).is_superuser


async def test_promotes_an_existing_account_given_its_password(db_session):
    user = await _user(db_session, EMAIL, password=PASSWORD)

    assert await bootstrap(db_session, EMAIL, PASSWORD) == 0
    await db_session.refresh(user)
    assert user.is_superuser


async def test_never_promotes_an_account_registered_by_someone_else(db_session):
    squatter = await _user(db_session, EMAIL, password="the-squatters-password")

    assert await bootstrap(db_session, EMAIL, PASSWORD) == 1
    await db_session.refresh(squatter)
    assert not squatter.is_superuser


async def test_unset_configuration_is_not_an_error(db_session):
    assert await bootstrap(db_session, "", "") == 0
