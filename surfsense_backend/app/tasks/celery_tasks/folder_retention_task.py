"""Celery task that deletes root folders older than their retention period.

Three periods, set by the deployment (see ``Config.*_FOLDER_RETENTION_DAYS``),
each counted from upload (``Folder.created_at``):

- session: a folder scoped to one chat session (``owner_thread_id`` set);
- admin:   a space-wide folder uploaded by a system admin;
- space:   every other space-wide folder, promoted ones included.

A session-only upload is a session folder whoever made it. "Admin" is read off
the uploader's current role, so a folder follows its uploader if they are
promoted or demoted. Deletion goes through ``dispatch_folder_deletion``, the path
the delete buttons use: documents first, then the folder rows, and with them the
shares, links and group grants that point at the folder.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.config import config
from app.db import Folder, User, async_session_maker
from app.services.folder_service import dispatch_folder_deletion

logger = logging.getLogger(__name__)


def folder_kind(owner_thread_id: int | None, uploaded_by_admin: bool) -> str:
    if owner_thread_id is not None:
        return "session"
    return "admin" if uploaded_by_admin else "space"


def retention_days() -> dict[str, int | None]:
    return {
        "session": config.SESSION_FOLDER_RETENTION_DAYS,
        "admin": config.ADMIN_FOLDER_RETENTION_DAYS,
        "space": config.SPACE_FOLDER_RETENTION_DAYS,
    }


@celery_app.task(name="expire_old_folders")
def expire_old_folders() -> int:
    return asyncio.run(_run())


async def _run() -> int:
    async with async_session_maker() as session:
        return await expire_folders(session)


async def expire_folders(session: AsyncSession, now: datetime | None = None) -> int:
    """Queue the deletion of every root folder past its retention; returns how many."""
    days = retention_days()
    if not any(days.values()):
        return 0
    now = now or datetime.now(UTC)
    # Nothing can be due before the shortest period has passed.
    oldest_due = now - timedelta(days=min(d for d in days.values() if d))

    rows = (
        await session.execute(
            select(Folder.id, Folder.name, Folder.created_at, Folder.owner_thread_id, User.is_superuser)
            .outerjoin(User, User.id == Folder.created_by_id)
            .where(Folder.parent_id.is_(None), Folder.created_at < oldest_due)
        )
    ).all()

    expired = 0
    for folder_id, name, created_at, owner_thread_id, is_admin in rows:
        kind = folder_kind(owner_thread_id, bool(is_admin))
        keep_days = days[kind]
        if not keep_days or created_at >= now - timedelta(days=keep_days):
            continue
        try:
            queued = await dispatch_folder_deletion(session, folder_id)
        except Exception:
            logger.exception("Could not queue the deletion of expired folder %s", folder_id)
            continue
        expired += 1
        logger.info(
            "Folder %s (%r, %s, uploaded %s) is past its %d-day retention; "
            "deleting it and %d document(s).",
            folder_id, name, kind, created_at.date(), keep_days, queued,
        )
    return expired
