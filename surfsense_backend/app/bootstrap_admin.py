"""Seed the first system admin from the deployment's configuration.

    python -m app.bootstrap_admin

Runs in the migrate step of scripts/docker/entrypoint.sh, right after alembic, so
every deploy applies it and nobody has to exec into a pod. It reads
``ADMIN_EMAIL`` and ``ADMIN_PASSWORD`` (from the Secret on k8s) and does nothing
once any active admin exists: from then on admins are managed on the admin page.
That makes it safe to leave configured and to run on every deploy.

With no admin yet:

- the email has no account: it is created, verified, as an admin, with that
  password. The deployment makes the account, so nobody can claim the address
  first by registering it.
- the email already has an account: it is promoted only when ``ADMIN_PASSWORD``
  is that account's password. Otherwise whoever registered the address first
  would be handed admin — the hole the old promote-on-login ``ADMIN_EMAILS`` had.

Exit status: 1 only when the configuration is set but cannot be applied (the
entrypoint logs it and carries on); 0 otherwise, unset configuration included.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from fastapi_users.exceptions import InvalidPasswordException
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import User, async_session_maker
from app.schemas.users import UserCreate
from app.users import UserManager

logger = logging.getLogger("bootstrap_admin")


async def bootstrap(session: AsyncSession, email: str, password: str) -> int:
    """Apply ADMIN_EMAIL / ADMIN_PASSWORD; returns the exit status (see above)."""
    admins = await session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_superuser.is_(True), User.is_active.is_(True))
    )
    if admins:
        logger.info("An active admin exists (%d); nothing to do.", admins)
        return 0

    if not email or not password:
        logger.warning(
            "No active admin, and ADMIN_EMAIL / ADMIN_PASSWORD are not both set. "
            "Set them to seed the first admin."
        )
        return 0

    manager = UserManager(SQLAlchemyUserDatabase(session, User))
    user = await session.scalar(select(User).where(func.lower(User.email) == email.lower()))

    if user is None:
        try:
            user = await manager.create(
                UserCreate(email=email, password=password, is_superuser=True, is_verified=True),
                safe=False,
            )
        except InvalidPasswordException as err:
            logger.error("ADMIN_PASSWORD was rejected: %s", err.reason)
            return 1
        logger.info("Created the first admin, %s.", user.email)
        return 0

    valid, _ = manager.password_helper.verify_and_update(password, user.hashed_password)
    if not valid:
        logger.error(
            "%s is already registered and ADMIN_PASSWORD is not its password, so it "
            "was NOT made an admin. Use that account's password, or another email.",
            user.email,
        )
        return 1

    user.is_superuser = True
    user.is_active = True
    user.is_verified = True
    await session.commit()
    logger.info("Promoted the existing account %s to admin.", user.email)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="bootstrap_admin: %(message)s")
    email = os.getenv("ADMIN_EMAIL", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    return asyncio.run(_run(email, password))


async def _run(email: str, password: str) -> int:
    async with async_session_maker() as session:
        return await bootstrap(session, email, password)


if __name__ == "__main__":
    sys.exit(main())
